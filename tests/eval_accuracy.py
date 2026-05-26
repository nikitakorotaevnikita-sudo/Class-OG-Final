"""
Скрипт оценки точности классификации.
Запуск: python tests/eval_accuracy.py [--fixtures tests/fixtures/test_appeals.json]

Вывод: таблица с результатами по каждому тесту + итоговые метрики.
JSON-отчёт дополнительно содержит stage-level диагностику retrieval.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import ClassifierAgent
from eval_metrics_helpers import score_eval_case, summarize_eval_results


def load_fixtures(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def code_prefix(code: str, level: int) -> str:
    """Returns first N dot-separated code parts."""
    return ".".join((code or "").split(".")[:level])


def any_expected_in_codes(candidate_codes: list[str], expected_codes: list[str], top_k: int | None = None) -> bool | None:
    if not expected_codes:
        return None
    scope = candidate_codes[:top_k] if top_k is not None else candidate_codes
    return any(code in expected_codes for code in scope)


def first_expected_rank(candidate_codes: list[str], expected_codes: list[str]) -> int | None:
    expected = set(expected_codes or [])
    for idx, code in enumerate(candidate_codes, start=1):
        if code in expected:
            return idx
    return None


def prefix_match(predicted_code: str | None, expected_codes: list[str], level: int) -> bool | None:
    if not predicted_code or not expected_codes:
        return None
    predicted_prefix = code_prefix(predicted_code, level)
    return any(predicted_prefix == code_prefix(expected, level) for expected in expected_codes)


def run_dense_retrieval_debug(agent: ClassifierAgent, appeal_text: str, expected_codes: list[str], top_k: int) -> dict:
    start = time.time()
    dense_candidates = agent._search_candidates(appeal_text, top_k=top_k)
    lexical_candidates = agent._search_lexical_candidates(appeal_text, top_k=30)
    candidates = agent._merge_candidate_pools(dense_candidates, lexical_candidates)
    reranked_candidates = agent._rerank_candidates(appeal_text, candidates, top_k=10)
    elapsed = time.time() - start
    dense_codes = [c["code"] for c in dense_candidates]
    lexical_codes = [c["code"] for c in lexical_candidates]
    codes = [c["code"] for c in candidates]
    reranked_codes = [c["code"] for c in reranked_candidates]
    return {
        "elapsed": round(elapsed, 3),
        "top_k": top_k,
        "dense_candidates": dense_candidates,
        "dense_candidate_codes": dense_codes,
        "lexical_candidates": lexical_candidates,
        "lexical_candidate_codes": lexical_codes,
        "candidates": candidates,
        "candidate_codes": codes,
        "reranked_candidates": reranked_candidates,
        "reranked_candidate_codes": reranked_codes,
        "first_expected_rank": first_expected_rank(dense_codes, expected_codes),
        "first_expected_rank_reranked": first_expected_rank(reranked_codes, expected_codes),
        "recall_at_10": any_expected_in_codes(dense_codes, expected_codes, top_k=10),
        "recall_at_50": any_expected_in_codes(dense_codes, expected_codes, top_k=min(50, top_k)),
        "reranked_recall_at_10": any_expected_in_codes(reranked_codes, expected_codes, top_k=10),
    }


def run_eval(fixtures: list[dict], agent: ClassifierAgent, retrieval_top_k: int = 50) -> dict:
    results = []

    for fix in fixtures:
        appeal_text = fix["text"]
        expected_vid = fix.get("expected_vid")
        expected_codes = fix.get("expected_codes", [])
        expected_prefix = fix.get("expected_prefix", "")

        total_start = time.time()
        dense_debug = run_dense_retrieval_debug(agent, appeal_text, expected_codes, retrieval_top_k)

        classify_start = time.time()
        try:
            result = agent.classify(appeal_text)
        except Exception as e:
            results.append({
                "id": fix["id"],
                "title": fix["title"],
                "status": "ERROR",
                "error": str(e),
                "elapsed": None,
                "stage_timings": {
                    "segmentation": None,
                    "dense_retrieval": dense_debug["elapsed"],
                    "rerank": None,
                    "classification": round(time.time() - classify_start, 3),
                    "total": round(time.time() - total_start, 3),
                },
                "debug": {
                    "expected_codes": expected_codes,
                    "segments": [appeal_text],
                    "dense_candidates": dense_debug["dense_candidate_codes"],
                    "lexical_candidates": dense_debug["lexical_candidate_codes"],
                    "reranked_candidates": dense_debug["reranked_candidate_codes"],
                    "llm_codes": [],
                    "first_expected_rank_dense": dense_debug["first_expected_rank"],
                    "first_expected_rank_reranked": dense_debug["first_expected_rank_reranked"],
                },
            })
            continue

        classify_elapsed = time.time() - classify_start
        elapsed = time.time() - total_start
        questions = result.questions
        agent_codes = [q.code for q in questions]
        agent_vid = result.vid_obrascheniya

        # Вид обращения: точное совпадение
        vid_correct = (agent_vid == expected_vid) if expected_vid else None

        # Top-N accuracy here means final LLM-selected question codes, not retrieval top-N.
        top1_code = agent_codes[0] if agent_codes else None
        top1_correct = any_expected_in_codes(agent_codes, expected_codes, top_k=1)
        top3_correct = any_expected_in_codes(agent_codes, expected_codes, top_k=3)
        top5_correct = any_expected_in_codes(agent_codes, expected_codes, top_k=5)

        # Legacy prefix accuracy plus level-aware prefix metrics.
        prefix_correct = None
        if expected_prefix and top1_code:
            prefix_correct = top1_code.startswith(expected_prefix)

        prefix_level1 = prefix_match(top1_code, expected_codes, level=1)
        prefix_level2 = prefix_match(top1_code, expected_codes, level=2)
        prefix_level3 = prefix_match(top1_code, expected_codes, level=3)

        # Количество вопросов (для многовопросных)
        questions_count = len(questions)
        questions_count_match = None
        if "expected_questions_count" in fix:
            questions_count_match = questions_count == fix["expected_questions_count"]

        invalid_llm_codes = [code for code in agent_codes if code not in agent.code_index]
        metrics = score_eval_case(
            agent_vid=agent_vid,
            agent_codes=agent_codes,
            expected_vid=expected_vid,
            expected_codes=expected_codes,
            expected_prefix=expected_prefix,
            expected_questions_count=fix.get("expected_questions_count"),
            dense_candidate_codes=dense_debug["dense_candidate_codes"],
            reranked_candidate_codes=dense_debug["reranked_candidate_codes"],
            valid_codes=set(agent.code_index),
        )

        results.append({
            "id": fix["id"],
            "title": fix["title"],
            "status": "OK",
            "vid_correct": metrics["vid_correct"],
            "top1_correct": metrics["top1_correct"],
            "top3_correct": metrics["top3_correct"],
            "top5_correct": metrics["top5_correct"],
            "prefix_correct": metrics["prefix_correct"],
            "prefix_level1": metrics["prefix_level1"],
            "prefix_level2": metrics["prefix_level2"],
            "prefix_level3": metrics["prefix_level3"],
            "questions_count_match": metrics["questions_count_match"],
            "questions_count": questions_count,
            "agent_vid": agent_vid,
            "agent_codes": agent_codes,
            "expected_codes": expected_codes,
            "confidence": result.overall_confidence,
            "needs_verification": result.needs_verification,
            "invalid_llm_codes": metrics["invalid_llm_codes"],
            "retrieval_recall_at_10": metrics["retrieval_recall_at_10"],
            "retrieval_recall_at_50": metrics["retrieval_recall_at_50"],
            "reranked_recall_at_10": metrics["reranked_recall_at_10"],
            "first_expected_rank_dense": metrics["first_expected_rank_dense"],
            "first_expected_rank_reranked": metrics["first_expected_rank_reranked"],
            "elapsed": round(elapsed, 2),
            "stage_timings": {
                "segmentation": None,
                "dense_retrieval": dense_debug["elapsed"],
                "rerank": None,
                "classification": round(classify_elapsed, 3),
                "total": round(elapsed, 3),
            },
            "debug": {
                "expected_codes": expected_codes,
                "segments": [appeal_text],
                "dense_candidates": dense_debug["dense_candidate_codes"],
                "dense_candidates_full": dense_debug["dense_candidates"],
                "lexical_candidates": dense_debug["lexical_candidate_codes"],
                "lexical_candidates_full": dense_debug["lexical_candidates"],
                "reranked_candidates": dense_debug["reranked_candidate_codes"],
                "reranked_candidates_full": dense_debug["reranked_candidates"],
                "llm_codes": agent_codes,
                "first_expected_rank_dense": dense_debug["first_expected_rank"],
                "first_expected_rank_reranked": dense_debug["first_expected_rank_reranked"],
                "stage_timings": {
                    "segmentation": None,
                    "dense_retrieval": dense_debug["elapsed"],
                    "rerank": None,
                    "classification": round(classify_elapsed, 3),
                    "total": round(elapsed, 3),
                },
            },
        })

    return {"results": results, "summary": summarize_eval_results(results)}


def bool_count(results: list[dict], key: str) -> int:
    return sum(1 for r in results if r.get(key) is True)


def summarize_results(results: list[dict]) -> dict[str, Any]:
    total = len(results)
    errors = sum(1 for r in results if r["status"] == "ERROR")
    ok_results = [r for r in results if r["status"] == "OK"]
    ok_total = len(ok_results)

    avg_confidence = (
        sum(r["confidence"] for r in ok_results if r.get("confidence")) / ok_total
        if ok_results else 0
    )
    avg_time = (
        sum(r["elapsed"] for r in ok_results if r.get("elapsed")) / ok_total
        if ok_results else 0
    )

    invalid_llm_code_count = sum(len(r.get("invalid_llm_codes", [])) for r in ok_results)

    return {
        "total": total,
        "ok": ok_total,
        "errors": errors,
        "vid_correct": bool_count(ok_results, "vid_correct"),
        "top1_correct": bool_count(ok_results, "top1_correct"),
        "top3_correct": bool_count(ok_results, "top3_correct"),
        "top5_correct": bool_count(ok_results, "top5_correct"),
        "prefix_correct": bool_count(ok_results, "prefix_correct"),
        "prefix_level1": bool_count(ok_results, "prefix_level1"),
        "prefix_level2": bool_count(ok_results, "prefix_level2"),
        "prefix_level3": bool_count(ok_results, "prefix_level3"),
        "retrieval_recall_at_10": bool_count(ok_results, "retrieval_recall_at_10"),
        "retrieval_recall_at_50": bool_count(ok_results, "retrieval_recall_at_50"),
        "reranked_recall_at_10": bool_count(ok_results, "reranked_recall_at_10"),
        "needs_verification": sum(1 for r in ok_results if r.get("needs_verification")),
        "invalid_llm_code_count": invalid_llm_code_count,
        "avg_confidence": avg_confidence,
        "avg_time": avg_time,
    }


def print_table(results: list[dict]) -> None:
    header = (
        f"{'ID':<6} {'Vid':<5} {'T1':<4} {'T3':<4} {'T5':<4} "
        f"{'R10':<4} {'R50':<4} {'RR10':<5} {'P1':<4} {'P2':<4} {'P3':<4} "
        f"{'Cnt':<4} {'Bad':<4} {'Conf':<6} {'Time':<7}"
    )
    print(header)
    print("-" * len(header))

    for r in results:
        if r["status"] == "ERROR":
            print(f"{r['id']:<6} ERROR: {r['error'][:80]}")
            continue

        def cell(value):
            return "OK" if value is True else ("XX" if value is False else "?")

        conf_s = f"{r['confidence']:.0%}" if r["confidence"] else "?"
        elapsed_s = f"{r['elapsed']:.1f}s" if r["elapsed"] else "?"
        bad_s = str(len(r.get("invalid_llm_codes", [])))

        print(
            f"{r['id']:<6} {cell(r['vid_correct']):<5} "
            f"{cell(r['top1_correct']):<4} {cell(r['top3_correct']):<4} {cell(r['top5_correct']):<4} "
            f"{cell(r['retrieval_recall_at_10']):<4} {cell(r['retrieval_recall_at_50']):<4} "
            f"{cell(r['reranked_recall_at_10']):<5} "
            f"{cell(r['prefix_level1']):<4} {cell(r['prefix_level2']):<4} {cell(r['prefix_level3']):<4} "
            f"{cell(r['questions_count_match']):<4} {bad_s:<4} {conf_s:<6} {elapsed_s:<7}"
        )


def format_pct(n: int, total: int) -> str:
    return f"{n}/{total} = {n/total*100:.1f}%" if total else "N/A"


def print_summary(summary: dict) -> None:
    ok_total = summary["ok"]
    print("\n" + "=" * 60)
    print("ИТОГОВЫЕ МЕТРИКИ")
    print("=" * 60)
    print(f"  Всего обращений:        {summary['total']}")
    print(f"  Ошибок:                 {summary['errors']}")
    print(f"  Точность вида:          {format_pct(summary['vid_correct'], ok_total)} (цель 90%)")
    print(f"  Exact Top-1:            {format_pct(summary['top1_correct'], ok_total)} (цель 70%)")
    print(f"  Exact Top-3:            {format_pct(summary['top3_correct'], ok_total)} (цель 85%)")
    print(f"  Exact Top-5:            {format_pct(summary['top5_correct'], ok_total)}")
    print(f"  Dense recall@10:        {format_pct(summary['retrieval_recall_at_10'], ok_total)}")
    print(f"  Dense recall@50:        {format_pct(summary['retrieval_recall_at_50'], ok_total)}")
    print(f"  Reranked recall@10:     {format_pct(summary['reranked_recall_at_10'], ok_total)}")
    print(f"  Prefix level-1:         {format_pct(summary['prefix_level1'], ok_total)}")
    print(f"  Prefix level-2:         {format_pct(summary['prefix_level2'], ok_total)}")
    print(f"  Prefix level-3:         {format_pct(summary['prefix_level3'], ok_total)}")
    print(f"  Доля верификаций:       {format_pct(summary['needs_verification'], ok_total)} (цель 30%)")
    print(f"  Invalid LLM codes:      {summary['invalid_llm_code_count']}")
    print(f"  Средн. уверенность:     {summary['avg_confidence']:.1%}")
    print(f"  Средн. время:           {summary['avg_time']:.1f} сек (цель 8 сек)")
    print("=" * 60)


def write_markdown_report(data: dict, report_path: Path, fixtures_path: Path) -> None:
    summary = data["summary"]
    ok_total = summary["ok"]
    lines = [
        "# Отчёт точности v2",
        "",
        f"**Дата:** {time.strftime('%Y-%m-%d')}",
        f"**Датасет:** `{fixtures_path}`",
        "**Pipeline:** baseline dense retrieval + текущий LLM selection",
        "",
        "## Метрики",
        "",
        "| Метрика | Результат |",
        "|---|---:|",
        f"| Вид обращения | {format_pct(summary['vid_correct'], ok_total)} |",
        f"| Exact Top-1 | {format_pct(summary['top1_correct'], ok_total)} |",
        f"| Exact Top-3 | {format_pct(summary['top3_correct'], ok_total)} |",
        f"| Exact Top-5 | {format_pct(summary['top5_correct'], ok_total)} |",
        f"| Dense retrieval recall@10 | {format_pct(summary['retrieval_recall_at_10'], ok_total)} |",
        f"| Dense retrieval recall@50 | {format_pct(summary['retrieval_recall_at_50'], ok_total)} |",
        f"| Reranked recall@10 | {format_pct(summary['reranked_recall_at_10'], ok_total)} |",
        f"| Prefix level-1 | {format_pct(summary['prefix_level1'], ok_total)} |",
        f"| Prefix level-2 | {format_pct(summary['prefix_level2'], ok_total)} |",
        f"| Prefix level-3 | {format_pct(summary['prefix_level3'], ok_total)} |",
        f"| Invalid LLM codes | {summary['invalid_llm_code_count']} |",
        f"| Средняя уверенность | {summary['avg_confidence']:.1%} |",
        f"| Среднее время | {summary['avg_time']:.1f} сек |",
        "",
        "## Детализация",
        "",
        "| ID | Top-1 | Top-3 | R@10 | R@50 | RR@10 | Dense rank | Rerank rank | LLM codes | Expected |",
        "|---|---|---|---|---|---|---:|---:|---|---|",
    ]

    for r in data["results"]:
        if r["status"] == "ERROR":
            lines.append(f"| {r['id']} | ERROR | ERROR | ? | ? | ? |  |  | `{r['error'][:60]}` |  |")
            continue
        rank = r.get("first_expected_rank_dense")
        rank_s = str(rank) if rank is not None else "-"
        rerank = r.get("first_expected_rank_reranked")
        rerank_s = str(rerank) if rerank is not None else "-"
        lines.append(
            "| {id} | {top1} | {top3} | {r10} | {r50} | {rr10} | {rank} | {rerank} | `{llm}` | `{expected}` |".format(
                id=r["id"],
                top1="OK" if r["top1_correct"] else "XX",
                top3="OK" if r["top3_correct"] else "XX",
                r10="OK" if r["retrieval_recall_at_10"] else "XX",
                r50="OK" if r["retrieval_recall_at_50"] else "XX",
                rr10="OK" if r["reranked_recall_at_10"] else "XX",
                rank=rank_s,
                rerank=rerank_s,
                llm=", ".join(r["agent_codes"]),
                expected=", ".join(r["expected_codes"]),
            )
        )

    lines.extend([
        "",
        "## Notes",
        "",
        "- `Top-1/Top-3/Top-5` здесь относятся к финальным кодам вопросов после LLM, а не к retrieval.",
        "- `Dense retrieval recall@K` показывает, попал ли хотя бы один ожидаемый код в dense-кандидаты до LLM.",
        "- `Reranked recall@10` показывает эффект дешёвого lexical rerank после dense top-50 перед передачей кандидатов в LLM.",
    ])

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Оценка точности классификатора")
    parser.add_argument(
        "--fixtures",
        default="tests/fixtures/test_appeals.json",
        help="Путь к файлу с тестовыми обращениями",
    )
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=50,
        help="Сколько dense-кандидатов сохранять для stage-level diagnostics",
    )
    parser.add_argument(
        "--json-report",
        default="data/accuracy_report.json",
        help="Путь для сырого JSON-отчёта",
    )
    parser.add_argument(
        "--markdown-report",
        default="docs/accuracy_report_v2.md",
        help="Путь для markdown-отчёта",
    )
    args = parser.parse_args()

    fixtures_path = Path(args.fixtures)
    if not fixtures_path.exists():
        fixtures_path = Path(__file__).parent / fixtures_path

    print(f"Загрузка тестовых обращений: {fixtures_path}")
    fixtures = load_fixtures(fixtures_path)
    print(f"Загружено {len(fixtures)} обращений\n")

    print("Инициализация агента (загрузка модели эмбеддингов)...")
    agent = ClassifierAgent()
    print()

    print("Запуск классификации...\n")
    data = run_eval(fixtures, agent, retrieval_top_k=args.retrieval_top_k)
    results = data["results"]

    print_table(results)
    print_summary(data["summary"])

    # Сохраняем сырые результаты в JSON для последующего анализа
    json_report_path = Path(args.json_report)
    json_report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_report_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nСырые результаты сохранены: {json_report_path}")

    markdown_report_path = Path(args.markdown_report)
    write_markdown_report(data, markdown_report_path, fixtures_path)
    print(f"Markdown-отчёт сохранён: {markdown_report_path}")


if __name__ == "__main__":
    main()

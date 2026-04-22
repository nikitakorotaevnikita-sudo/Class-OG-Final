"""
Скрипт оценки точности классификации.
Запуск: python tests/eval_accuracy.py [--fixtures tests/fixtures/test_appeals.json]

Вывод: таблица с результатами по каждному тесту + итоговые метрики.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import ClassifierAgent


def load_fixtures(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_eval(fixtures: list[dict], agent: ClassifierAgent) -> dict:
    results = []

    for fix in fixtures:
        appeal_text = fix["text"]
        expected_vid = fix.get("expected_vid")
        expected_codes = fix.get("expected_codes", [])
        expected_prefix = fix.get("expected_prefix", "")

        start = time.time()
        try:
            result = agent.classify(appeal_text)
        except Exception as e:
            results.append({
                "id": fix["id"],
                "title": fix["title"],
                "status": "ERROR",
                "error": str(e),
                "elapsed": None,
            })
            continue

        elapsed = time.time() - start
        questions = result.questions
        agent_codes = [q.code for q in questions]
        agent_vid = result.vid_obrascheniya

        # Вид обращения: точное совпадение
        vid_correct = (agent_vid == expected_vid) if expected_vid else None

        # Top-1 accuracy: первый предложенный код в expected_codes
        top1_code = agent_codes[0] if agent_codes else None
        top1_correct = (top1_code in expected_codes) if expected_codes else None

        # Top-3 accuracy: любой из первых трёх кодов в expected_codes
        top3_codes = agent_codes[:3]
        top3_correct = any(c in expected_codes for c in top3_codes) if expected_codes and top3_codes else None

        # Prefix accuracy: код начинается с ожидаемого префикса
        prefix_correct = None
        if expected_prefix and top1_code:
            prefix_correct = top1_code.startswith(expected_prefix)

        # Количество вопросов (для многовопросных)
        questions_count = len(questions)
        questions_count_match = None
        if "expected_questions_count" in fix:
            questions_count_match = questions_count == fix["expected_questions_count"]

        results.append({
            "id": fix["id"],
            "title": fix["title"],
            "status": "OK",
            "vid_correct": vid_correct,
            "top1_correct": top1_correct,
            "top3_correct": top3_correct,
            "prefix_correct": prefix_correct,
            "questions_count_match": questions_count_match,
            "agent_vid": agent_vid,
            "agent_codes": agent_codes,
            "expected_codes": expected_codes,
            "confidence": result.overall_confidence,
            "needs_verification": result.needs_verification,
            "elapsed": round(elapsed, 2),
        })

    return {"results": results}


def print_table(results: list[dict]) -> None:
    header = (
        f"{'ID':<6} {'Vid':<12} {'Top1':<6} {'Top3':<6} {'Pfx':<6} "
        f"{'Cnt':<6} {'Conf':<8} {'Verif':<6} {'Time':<7}"
    )
    print(header)
    print("-" * len(header))

    for r in results:
        if r["status"] == "ERROR":
            print(f"{r['id']:<6} ERROR: {r['error'][:50]}")
            continue

        vid_s = "OK" if r["vid_correct"] else ("XX" if r["vid_correct"] is not None else "?")
        t1_s  = "OK" if r["top1_correct"] else ("XX" if r["top1_correct"] is not None else "?")
        t3_s  = "OK" if r["top3_correct"] else ("XX" if r["top3_correct"] is not None else "?")
        pfx_s = "OK" if r["prefix_correct"] else ("XX" if r["prefix_correct"] is not None else "?")
        cnt_s = "OK" if r["questions_count_match"] else ("?" if r["questions_count_match"] is None else "XX")
        conf_s = f"{r['confidence']:.0%}" if r["confidence"] else "?"
        needs_s = "WARN" if r["needs_verification"] else "OK"
        elapsed_s = f"{r['elapsed']:.1f}s" if r["elapsed"] else "?"

        print(
            f"{r['id']:<6} {vid_s:<12} {t1_s:<7} {t3_s:<7} {pfx_s:<7} "
            f"{cnt_s:<6} {conf_s:<9} {needs_s:<7} {elapsed_s:<7}"
        )


def print_summary(results: list[dict]) -> None:
    total = len(results)
    errors = sum(1 for r in results if r["status"] == "ERROR")
    ok_results = [r for r in results if r["status"] == "OK"]

    vid_correct = sum(1 for r in ok_results if r["vid_correct"] is True)
    top1_correct = sum(1 for r in ok_results if r["top1_correct"] is True)
    top3_correct = sum(1 for r in ok_results if r["top3_correct"] is True)
    prefix_correct = sum(1 for r in ok_results if r["prefix_correct"] is True)
    needs_verification = sum(1 for r in ok_results if r["needs_verification"])

    avg_confidence = (
        sum(r["confidence"] for r in ok_results if r["confidence"]) / len(ok_results)
        if ok_results else 0
    )
    avg_time = (
        sum(r["elapsed"] for r in ok_results if r["elapsed"]) / len(ok_results)
        if ok_results else 0
    )

    def pct(n, total_):
        return f"{n}/{total_} = {n/total_*100:.1f}%" if total_ else "N/A"

    print("\n" + "=" * 60)
    print("ИТОГОВЫЕ МЕТРИКИ")
    print("=" * 60)
    print(f"  Всего обращений:    {total}")
    print(f"  Ошибок:             {errors}")
    print(f"  Точность вида:      {pct(vid_correct, len(ok_results))} (цель 90%)")
    print(f"  Top-1 точность:     {pct(top1_correct, len(ok_results))} (цель 70%)")
    print(f"  Top-3 точность:     {pct(top3_correct, len(ok_results))} (цель 85%)")
    print(f"  Prefix точность:    {pct(prefix_correct, len(ok_results))} (цель 85%)")
    print(f"  Доля верификаций:   {pct(needs_verification, len(ok_results))} (цель 30%)")
    print(f"  Средн. уверенность: {avg_confidence:.1%}")
    print(f"  Средн. время:       {avg_time:.1f} сек (цель 8 сек)")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Оценка точности классификатора")
    parser.add_argument(
        "--fixtures",
        default="tests/fixtures/test_appeals.json",
        help="Путь к файлу с тестовыми обращениями",
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
    data = run_eval(fixtures, agent)
    results = data["results"]

    print_table(results)
    print_summary(results)

    # Сохраняем сырые результаты в JSON для последующего анализа
    report_path = Path("data/accuracy_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nСырые результаты сохранены: {report_path}")


if __name__ == "__main__":
    main()

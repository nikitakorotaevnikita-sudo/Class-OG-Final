"""Evaluate retrieval quality on the imported II25 dataset without LLM calls.

Usage:
  python tests/eval_ii25_retrieval.py
  python tests/eval_ii25_retrieval.py --dataset data/ii25_test.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from classifier_agent import ClassifierAgent  # noqa: E402
from eval_metrics_helpers import code_prefix, first_expected_rank, pct  # noqa: E402


DEFAULT_DATASET = REPO_ROOT / "data" / "ii25_test.jsonl"
DEFAULT_JSON_REPORT = REPO_ROOT / "data" / "ii25_retrieval_report.json"
DEFAULT_MD_REPORT = REPO_ROOT / "docs" / "ii25_retrieval_report.md"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def candidate_codes(candidates: list[dict[str, Any]]) -> list[str]:
    return [candidate["code"] for candidate in candidates]


def contains_code(codes: list[str], expected_code: str, top_k: int | None = None) -> bool:
    scope = codes[:top_k] if top_k is not None else codes
    return expected_code in scope


def prefix_top1_match(codes: list[str], expected_code: str, level: int) -> bool:
    if not codes:
        return False
    return code_prefix(codes[0], level) == code_prefix(expected_code, level)


def mean_rank(ranks: list[int | None]) -> float | None:
    present = [rank for rank in ranks if rank is not None]
    return round(statistics.mean(present), 1) if present else None


def evaluate_record(agent: ClassifierAgent, record: dict[str, Any], dense_top_k: int, lexical_top_k: int, rerank_top_k: int) -> dict[str, Any]:
    text = record["appeal_text"]
    expected_code = record["assigned_code"]

    started = time.time()
    dense = agent._search_candidates(text, top_k=dense_top_k)
    lexical = agent._search_lexical_candidates(text, top_k=lexical_top_k)
    merged = agent._merge_candidate_pools(dense, lexical)
    reranked = agent._rerank_candidates(text, merged, top_k=rerank_top_k)
    elapsed = time.time() - started

    dense_codes = candidate_codes(dense)
    lexical_codes = candidate_codes(lexical)
    merged_codes = candidate_codes(merged)
    reranked_codes = candidate_codes(reranked)

    return {
        "id": record["id"],
        "source_file": record["source_file"],
        "expected_code": expected_code,
        "expected_name": record.get("code_name", ""),
        "dense_rank": first_expected_rank(dense_codes, [expected_code]),
        "lexical_rank": first_expected_rank(lexical_codes, [expected_code]),
        "merged_rank": first_expected_rank(merged_codes, [expected_code]),
        "reranked_rank": first_expected_rank(reranked_codes, [expected_code]),
        "dense_recall_at_10": contains_code(dense_codes, expected_code, 10),
        "dense_recall_at_50": contains_code(dense_codes, expected_code, 50),
        "lexical_recall_at_30": contains_code(lexical_codes, expected_code, 30),
        "merged_recall_at_50": contains_code(merged_codes, expected_code, 50),
        "reranked_top1": contains_code(reranked_codes, expected_code, 1),
        "reranked_top3": contains_code(reranked_codes, expected_code, 3),
        "reranked_top5": contains_code(reranked_codes, expected_code, 5),
        "reranked_recall_at_10": contains_code(reranked_codes, expected_code, 10),
        "reranked_prefix_level1": prefix_top1_match(reranked_codes, expected_code, 1),
        "reranked_prefix_level2": prefix_top1_match(reranked_codes, expected_code, 2),
        "reranked_prefix_level3": prefix_top1_match(reranked_codes, expected_code, 3),
        "top_reranked_codes": reranked_codes[:5],
        "top_reranked": [
            {
                "code": item["code"],
                "name": item["name"],
                "similarity": item.get("similarity"),
                "rerank_score": item.get("rerank_score"),
                "source": item.get("source"),
            }
            for item in reranked[:5]
        ],
        "elapsed": round(elapsed, 3),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)

    def count_true(key: str) -> int:
        return sum(1 for row in rows if row.get(key) is True)

    return {
        "total": total,
        "dense_recall_at_10": count_true("dense_recall_at_10"),
        "dense_recall_at_50": count_true("dense_recall_at_50"),
        "lexical_recall_at_30": count_true("lexical_recall_at_30"),
        "merged_recall_at_50": count_true("merged_recall_at_50"),
        "reranked_top1": count_true("reranked_top1"),
        "reranked_top3": count_true("reranked_top3"),
        "reranked_top5": count_true("reranked_top5"),
        "reranked_recall_at_10": count_true("reranked_recall_at_10"),
        "reranked_prefix_level1": count_true("reranked_prefix_level1"),
        "reranked_prefix_level2": count_true("reranked_prefix_level2"),
        "reranked_prefix_level3": count_true("reranked_prefix_level3"),
        "mean_dense_rank_found": mean_rank([row["dense_rank"] for row in rows]),
        "mean_lexical_rank_found": mean_rank([row["lexical_rank"] for row in rows]),
        "mean_reranked_rank_found": mean_rank([row["reranked_rank"] for row in rows]),
        "avg_elapsed": round(statistics.mean(row["elapsed"] for row in rows), 3) if rows else 0,
    }


def print_summary(summary: dict[str, Any]) -> None:
    total = summary["total"]
    print("\n" + "=" * 60)
    print("II25 RETRIEVAL METRICS")
    print("=" * 60)
    print(f"  Всего обращений:        {total}")
    print(f"  Dense recall@10:        {pct(summary['dense_recall_at_10'], total)}")
    print(f"  Dense recall@50:        {pct(summary['dense_recall_at_50'], total)}")
    print(f"  Lexical recall@30:      {pct(summary['lexical_recall_at_30'], total)}")
    print(f"  Merged recall@50:       {pct(summary['merged_recall_at_50'], total)}")
    print(f"  Reranked Top-1:         {pct(summary['reranked_top1'], total)}")
    print(f"  Reranked Top-3:         {pct(summary['reranked_top3'], total)}")
    print(f"  Reranked Top-5:         {pct(summary['reranked_top5'], total)}")
    print(f"  Reranked recall@10:     {pct(summary['reranked_recall_at_10'], total)}")
    print(f"  Prefix level-1 Top-1:   {pct(summary['reranked_prefix_level1'], total)}")
    print(f"  Prefix level-2 Top-1:   {pct(summary['reranked_prefix_level2'], total)}")
    print(f"  Prefix level-3 Top-1:   {pct(summary['reranked_prefix_level3'], total)}")
    print(f"  Avg dense rank found:   {summary['mean_dense_rank_found']}")
    print(f"  Avg rerank found:       {summary['mean_reranked_rank_found']}")
    print(f"  Avg time:               {summary['avg_elapsed']} sec")
    print("=" * 60)


def write_markdown(path: Path, dataset: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    total = summary["total"]
    lines = [
        "# II25 Retrieval Report",
        "",
        f"**Dataset:** `{dataset}`",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Dense recall@10 | {pct(summary['dense_recall_at_10'], total)} |",
        f"| Dense recall@50 | {pct(summary['dense_recall_at_50'], total)} |",
        f"| Lexical recall@30 | {pct(summary['lexical_recall_at_30'], total)} |",
        f"| Merged recall@50 | {pct(summary['merged_recall_at_50'], total)} |",
        f"| Reranked Top-1 | {pct(summary['reranked_top1'], total)} |",
        f"| Reranked Top-3 | {pct(summary['reranked_top3'], total)} |",
        f"| Reranked Top-5 | {pct(summary['reranked_top5'], total)} |",
        f"| Reranked recall@10 | {pct(summary['reranked_recall_at_10'], total)} |",
        f"| Prefix level-1 Top-1 | {pct(summary['reranked_prefix_level1'], total)} |",
        f"| Prefix level-2 Top-1 | {pct(summary['reranked_prefix_level2'], total)} |",
        f"| Prefix level-3 Top-1 | {pct(summary['reranked_prefix_level3'], total)} |",
        f"| Avg dense rank found | {summary['mean_dense_rank_found']} |",
        f"| Avg rerank found | {summary['mean_reranked_rank_found']} |",
        "",
        "## Details",
        "",
        "| ID | Expected | Dense rank | Rerank rank | Top reranked codes |",
        "|---|---|---:|---:|---|",
    ]
    for row in rows:
        top_codes = ", ".join(f"`{code}`" for code in row["top_reranked_codes"])
        lines.append(
            f"| {row['id']} | `{row['expected_code']}` | "
            f"{row['dense_rank'] or '-'} | {row['reranked_rank'] or '-'} | {top_codes} |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate II25 retrieval without LLM calls.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--md-out", type=Path, default=DEFAULT_MD_REPORT)
    parser.add_argument("--dense-top-k", type=int, default=50)
    parser.add_argument("--lexical-top-k", type=int, default=30)
    parser.add_argument("--rerank-top-k", type=int, default=10)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    records = load_jsonl(args.dataset)
    if not records:
        print(f"No records found in {args.dataset}", file=sys.stderr)
        return 2

    print(f"Loading classifier agent for retrieval eval...")
    agent = ClassifierAgent()
    print(f"Evaluating {len(records)} records from {args.dataset}...")

    rows = [
        evaluate_record(agent, record, args.dense_top_k, args.lexical_top_k, args.rerank_top_k)
        for record in records
    ]
    summary = summarize(rows)

    report = {
        "dataset": str(args.dataset),
        "summary": summary,
        "results": rows,
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(args.md_out, args.dataset, summary, rows)

    print_summary(summary)
    print(f"\nJSON report: {args.json_out}")
    print(f"Markdown report: {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

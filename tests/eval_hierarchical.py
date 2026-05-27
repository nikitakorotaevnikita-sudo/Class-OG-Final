"""Hierarchical pipeline evaluation with per-stage failure attribution.

Phase 0: baseline evaluation of the current pipeline (no router LLM yet).
Phase 1+ will extend stage_outputs with router_l1/router_l2.

Usage:
    python tests/eval_hierarchical.py --dataset data/ii25_test.jsonl --stage retrieval
    python tests/eval_hierarchical.py --stage reranker   # not yet implemented
    python tests/eval_hierarchical.py --stage final      # not yet implemented
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from classifier_agent import ClassifierAgent  # noqa: E402
from eval_metrics_helpers import (  # noqa: E402
    attribute_failure,
    compute_per_stage_metrics,
    first_expected_rank,
)


DEFAULT_DATASET = REPO_ROOT / "data" / "ii25_test.jsonl"
EVAL_RESULTS_DIR = REPO_ROOT / "eval_results"
BASELINES_DIR = REPO_ROOT / "data" / "eval_baselines"


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hierarchical pipeline evaluation")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Path to JSONL dataset (default: data/ii25_test.jsonl)",
    )
    parser.add_argument(
        "--stage",
        choices=["retrieval", "reranker", "final", "all"],
        default="all",
        help="Which pipeline stage to evaluate (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only first N cases (for smoke testing)",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save summary as baseline snapshot to data/eval_baselines/",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="run",
        help="Tag for output filename (e.g., 'baseline', 'v1.1-reranker-fix')",
    )
    return parser.parse_args()


def write_per_case_log(rows: list[dict[str, Any]], stage: str, tag: str) -> Path:
    """Write per-case rows as JSONL to eval_results/."""
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = EVAL_RESULTS_DIR / f"{timestamp}_{tag}_{stage}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out_path


def write_summary_md(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    stage: str,
    tag: str,
    dataset_path: Path,
) -> Path:
    """Write human-readable markdown summary to eval_results/."""
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = EVAL_RESULTS_DIR / f"{timestamp}_{tag}_{stage}_summary.md"

    lines = [
        f"# Eval Hierarchical — {tag} — {stage}",
        "",
        f"**Date:** {datetime.now().isoformat()}",
        f"**Dataset:** `{dataset_path}` ({summary['total']} records)",
        f"**Stage:** `{stage}`",
        "",
        "## Summary metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    if "top1_accuracy_pct" in summary:
        lines.append(f"| Top-1 accuracy | {summary['correct']}/{summary['evaluable']} = {summary['top1_accuracy_pct']}% |")
    for key in ["dense_recall_at_10_pct", "dense_recall_at_50_pct", "reranked_recall_at_10_pct"]:
        if key in summary:
            lines.append(f"| {key.replace('_pct', '').replace('_', ' ')} | {summary[key]}% |")
    lines.append("")

    if summary.get("failure_breakdown"):
        lines.extend([
            "## Failure attribution",
            "",
            "| Category | Count | % of evaluable |",
            "|---|---:|---:|",
        ])
        evaluable = summary["evaluable"] or 1
        for category, count in sorted(summary["failure_breakdown"].items(), key=lambda kv: -kv[1]):
            pct = round(100.0 * count / evaluable, 1)
            lines.append(f"| `{category}` | {count} | {pct}% |")
        lines.append("")

    lines.extend([
        "## Per-case details",
        "",
        "| ID | Gold | Dense rank | Reranked rank | Final top-1 | Attribution |",
        "|---|---|---:|---:|---|---|",
    ])
    for row in rows:
        lines.append(
            f"| `{row['case_id']}` | `{row['gold_code']}` | "
            f"{row.get('dense_rank') or '−'} | "
            f"{row.get('reranked_rank') or '−'} | "
            f"`{row.get('final_top1_code') or '−'}` | "
            f"`{row.get('failure_attribution', '−')}` |"
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def evaluate_record_retrieval(
    agent: ClassifierAgent, record: dict[str, Any], dense_top_k: int = 50
) -> dict[str, Any]:
    """Run dense retrieval only — no reranker, no LLM. Fast (~50ms/case)."""
    text = record["appeal_text"]
    gold_code = record["assigned_code"]
    gold_codes = [gold_code]

    started = time.time()
    dense_candidates = agent._search_candidates(text, top_k=dense_top_k)
    elapsed = round(time.time() - started, 3)

    dense_codes = [c["code"] for c in dense_candidates]
    rank = first_expected_rank(dense_codes, gold_codes)

    return {
        "case_id": record["id"],
        "gold_code": gold_code,
        "dense_top50_codes": dense_codes[:50],
        "dense_top10_codes": dense_codes[:10],
        "dense_rank": rank,
        "dense_recall_at_10": (rank is not None and rank <= 10),
        "dense_recall_at_50": (rank is not None and rank <= 50),
        "elapsed": elapsed,
        # Stages below are not run in --stage retrieval; placeholders for attribution
        "reranked_top10_codes": [],
        "final_top1_code": None,
    }


def evaluate_record_reranker(
    agent: ClassifierAgent, record: dict[str, Any]
) -> dict[str, Any]:
    """Run dense + lexical + reranker — no LLM."""
    text = record["appeal_text"]
    gold_code = record["assigned_code"]
    gold_codes = [gold_code]

    started = time.time()
    dense_candidates = agent._search_candidates(text, top_k=50)
    lexical_candidates = agent._search_lexical_candidates(text, top_k=30)
    merged = agent._merge_candidate_pools(dense_candidates, lexical_candidates)
    reranked = agent._rerank_candidates(text, merged, top_k=10)
    elapsed = round(time.time() - started, 3)

    dense_codes = [c["code"] for c in dense_candidates]
    reranked_codes = [c["code"] for c in reranked]
    dense_rank = first_expected_rank(dense_codes, gold_codes)
    reranked_rank = first_expected_rank(reranked_codes, gold_codes)

    return {
        "case_id": record["id"],
        "gold_code": gold_code,
        "dense_top50_codes": dense_codes[:50],
        "dense_top10_codes": dense_codes[:10],
        "dense_rank": dense_rank,
        "dense_recall_at_10": (dense_rank is not None and dense_rank <= 10),
        "dense_recall_at_50": (dense_rank is not None and dense_rank <= 50),
        "reranked_top10_codes": reranked_codes,
        "reranked_rank": reranked_rank,
        "reranked_recall_at_10": (reranked_rank is not None and reranked_rank <= 10),
        "elapsed": elapsed,
        "final_top1_code": None,
    }


def evaluate_record_final(
    agent: ClassifierAgent, record: dict[str, Any]
) -> dict[str, Any]:
    """Full pipeline including LLM final pick."""
    text = record["appeal_text"]
    gold_code = record["assigned_code"]
    gold_codes = [gold_code]

    started = time.time()

    # Pre-LLM stages (collect for attribution)
    dense_candidates = agent._search_candidates(text, top_k=50)
    lexical_candidates = agent._search_lexical_candidates(text, top_k=30)
    merged = agent._merge_candidate_pools(dense_candidates, lexical_candidates)
    reranked = agent._rerank_candidates(text, merged, top_k=10)

    # LLM final
    try:
        result = agent.classify(text)
        final_codes = [q.code for q in result.questions if q.code]
        final_top1 = final_codes[0] if final_codes else None
        llm_error = None
    except Exception as exc:  # noqa: BLE001
        final_codes = []
        final_top1 = None
        llm_error = str(exc)

    elapsed = round(time.time() - started, 3)

    dense_codes = [c["code"] for c in dense_candidates]
    reranked_codes = [c["code"] for c in reranked]
    dense_rank = first_expected_rank(dense_codes, gold_codes)
    reranked_rank = first_expected_rank(reranked_codes, gold_codes)

    return {
        "case_id": record["id"],
        "gold_code": gold_code,
        "dense_top50_codes": dense_codes[:50],
        "dense_top10_codes": dense_codes[:10],
        "dense_rank": dense_rank,
        "dense_recall_at_10": (dense_rank is not None and dense_rank <= 10),
        "dense_recall_at_50": (dense_rank is not None and dense_rank <= 50),
        "reranked_top10_codes": reranked_codes,
        "reranked_rank": reranked_rank,
        "reranked_recall_at_10": (reranked_rank is not None and reranked_rank <= 10),
        "final_top1_code": final_top1,
        "final_codes": final_codes,
        "llm_error": llm_error,
        "elapsed": elapsed,
    }


def main() -> int:
    args = parse_args()
    print(f"[eval_hierarchical] dataset={args.dataset.name} stage={args.stage}")

    records = load_jsonl(args.dataset)
    if args.limit:
        records = records[: args.limit]
    print(f"[eval_hierarchical] loaded {len(records)} records")

    print("[eval_hierarchical] initializing ClassifierAgent...")
    agent = ClassifierAgent()

    rows: list[dict[str, Any]] = []
    for i, record in enumerate(records, start=1):
        print(f"  [{i}/{len(records)}] {record['id']}", end=" ", flush=True)
        if args.stage == "retrieval":
            row = evaluate_record_retrieval(agent, record)
        elif args.stage == "reranker":
            row = evaluate_record_reranker(agent, record)
        elif args.stage in ("final", "all"):
            row = evaluate_record_final(agent, record)
        else:
            raise NotImplementedError(f"stage={args.stage}")
        rows.append(row)
        print(f"rank={row.get('dense_rank')} t={row['elapsed']}s")

    # Attribute failures per-row
    for row in rows:
        row["failure_attribution"] = attribute_failure(
            gold_codes=[row["gold_code"]],
            dense_top50_codes=row.get("dense_top50_codes", []),
            reranked_top10_codes=row.get("reranked_top10_codes", []),
            final_top1_code=row.get("final_top1_code"),
        )

    per_case_path = write_per_case_log(rows, args.stage, args.tag)
    print(f"[eval_hierarchical] per-case log: {per_case_path}")

    summary = compute_per_stage_metrics(per_case_rows=rows)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    md_path = write_summary_md(rows, summary, args.stage, args.tag, args.dataset)
    print(f"[eval_hierarchical] summary md: {md_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

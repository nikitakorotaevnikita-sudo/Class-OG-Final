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
        else:
            raise NotImplementedError(f"stage={args.stage} not yet implemented")
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

    summary = compute_per_stage_metrics(per_case_rows=rows)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

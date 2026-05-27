"""Eval retrieval+rerank with CrossEncoder on ii25_test (без LLM-вызовов).

Сравнивает:
  Baseline: dense + lexical + heuristic_rerank → top-10
  +CE:      то же → CE rerank ещё раз → top-10

Печатает рядом метрики, чтобы видеть прирост от CE.

Usage:
  python tests/eval_ii25_with_ce.py
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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def first_rank(codes: list[str], expected: str) -> int | None:
    for i, c in enumerate(codes, start=1):
        if c == expected:
            return i
    return None


def code_prefix(code: str, level: int) -> str:
    return ".".join((code or "").split(".")[:level])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "data" / "ii25_test.jsonl")
    args = parser.parse_args()

    records = load_jsonl(args.dataset)
    print(f"Loaded {len(records)} test records")

    from classifier_agent import ClassifierAgent
    agent = ClassifierAgent()

    print("\nRunning eval (per record: heuristic + CE)...\n")

    rows = []
    for i, rec in enumerate(records, 1):
        text = rec["appeal_text"]
        expected = rec["assigned_code"]

        # Baseline: same path as before (heuristic only)
        start_h = time.time()
        dense = agent._search_candidates(text, top_k=100)
        lexical = agent._search_lexical_candidates(text, top_k=30)
        merged = agent._merge_candidate_pools(dense, lexical)
        heuristic_top = agent._rerank_candidates(text, merged, top_k=30)
        elapsed_h = time.time() - start_h

        heuristic_codes = [c["code"] for c in heuristic_top]

        # +CE: rerank heuristic_top via CE
        ce_codes = heuristic_codes  # fallback
        elapsed_ce = 0.0
        if agent.ce_reranker is not None:
            start_ce = time.time()
            ce_reranked = agent.ce_reranker.rerank(text, list(heuristic_top), top_k=10)
            elapsed_ce = time.time() - start_ce
            ce_codes = [c["code"] for c in ce_reranked]

        rank_h = first_rank(heuristic_codes[:10], expected)
        rank_ce = first_rank(ce_codes[:10], expected)

        # Take top-10 of heuristic as the "reference" pool — top_k=10 from heuristic
        heuristic_top_10_codes = heuristic_codes[:10]
        ce_top_10_codes = ce_codes[:10]

        row = {
            "id": rec["id"],
            "expected": expected,
            "heuristic_rank": rank_h,
            "ce_rank": rank_ce,
            "h_top1": heuristic_top_10_codes[0] if heuristic_top_10_codes else None,
            "ce_top1": ce_top_10_codes[0] if ce_top_10_codes else None,
            "h_in_top1": heuristic_top_10_codes[:1] == [expected],
            "ce_in_top1": ce_top_10_codes[:1] == [expected],
            "h_in_top3": expected in heuristic_top_10_codes[:3],
            "ce_in_top3": expected in ce_top_10_codes[:3],
            "h_in_top5": expected in heuristic_top_10_codes[:5],
            "ce_in_top5": expected in ce_top_10_codes[:5],
            "h_in_top10": expected in heuristic_top_10_codes,
            "ce_in_top10": expected in ce_top_10_codes,
            "h_l1": code_prefix(heuristic_top_10_codes[0] if heuristic_top_10_codes else "", 1) == code_prefix(expected, 1),
            "ce_l1": code_prefix(ce_top_10_codes[0] if ce_top_10_codes else "", 1) == code_prefix(expected, 1),
            "h_l2": code_prefix(heuristic_top_10_codes[0] if heuristic_top_10_codes else "", 2) == code_prefix(expected, 2),
            "ce_l2": code_prefix(ce_top_10_codes[0] if ce_top_10_codes else "", 2) == code_prefix(expected, 2),
            "h_l3": code_prefix(heuristic_top_10_codes[0] if heuristic_top_10_codes else "", 3) == code_prefix(expected, 3),
            "ce_l3": code_prefix(ce_top_10_codes[0] if ce_top_10_codes else "", 3) == code_prefix(expected, 3),
        }
        rows.append(row)

        if i % 10 == 0:
            print(f"  {i}/{len(records)}: heuristic_top1 {sum(r['h_in_top1'] for r in rows)}, ce_top1 {sum(r['ce_in_top1'] for r in rows)}")

    n = len(rows)
    print("\n" + "=" * 70)
    print(f"II25 retrieval+rerank — {n} cases (no LLM)")
    print("=" * 70)
    print(f"{'Metric':<25s} {'Heuristic':>12s} {'+CrossEncoder':>16s} {'Δ':>8s}")
    print("-" * 70)
    for label, h_key, ce_key in [
        ("Top-1",          "h_in_top1",   "ce_in_top1"),
        ("Top-3",          "h_in_top3",   "ce_in_top3"),
        ("Top-5",          "h_in_top5",   "ce_in_top5"),
        ("Recall@10",      "h_in_top10",  "ce_in_top10"),
        ("Prefix L1 Top-1","h_l1",         "ce_l1"),
        ("Prefix L2 Top-1","h_l2",         "ce_l2"),
        ("Prefix L3 Top-1","h_l3",         "ce_l3"),
    ]:
        h = sum(1 for r in rows if r[h_key])
        ce = sum(1 for r in rows if r[ce_key])
        delta = ce - h
        sign = f"+{delta}" if delta >= 0 else f"{delta}"
        print(f"{label:<25s} {h}/{n} ({h/n*100:5.1f}%)  {ce}/{n} ({ce/n*100:5.1f}%)  {sign:>8s}")

    # Save
    out = REPO_ROOT / "data" / "ii25_ce_compare_report.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"records": rows, "total": n}, f, ensure_ascii=False, indent=2)
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()

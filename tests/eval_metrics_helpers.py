"""Pure helpers for eval accuracy metrics.

These functions mirror the metric rules currently embedded in eval_accuracy.py,
without importing the classifier or touching files.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


def code_prefix(code: str, level: int) -> str:
    """Return the first N dot-separated code parts."""
    return ".".join((code or "").split(".")[:level])


def any_expected_in_codes(
    candidate_codes: Sequence[str],
    expected_codes: Sequence[str] | None,
    top_k: int | None = None,
) -> bool | None:
    """Check whether any expected code is present in candidate codes."""
    expected = set(expected_codes or [])
    if not expected:
        return None
    scope = candidate_codes[:top_k] if top_k is not None else candidate_codes
    return any(code in expected for code in scope)


def first_expected_rank(
    candidate_codes: Sequence[str],
    expected_codes: Sequence[str] | None,
) -> int | None:
    """Return 1-based rank of the first expected code in candidates."""
    expected = set(expected_codes or [])
    for idx, code in enumerate(candidate_codes, start=1):
        if code in expected:
            return idx
    return None


def prefix_match(
    predicted_code: str | None,
    expected_codes: Sequence[str] | None,
    level: int,
) -> bool | None:
    """Compare predicted code prefix with any expected code prefix at a level."""
    if not predicted_code or not expected_codes:
        return None
    predicted_prefix = code_prefix(predicted_code, level)
    return any(predicted_prefix == code_prefix(expected, level) for expected in expected_codes)


def score_eval_case(
    *,
    agent_vid: str | None,
    agent_codes: Sequence[str],
    expected_vid: str | None = None,
    expected_codes: Sequence[str] | None = None,
    expected_prefix: str = "",
    expected_questions_count: int | None = None,
    dense_candidate_codes: Sequence[str] | None = None,
    reranked_candidate_codes: Sequence[str] | None = None,
    valid_codes: set[str] | None = None,
) -> dict[str, Any]:
    """Calculate per-case correctness flags used by the eval report."""
    expected_codes = list(expected_codes or [])
    top1_code = agent_codes[0] if agent_codes else None

    vid_correct = (agent_vid == expected_vid) if expected_vid else None
    top1_correct = any_expected_in_codes(agent_codes, expected_codes, top_k=1)
    top3_correct = any_expected_in_codes(agent_codes, expected_codes, top_k=3)
    top5_correct = any_expected_in_codes(agent_codes, expected_codes, top_k=5)
    prefix_correct = (
        top1_code.startswith(expected_prefix)
        if expected_prefix and top1_code
        else None
    )
    prefix_level1 = prefix_match(top1_code, expected_codes, level=1)
    prefix_level2 = prefix_match(top1_code, expected_codes, level=2)
    prefix_level3 = prefix_match(top1_code, expected_codes, level=3)
    questions_count_match = (
        len(agent_codes) == expected_questions_count
        if expected_questions_count is not None
        else None
    )
    dense_codes = list(dense_candidate_codes or [])
    reranked_codes = list(reranked_candidate_codes or [])

    metrics: dict[str, Any] = {
        "vid_correct": vid_correct,
        "top1_correct": top1_correct,
        "top3_correct": top3_correct,
        "top5_correct": top5_correct,
        "prefix_correct": prefix_correct,
        "prefix_level1": prefix_level1,
        "prefix_level2": prefix_level2,
        "prefix_level3": prefix_level3,
        "questions_count_match": questions_count_match,
        "retrieval_recall_at_10": any_expected_in_codes(dense_codes, expected_codes, top_k=10),
        "retrieval_recall_at_50": any_expected_in_codes(dense_codes, expected_codes, top_k=50),
        "reranked_recall_at_10": any_expected_in_codes(reranked_codes, expected_codes, top_k=10),
        "first_expected_rank_dense": first_expected_rank(dense_codes, expected_codes),
        "first_expected_rank_reranked": first_expected_rank(reranked_codes, expected_codes),
    }
    if valid_codes is not None:
        metrics["invalid_llm_codes"] = [code for code in agent_codes if code not in valid_codes]
    return metrics


def pct(numerator: int, denominator: int) -> str:
    """Format metric count the same way as eval_accuracy.print_summary."""
    return f"{numerator}/{denominator} = {numerator / denominator * 100:.1f}%" if denominator else "N/A"


def summarize_eval_results(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate eval result rows into counts and averages."""
    rows = list(results)
    ok_rows = [row for row in rows if row.get("status") == "OK"]

    def count_true(key: str) -> int:
        return sum(1 for row in ok_rows if row.get(key) is True)

    confidences = [row["confidence"] for row in ok_rows if row.get("confidence")]
    elapsed_values = [row["elapsed"] for row in ok_rows if row.get("elapsed")]

    return {
        "total": len(rows),
        "errors": sum(1 for row in rows if row.get("status") == "ERROR"),
        "ok": len(ok_rows),
        "vid_correct": count_true("vid_correct"),
        "top1_correct": count_true("top1_correct"),
        "top3_correct": count_true("top3_correct"),
        "top5_correct": count_true("top5_correct"),
        "prefix_correct": count_true("prefix_correct"),
        "prefix_level1": count_true("prefix_level1"),
        "prefix_level2": count_true("prefix_level2"),
        "prefix_level3": count_true("prefix_level3"),
        "retrieval_recall_at_10": count_true("retrieval_recall_at_10"),
        "retrieval_recall_at_50": count_true("retrieval_recall_at_50"),
        "reranked_recall_at_10": count_true("reranked_recall_at_10"),
        "needs_verification": sum(1 for row in ok_rows if row.get("needs_verification")),
        "invalid_llm_code_count": sum(len(row.get("invalid_llm_codes", [])) for row in ok_rows),
        "avg_confidence": sum(confidences) / len(ok_rows) if ok_rows else 0,
        "avg_time": sum(elapsed_values) / len(ok_rows) if ok_rows else 0,
    }


def attribute_failure(
    *,
    gold_codes: Sequence[str],
    dense_top50_codes: Sequence[str],
    reranked_top10_codes: Sequence[str],
    final_top1_code: str | None,
) -> str:
    """Categorize which pipeline stage failed.

    Phase 0 baseline categories (will be extended in Phase 1+ to include
    router_stage1, router_stage2):
      - correct: final top-1 prediction is in gold
      - llm_final: gold in reranked top-10, but LLM picked wrong code
      - reranker: gold in dense top-50, but reranker dropped it from top-10
      - retrieval_recall: gold not even in dense top-50
      - no_gold: gold_codes is empty (eval skip)

    Note: final_top1_code may be None when the LLM stage produced no answer;
    in that case the result falls through to retrieval/reranker categories.
    """
    if not gold_codes:
        return "no_gold"

    gold_set = set(gold_codes)
    if final_top1_code in gold_set:
        return "correct"
    if any(code in gold_set for code in reranked_top10_codes):
        return "llm_final"
    if any(code in gold_set for code in dense_top50_codes):
        return "reranker"
    return "retrieval_recall"

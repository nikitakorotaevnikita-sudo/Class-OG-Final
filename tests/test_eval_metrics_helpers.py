import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from eval_metrics_helpers import (
    any_expected_in_codes,
    first_expected_rank,
    pct,
    prefix_match,
    score_eval_case,
    summarize_eval_results,
)


def test_score_eval_case_marks_exact_top1_top3_and_prefix_matches():
    score = score_eval_case(
        agent_vid="complaint",
        agent_codes=["0005.0005.0056.1160", "0003.0009.0099.0742.0110"],
        expected_vid="complaint",
        expected_codes=["0005.0005.0056.1160"],
        expected_prefix="0005",
        expected_questions_count=2,
    )

    assert score["vid_correct"] is True
    assert score["top1_correct"] is True
    assert score["top3_correct"] is True
    assert score["top5_correct"] is True
    assert score["prefix_correct"] is True
    assert score["prefix_level1"] is True
    assert score["prefix_level2"] is True
    assert score["prefix_level3"] is True
    assert score["questions_count_match"] is True


def test_score_eval_case_allows_expected_code_in_top3_not_top1():
    score = score_eval_case(
        agent_vid="statement",
        agent_codes=[
            "0001.0002.0027.0124",
            "0003.0009.0099.0742.0110",
            "0005.0005.0056.1160",
        ],
        expected_vid="complaint",
        expected_codes=["0005.0005.0056.1160"],
        expected_prefix="0005",
    )

    assert score["vid_correct"] is False
    assert score["top1_correct"] is False
    assert score["top3_correct"] is True
    assert score["top5_correct"] is True
    assert score["prefix_correct"] is False
    assert score["questions_count_match"] is None


def test_score_eval_case_returns_none_for_missing_expectations_or_predictions():
    score = score_eval_case(
        agent_vid=None,
        agent_codes=[],
        expected_codes=[],
        expected_prefix="0005",
    )

    assert score["vid_correct"] is None
    assert score["top1_correct"] is None
    assert score["top3_correct"] is None
    assert score["top5_correct"] is None
    assert score["prefix_correct"] is None
    assert score["questions_count_match"] is None


def test_score_eval_case_reports_retrieval_rank_and_invalid_codes():
    score = score_eval_case(
        agent_vid="complaint",
        agent_codes=["9999.0000", "0005.0005.0056.1160"],
        expected_codes=["0005.0005.0056.1160"],
        dense_candidate_codes=["0001", "0005.0005.0056.1160"],
        reranked_candidate_codes=["0005.0005.0056.1160", "0001"],
        valid_codes={"0005.0005.0056.1160"},
    )

    assert score["retrieval_recall_at_10"] is True
    assert score["retrieval_recall_at_50"] is True
    assert score["reranked_recall_at_10"] is True
    assert score["first_expected_rank_dense"] == 2
    assert score["first_expected_rank_reranked"] == 1
    assert score["invalid_llm_codes"] == ["9999.0000"]


def test_rank_and_prefix_helpers_handle_absent_expectations():
    assert any_expected_in_codes(["0001"], [], top_k=10) is None
    assert first_expected_rank(["0001", "0002"], ["0003"]) is None
    assert prefix_match("0005.0005.0056.1160", ["0005.0007"], level=2) is False


def test_summarize_eval_results_counts_ok_rows_and_ignores_errors_for_metrics():
    summary = summarize_eval_results(
        [
            {
                "status": "OK",
                "vid_correct": True,
                "top1_correct": False,
                "top3_correct": True,
                "top5_correct": True,
                "prefix_correct": True,
                "prefix_level1": True,
                "prefix_level2": True,
                "prefix_level3": False,
                "retrieval_recall_at_10": False,
                "retrieval_recall_at_50": True,
                "reranked_recall_at_10": True,
                "invalid_llm_codes": [],
                "needs_verification": False,
                "confidence": 0.9,
                "elapsed": 1.0,
            },
            {
                "status": "OK",
                "vid_correct": False,
                "top1_correct": True,
                "top3_correct": True,
                "top5_correct": True,
                "prefix_correct": False,
                "prefix_level1": True,
                "prefix_level2": False,
                "prefix_level3": False,
                "retrieval_recall_at_10": True,
                "retrieval_recall_at_50": True,
                "reranked_recall_at_10": True,
                "invalid_llm_codes": ["bad-code"],
                "needs_verification": True,
                "confidence": 0.7,
                "elapsed": 3.0,
            },
            {"status": "ERROR", "error": "boom"},
        ]
    )

    assert summary["total"] == 3
    assert summary["errors"] == 1
    assert summary["ok"] == 2
    assert summary["vid_correct"] == 1
    assert summary["top1_correct"] == 1
    assert summary["top3_correct"] == 2
    assert summary["top5_correct"] == 2
    assert summary["prefix_correct"] == 1
    assert summary["prefix_level1"] == 2
    assert summary["prefix_level2"] == 1
    assert summary["prefix_level3"] == 0
    assert summary["retrieval_recall_at_10"] == 1
    assert summary["retrieval_recall_at_50"] == 2
    assert summary["reranked_recall_at_10"] == 2
    assert summary["invalid_llm_code_count"] == 1
    assert summary["needs_verification"] == 1
    assert summary["avg_confidence"] == 0.8
    assert summary["avg_time"] == 2.0


def test_summarize_eval_results_empty_input_returns_zeroes():
    summary = summarize_eval_results([])

    assert summary["total"] == 0
    assert summary["errors"] == 0
    assert summary["ok"] == 0
    assert summary["avg_confidence"] == 0
    assert summary["avg_time"] == 0


def test_pct_formats_counts_and_zero_denominator():
    assert pct(2, 3) == "2/3 = 66.7%"
    assert pct(0, 0) == "N/A"

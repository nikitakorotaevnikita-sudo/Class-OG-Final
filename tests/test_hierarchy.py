"""Tests for src/hierarchy.py — hierarchy-aware utilities."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hierarchy import (
    code_parts, prefix_at_level, parent_code, same_branch,
    branch_agreement_scores, parent_similarity_boost, dominant_l1_sections,
)


def test_code_parts():
    assert code_parts("0001.0002.0027.0124") == ["0001", "0002", "0027", "0124"]
    assert code_parts("") == [""]


def test_prefix_at_level():
    code = "0001.0002.0027.0124"
    assert prefix_at_level(code, 1) == "0001"
    assert prefix_at_level(code, 2) == "0001.0002"
    assert prefix_at_level(code, 3) == "0001.0002.0027"
    assert prefix_at_level(code, 4) == "0001.0002.0027.0124"


def test_parent_code():
    assert parent_code("0001.0002.0027.0124") == "0001.0002.0027"
    # Filler 0000 → parent поднимается выше
    assert parent_code("0001.0002.0000.0000") == "0001"


def test_same_branch():
    assert same_branch("0001.0002.0027.0124", "0001.0002.0027.0125", level=3)
    assert same_branch("0001.0002.0027.0124", "0001.0002.0028.0001", level=2)
    assert not same_branch("0001.0002.0027.0124", "0002.0007.0070.0281", level=1)


def test_branch_agreement_dense_branch():
    """Густая ветка (3 кандидата в одной L2) даёт высокий score, одиночка - низкий."""
    cands = [
        {"code": "0001.0002.0027.0124", "similarity": 0.8},
        {"code": "0001.0002.0027.0125", "similarity": 0.7},
        {"code": "0001.0002.0028.0001", "similarity": 0.6},  # та же L2 = 0001.0002
        {"code": "0003.0009.0099.0742", "similarity": 0.5},  # одиночка
    ]
    scores = branch_agreement_scores(cands, level=2)
    # Три кандидата из 0001.0002 — должны получить одинаковый высокий score
    s1 = scores["0001.0002.0027.0124"]
    s2 = scores["0001.0002.0027.0125"]
    s3 = scores["0001.0002.0028.0001"]
    assert s1 == s2 == s3
    s4 = scores["0003.0009.0099.0742"]
    assert s1 > s4
    # Сумма должна быть около 1.0 (нормировано на массу)
    # Точнее: s1*3 + s4 ≈ 1.0 (т.к. это доли)


def test_parent_similarity_boost_found_in_pool():
    """Если родитель кандидата в пуле — boost = его similarity."""
    cands = [
        {"code": "0001.0002.0027.0124", "similarity": 0.8, "parent_code": "0001.0002.0027.0000"},
        {"code": "0001.0002.0027.0000", "similarity": 0.6, "parent_code": "0001.0002.0000.0000"},  # parent
    ]
    boost = parent_similarity_boost(cands)
    assert boost["0001.0002.0027.0124"] == 0.6  # boost = similarity родителя в пуле
    # У родителя его собственный родитель не в пуле → 0
    assert boost["0001.0002.0027.0000"] == 0.0


def test_parent_similarity_boost_no_parent():
    """Если родителя нет в пуле — boost 0."""
    cands = [
        {"code": "0001.0002.0027.0124", "similarity": 0.8, "parent_code": "0001.0002.0027.0000"},
        {"code": "0003.0009.0099.0742", "similarity": 0.7, "parent_code": "0003.0009.0099.0000"},
    ]
    boost = parent_similarity_boost(cands)
    assert boost["0001.0002.0027.0124"] == 0.0
    assert boost["0003.0009.0099.0742"] == 0.0


def test_dominant_l1_sections_single_dominant():
    cands = [
        {"code": "0002.0007.0070.0281", "similarity": 0.9},
        {"code": "0002.0007.0073.0295", "similarity": 0.85},
        {"code": "0002.0014.0143.0422", "similarity": 0.8},
        {"code": "0003.0009.0099.0742", "similarity": 0.4},  # outlier
    ]
    dominant = dominant_l1_sections(cands, threshold=0.60)
    assert "0002" in dominant
    # Сумма 0002 = 0.9+0.85+0.8 = 2.55, общая = 2.95 → 86% > 60% — только 0002


def test_dominant_l1_sections_no_pruning_if_all_sections():
    """Если кандидаты равномерно распределены — pruning не нужен."""
    cands = [
        {"code": "0001.0002.0027.0124", "similarity": 0.8},
        {"code": "0002.0007.0070.0281", "similarity": 0.8},
        {"code": "0003.0009.0099.0742", "similarity": 0.8},
    ]
    # Каждый раздел = 33%, threshold 60% → нужны 2 раздела (33+33=66)
    # Но если 2 из 3 — это всё ещё pruning. Однако если все 3 нужны → пустой set
    dominant = dominant_l1_sections(cands, threshold=0.99)
    # При threshold ~1.0 — все 3 нужны, pruning не имеет смысла → пустой set
    assert dominant == set()


def test_branch_agreement_handles_empty():
    assert branch_agreement_scores([]) == {}


def test_branch_agreement_handles_zero_similarity():
    cands = [
        {"code": "0001.0002.0027.0124", "similarity": 0.0},
        {"code": "0002.0007.0070.0281", "similarity": 0.0},
    ]
    scores = branch_agreement_scores(cands, level=2)
    assert all(v == 0.0 for v in scores.values())

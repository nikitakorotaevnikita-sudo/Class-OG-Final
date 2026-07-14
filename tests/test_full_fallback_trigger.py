"""Триггер full-classifier fallback: детект промаха ретривера по max similarity."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import ClassifierAgent

_max_sim = ClassifierAgent._max_candidate_similarity


def test_max_similarity_across_questions():
    qwc = [
        {"candidates": [{"similarity": 0.31}, {"similarity": 0.24}]},
        {"candidates": [{"similarity": 0.42}, {"similarity": 0.38}]},
    ]
    assert _max_sim(qwc) == 0.42


def test_empty_candidates_returns_zero():
    assert _max_sim([{"candidates": []}]) == 0.0
    assert _max_sim([]) == 0.0


def test_missing_similarity_field_treated_as_zero():
    qwc = [{"candidates": [{"code": "x"}, {"similarity": 0.1}]}]
    assert _max_sim(qwc) == 0.1


def test_lexical_candidates_ignored():
    # Лексические кандидаты (синтетическая similarity ~0.76) НЕ должны учитываться —
    # иначе промах ретривера маскируется и fallback не срабатывает.
    qwc = [{"candidates": [
        {"similarity": 0.31, "source": "dense"},
        {"similarity": 0.80, "source": "lexical"},
    ]}]
    assert _max_sim(qwc) == 0.31


def test_miss_below_threshold_hit_above():
    # Промах (инсулин): max sim 0.31 < 0.40 → должен сработать fallback.
    miss = [{"candidates": [{"similarity": 0.31}]}]
    # Попадание (мусор): max sim 0.42 >= 0.40 → fallback НЕ нужен.
    hit = [{"candidates": [{"similarity": 0.42}]}]
    threshold = 0.40
    assert _max_sim(miss) < threshold
    assert _max_sim(hit) >= threshold

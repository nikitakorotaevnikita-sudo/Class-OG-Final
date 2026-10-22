"""Обогащение запроса перед поиском: LLM даёт суть, ключевые фразы и синонимы.

Поиск промахивается мимо темы, когда гражданин пишет бытовыми словами, а в
классификаторе — канцелярские. Обогащение переводит вопрос на язык
классификатора; по нему (вместе с текстом или вместо него) и ищутся кандидаты.
Модели на классификацию по-прежнему уходит исходный текст вопроса.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as classifier_mod
from classifier_agent import ClassifierAgent
from query_enrichment import (
    enrichment_text,
    normalize_mode,
    parse_enrichment,
    retrieval_inputs,
)

RAW = """```json
{"summary": "Не выплачены деньги за участие в боевых действиях.",
 "key_phrases": ["выплаты участникам СВО", "ветеран боевых действий"],
 "synonyms": ["единовременная выплата", "социальная поддержка военнослужащих"]}
```"""


def test_parses_a_fenced_json_answer():
    enr = parse_enrichment(RAW)
    assert enr["summary"] == "Не выплачены деньги за участие в боевых действиях."
    assert enr["key_phrases"] == ["выплаты участникам СВО", "ветеран боевых действий"]
    assert enr["synonyms"][0] == "единовременная выплата"


def test_garbage_or_empty_answer_gives_nothing():
    assert parse_enrichment("не знаю") is None
    assert parse_enrichment('{"summary": "", "key_phrases": [], "synonyms": []}') is None
    assert parse_enrichment("") is None


def test_enrichment_becomes_one_search_string():
    text = enrichment_text(parse_enrichment(RAW))
    assert text.startswith("Не выплачены деньги за участие в боевых действиях.")
    assert "выплаты участникам СВО, ветеран боевых действий" in text
    assert "единовременная выплата" in text
    assert enrichment_text(None) == ""


def test_modes():
    assert normalize_mode("APPEND") == "append"
    assert normalize_mode("replace") == "replace"
    assert normalize_mode("") == "off"
    assert normalize_mode("что-то") == "off"


def test_retrieval_inputs_per_mode():
    enr = parse_enrichment(RAW)
    extra = enrichment_text(enr)
    assert retrieval_inputs("вопрос", enr, "off") == ("вопрос", "")
    assert retrieval_inputs("вопрос", enr, "append") == ("вопрос", extra)
    assert retrieval_inputs("вопрос", enr, "replace") == (extra, "")


def test_failed_enrichment_falls_back_to_the_question_itself():
    """Сбой LLM не должен оставить поиск без запроса."""
    assert retrieval_inputs("вопрос", None, "replace") == ("вопрос", "")
    assert retrieval_inputs("вопрос", None, "append") == ("вопрос", "")


# ── Встраивание в classify ──────────────────────────────────────────────────


class _FakeLogger:
    def log(self, **_kwargs):
        return "fake-log-id"


CANDIDATE = {"code": "0004.0015.0158.0974", "name": "Выплаты", "level": 4,
             "full_path": "a / b", "similarity": 0.6}


def _agent(monkeypatch, mode: str, searched: list):
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = "ario"
    agent.code_index = {CANDIDATE["code"]: dict(CANDIDATE)}
    monkeypatch.setattr(classifier_mod, "QUERY_ENRICHMENT", mode)
    monkeypatch.setattr(agent, "_resolve_llm", lambda *_a, **_k: ("ario", "m"))
    monkeypatch.setattr(agent, "_log_request", lambda *_a, **_k: None)
    monkeypatch.setattr(classifier_mod, "get_logger", lambda: _FakeLogger())
    monkeypatch.setattr(agent, "_enrich_query",
                        lambda text, provider, model: parse_enrichment(RAW))

    def fake_retrieve(segment_text, expanded="", **_kw):
        searched.append((segment_text, expanded))
        return [dict(CANDIDATE)]

    seen_by_llm = []

    def fake_llm(appeal_text, questions_with_candidates, *_a, **_k):
        seen_by_llm.extend(q["question_text"] for q in questions_with_candidates)
        return {"vid_obrascheniya": "Заявление", "tip_obrascheniya": "Индивидуальное",
                "questions": [{"ordinal": 1, "question_text": "вопрос",
                               "selected_code": CANDIDATE["code"], "confidence": 0.9,
                               "reasoning": "r", "alternative_codes": []}]}

    monkeypatch.setattr(agent, "_retrieve_for_segment", fake_retrieve)
    monkeypatch.setattr(agent, "_classify_with_llm", fake_llm)
    return agent, seen_by_llm


def test_replace_searches_by_enrichment_but_llm_sees_the_question(monkeypatch):
    searched = []
    agent, seen_by_llm = _agent(monkeypatch, "replace", searched)
    result = agent.classify("Муж вернулся, выплаты не получены.")
    extra = enrichment_text(parse_enrichment(RAW))
    assert searched == [(extra, "")]
    assert seen_by_llm == ["Муж вернулся, выплаты не получены."]
    assert result.query_enrichments[1]["summary"].startswith("Не выплачены")
    assert result.retrieval_candidates[1] == [CANDIDATE["code"]]


def test_append_adds_enrichment_to_the_question(monkeypatch):
    searched = []
    agent, _ = _agent(monkeypatch, "append", searched)
    agent.classify("Муж вернулся, выплаты не получены.")
    assert searched == [("Муж вернулся, выплаты не получены.",
                         enrichment_text(parse_enrichment(RAW)))]


def test_off_makes_no_enrichment_call(monkeypatch):
    searched = []
    agent, _ = _agent(monkeypatch, "off", searched)

    def boom(*_a, **_k):
        raise AssertionError("при off вызова быть не должно")

    monkeypatch.setattr(agent, "_enrich_query", boom)
    result = agent.classify("Муж вернулся, выплаты не получены.")
    assert searched == [("Муж вернулся, выплаты не получены.", "")]
    assert result.query_enrichments == {}


def test_fallback_trigger_is_reported(monkeypatch):
    """Для отчёта нужен не только факт фоллбэка, но и что его вызвало."""
    searched = []
    agent, _ = _agent(monkeypatch, "off", searched)
    monkeypatch.setattr(classifier_mod, "ENABLE_FULL_CLASSIFIER_FALLBACK", True)
    monkeypatch.setattr(classifier_mod, "FULL_FALLBACK_SIM_THRESHOLD", 0.99)
    monkeypatch.setattr(agent, "_classify_with_full_classifier", lambda *_a, **_k: None)
    result = agent.classify("Муж вернулся, выплаты не получены.")
    assert result.fallback_trigger.startswith("max sim")

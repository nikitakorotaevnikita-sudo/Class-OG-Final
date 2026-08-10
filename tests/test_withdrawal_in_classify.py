"""classify() добавляет код «Прекращение рассмотрения обращения» при просьбе отозвать."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as classifier_mod
from classifier_agent import ClassifierAgent

WITHDRAWAL_CODE = "0001.0002.0027.0131"


class _FakeLogger:
    def log(self, **_kwargs):
        return "fake-log-id"


def _candidate(code, name="кандидат", similarity=0.9):
    return {"code": code, "name": name, "level": 4,
            "full_path": f"путь / {name}", "similarity": similarity}


def _agent(monkeypatch, llm_questions, candidates, llm_extra=None):
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = "ario"
    agent.code_index = {
        c["code"]: {"code": c["code"], "name": c["name"],
                    "level": c["level"], "full_path": c["full_path"]}
        for c in candidates
    }
    agent.code_index[WITHDRAWAL_CODE] = {
        "code": WITHDRAWAL_CODE, "name": "Прекращение рассмотрения обращения",
        "level": 4, "full_path": "Государство / обращения / прекращение рассмотрения",
    }
    monkeypatch.setattr(agent, "_resolve_llm", lambda *a, **k: ("ario", "test-model"))
    monkeypatch.setattr(agent, "_route_to_sections", lambda *a, **k: ["0005.0005"])
    monkeypatch.setattr(agent, "_retrieve_for_segment", lambda *a, **k: candidates)
    monkeypatch.setattr(agent, "_log_request", lambda *a, **k: None)
    monkeypatch.setattr(classifier_mod, "get_logger", lambda: _FakeLogger())
    monkeypatch.setattr(agent, "_classify_with_llm", lambda *a, **k: {
        "vid_obrascheniya": "Заявление", "tip_obrascheniya": "Индивидуальное",
        "is_ustnoe": False, "questions": llm_questions, **(llm_extra or {}),
    })
    return agent


def _q(code, text="вопрос"):
    return {"ordinal": 1, "question_text": text, "selected_code": code,
            "confidence": 0.6, "reasoning": "обоснование", "alternative_codes": []}


def test_withdrawal_request_adds_code(monkeypatch):
    monkeypatch.setattr(classifier_mod, "ENABLE_WITHDRAWAL_DETECTION", True)
    topical = "0005.0005.0056.1160"
    agent = _agent(monkeypatch, [_q(topical)], [_candidate(topical, "Вывоз ТКО")])

    result = agent.classify("Прошу прекратить рассмотрение моего обращения, вопрос решён")

    codes = [q.code for q in result.questions]
    assert WITHDRAWAL_CODE in codes
    assert topical in codes, "тематическая классификация должна сохраниться"


def test_no_withdrawal_no_extra_code(monkeypatch):
    monkeypatch.setattr(classifier_mod, "ENABLE_WITHDRAWAL_DETECTION", True)
    topical = "0005.0005.0056.1160"
    agent = _agent(monkeypatch, [_q(topical)], [_candidate(topical, "Вывоз ТКО")])

    result = agent.classify("Не вывозят мусор две недели, контейнеры переполнены")

    assert WITHDRAWAL_CODE not in [q.code for q in result.questions]


def test_disabled_flag_adds_nothing(monkeypatch):
    monkeypatch.setattr(classifier_mod, "ENABLE_WITHDRAWAL_DETECTION", False)
    topical = "0005.0005.0056.1160"
    agent = _agent(monkeypatch, [_q(topical)], [_candidate(topical, "Вывоз ТКО")])

    result = agent.classify("Прошу отозвать моё обращение")

    assert WITHDRAWAL_CODE not in [q.code for q in result.questions]


def test_llm_flag_triggers_without_regex_markers(monkeypatch):
    """LLM распознала просьбу отозвать, даже если явных маркеров нет."""
    monkeypatch.setattr(classifier_mod, "ENABLE_WITHDRAWAL_DETECTION", True)
    topical = "0005.0005.0056.1160"
    agent = _agent(monkeypatch, [_q(topical)], [_candidate(topical, "Вывоз ТКО")],
                   llm_extra={"is_withdrawal_request": True})

    result = agent.classify("Вопрос уже урегулирован, дальнейших действий не требуется")

    assert WITHDRAWAL_CODE in [q.code for q in result.questions]

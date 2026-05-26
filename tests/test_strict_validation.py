import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as classifier_mod
from classifier_agent import ClassifierAgent


class _FakeLogger:
    def log(self, **_kwargs):
        return "fake-log-id"


def _candidate(code: str, name: str = "candidate", similarity: float = 0.9) -> dict:
    return {
        "code": code,
        "name": name,
        "level": 4,
        "full_path": f"path / {name}",
        "similarity": similarity,
    }


def _agent(monkeypatch, llm_question: dict, candidates: list[dict]) -> ClassifierAgent:
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = "ario"
    agent.code_index = {
        c["code"]: {
            "code": c["code"],
            "name": c["name"],
            "level": c["level"],
            "full_path": c["full_path"],
        }
        for c in candidates
    }
    agent.code_index["0001.0002.0027.0145"] = {
        "code": "0001.0002.0027.0145",
        "name": "Личный прием",
        "level": 4,
        "full_path": "Государство / обращения / личный прием",
    }

    monkeypatch.setattr(agent, "_resolve_llm", lambda *_args, **_kwargs: ("ario", "test-model"))
    monkeypatch.setattr(agent, "_route_to_sections", lambda *_args, **_kwargs: ["0004.0019"])
    monkeypatch.setattr(agent, "_retrieve_for_segment", lambda *_args, **_kwargs: candidates)
    monkeypatch.setattr(agent, "_log_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(classifier_mod, "get_logger", lambda: _FakeLogger())

    def fake_classify_with_llm(*_args, **_kwargs):
        return {
            "vid_obrascheniya": "Жалоба",
            "tip_obrascheniya": "Индивидуальное",
            "is_ustnoe": False,
            "questions": [llm_question],
        }

    monkeypatch.setattr(agent, "_classify_with_llm", fake_classify_with_llm)
    return agent


def test_strict_validation_replaces_code_outside_question_candidates(monkeypatch):
    candidates = [
        _candidate("0004.0019.0178.1098", "Прокуратура", 0.9),
        _candidate("0004.0019.0179.1112", "Нотариат", 0.7),
    ]
    agent = _agent(
        monkeypatch,
        llm_question={
            "ordinal": 1,
            "question_text": "жалоба на прокуратуру",
            "selected_code": "0001.0002.0027.0145",
            "confidence": 0.92,
            "reasoning": "выбрано из-за обращения Президенту",
            "alternative_codes": [],
        },
        candidates=candidates,
    )

    result = agent.classify("Уважаемый Президент, жалуюсь на прокуратуру")

    assert result.questions[0].code == "0004.0019.0178.1098"
    assert result.questions[0].confidence == 0.45
    assert result.needs_verification is True
    assert "вне кандидатов" in result.questions[0].reasoning


def test_strict_validation_keeps_valid_candidate_code(monkeypatch):
    candidates = [
        _candidate("0004.0019.0178.1098", "Прокуратура", 0.9),
        _candidate("0004.0019.0179.1112", "Нотариат", 0.7),
    ]
    agent = _agent(
        monkeypatch,
        llm_question={
            "ordinal": 1,
            "question_text": "жалоба на прокуратуру",
            "selected_code": "0004.0019.0178.1098",
            "confidence": 0.92,
            "reasoning": "код есть в кандидатах",
            "alternative_codes": [],
        },
        candidates=candidates,
    )

    result = agent.classify("Жалуюсь на прокуратуру")

    assert result.questions[0].code == "0004.0019.0178.1098"
    assert result.questions[0].confidence == 0.92
    assert result.needs_verification is False


def test_calibration_flags_non_top_candidate_for_verification(monkeypatch):
    candidates = [
        _candidate("0004.0019.0178.1098", "Прокуратура", 0.95),
        _candidate("0004.0019.0179.1112", "Нотариат", 0.90),
    ]
    agent = _agent(
        monkeypatch,
        llm_question={
            "ordinal": 1,
            "question_text": "жалоба на прокуратуру",
            "selected_code": "0004.0019.0179.1112",
            "confidence": 0.92,
            "reasoning": "выбран второй кандидат",
            "alternative_codes": [],
        },
        candidates=candidates,
    )

    result = agent.classify("Жалуюсь на прокуратуру")

    assert result.questions[0].code == "0004.0019.0179.1112"
    assert result.questions[0].confidence <= 0.62
    assert result.needs_verification is True
    assert "selected_candidate_rank_2" in result.questions[0].reasoning

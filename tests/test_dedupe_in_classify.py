"""classify() не должен возвращать несколько карточек с одним и тем же кодом."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as classifier_mod
from classifier_agent import ClassifierAgent


class _FakeLogger:
    def log(self, **_kwargs):
        return "fake-log-id"


def _candidate(code, name="кандидат", similarity=0.9):
    return {"code": code, "name": name, "level": 4,
            "full_path": f"путь / {name}", "similarity": similarity}


def _agent(monkeypatch, llm_questions, candidates):
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = "ario"
    agent.code_index = {
        c["code"]: {"code": c["code"], "name": c["name"],
                    "level": c["level"], "full_path": c["full_path"]}
        for c in candidates
    }
    monkeypatch.setattr(agent, "_resolve_llm", lambda *a, **k: ("ario", "test-model"))
    monkeypatch.setattr(agent, "_route_to_sections", lambda *a, **k: ["0003.0011"])
    monkeypatch.setattr(agent, "_retrieve_for_segment", lambda *a, **k: candidates)
    monkeypatch.setattr(agent, "_log_request", lambda *a, **k: None)
    monkeypatch.setattr(classifier_mod, "get_logger", lambda: _FakeLogger())
    monkeypatch.setattr(agent, "_classify_with_llm", lambda *a, **k: {
        "vid_obrascheniya": "Заявление", "tip_obrascheniya": "Индивидуальное",
        "is_ustnoe": False, "questions": llm_questions,
    })
    return agent


def _q(ordinal, code, text, conf=0.5):
    return {"ordinal": ordinal, "question_text": text, "selected_code": code,
            "confidence": conf, "reasoning": f"обоснование {ordinal}",
            "alternative_codes": []}


def test_classify_collapses_repeated_code_into_one_question(monkeypatch):
    """Кейс из ОС Заказчика: одно обращение -> 3 карточки с одним кодом."""
    code = "0003.0011.0123.0850"
    agent = _agent(
        monkeypatch,
        llm_questions=[
            _q(1, code, "про отказ в аукционе"),
            _q(2, code, "про действительность аренды"),
            _q(3, code, "про разъяснение причины отказа"),
        ],
        candidates=[_candidate(code, "Арендные отношения в области землепользования")],
    )

    result = agent.classify("Прошу дать разъяснения о причине отказа в проведении аукциона")

    assert len(result.questions) == 1
    assert result.questions[0].code == code
    assert "merged_duplicate_questions" in result.questions[0].verification_reasons


def test_classify_keeps_genuinely_different_codes(monkeypatch):
    """Разные коды не схлопываются."""
    c1, c2 = "0003.0011.0123.0850", "0003.0011.0123.0846"
    agent = _agent(
        monkeypatch,
        llm_questions=[_q(1, c1, "про аренду"), _q(2, c2, "про приватизацию")],
        candidates=[_candidate(c1, "Арендные отношения"), _candidate(c2, "Приватизация ЗУ")],
    )

    result = agent.classify("Два разных вопроса в одном обращении")

    assert [q.code for q in result.questions] == [c1, c2]

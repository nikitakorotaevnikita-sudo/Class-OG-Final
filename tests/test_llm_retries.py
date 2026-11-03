"""Число попыток обращения к LLM задаётся настройкой LLM_MAX_RETRIES.

Было зашито числом 5 в основном вызове, а истёкший таймаут не повторялся вовсе:
один долгий ответ модели — и обращение падало с «LLM недоступен». Фолбэк по
полному классификатору ходил в модель один раз.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import pytest

import classifier_agent as ca
from classifier_agent import ClassifierAgent

ROOT = Path(__file__).parent.parent
GOOD = '{"vid_obrascheniya": "Заявление", "questions": [{"ordinal": 1, "selected_code": "A"}]}'
QUESTIONS = [{"ordinal": 1, "question_text": "вопрос",
              "candidates": [{"code": "0001.0001.0001.0001", "name": "n", "level": 4}]}]


def _agent(monkeypatch, answers: list):
    """Агент, у которого _ario_call по очереди отдаёт ответы или бросает исключения."""
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = "ario"
    agent.code_index = {}
    agent.metadata = [{"code": "0001.0001.0001.0001", "name": "n", "level": 4}]
    calls = []

    def fake_call(**_kwargs):
        calls.append(1)
        item = answers[min(len(calls), len(answers)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(agent, "_resolve_llm", lambda *_a, **_k: ("ario", "m"))
    monkeypatch.setattr(agent, "_ario_call", fake_call)
    monkeypatch.setattr(agent, "_log_request", lambda *_a, **_k: None)
    monkeypatch.setattr(ca.time, "sleep", lambda _s: None)
    return agent, calls


def test_setting_has_a_default_and_is_documented():
    config = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("LLM_MAX_RETRIES", "5")' in config
    assert "LLM_MAX_RETRIES=5" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_no_hardcoded_retry_count_left():
    source = (ROOT / "src" / "classifier_agent.py").read_text(encoding="utf-8")
    assert "max_retries = 5" not in source


def test_main_call_makes_as_many_attempts_as_configured(monkeypatch):
    monkeypatch.setattr(ca, "LLM_MAX_RETRIES", 3)
    agent, calls = _agent(monkeypatch, [httpx.ReadTimeout("timed out")])
    with pytest.raises(RuntimeError, match="после 3 попыток"):
        agent._classify_with_llm("текст", QUESTIONS)
    assert len(calls) == 3


def test_timeout_is_retried_on_the_main_call(monkeypatch):
    monkeypatch.setattr(ca, "LLM_MAX_RETRIES", 5)
    agent, calls = _agent(monkeypatch, [httpx.ReadTimeout("timed out"), GOOD])
    result = agent._classify_with_llm("текст", QUESTIONS)
    assert result["vid_obrascheniya"] == "Заявление"
    assert len(calls) == 2


def test_fallback_is_retried_too(monkeypatch):
    monkeypatch.setattr(ca, "LLM_MAX_RETRIES", 3)
    agent, calls = _agent(monkeypatch, [httpx.ReadTimeout("timed out"),
                                        RuntimeError("LLM HTTP 503 Service Unavailable"),
                                        GOOD])
    result = agent._classify_with_full_classifier("текст")
    assert result["questions"][0]["selected_code"] == "A"
    assert len(calls) == 3


def test_fallback_gives_up_after_the_configured_attempts(monkeypatch):
    monkeypatch.setattr(ca, "LLM_MAX_RETRIES", 2)
    agent, calls = _agent(monkeypatch, [httpx.ReadTimeout("timed out")])
    with pytest.raises(httpx.ReadTimeout):
        agent._classify_with_full_classifier("текст")
    assert len(calls) == 2


def test_fallback_does_not_retry_a_permanent_error(monkeypatch):
    """404 «нет такой модели» повтором не лечится — сразу наверх."""
    monkeypatch.setattr(ca, "LLM_MAX_RETRIES", 5)
    agent, calls = _agent(monkeypatch, [RuntimeError("LLM HTTP 404 model does not exist")])
    with pytest.raises(RuntimeError, match="404"):
        agent._classify_with_full_classifier("текст")
    assert len(calls) == 1

"""Таймаут ожидания ответа модели задаётся одной настройкой для всех провайдеров.

У клиента `ollama` он был зашит числом 120, тогда как `LLM_TIMEOUT_SEC` по
умолчанию 300. На стенде Заказчика модель подключена именно через провайдера
`ollama` (транспорт OpenAI-совместимый), то есть правка настройки там ничего не
меняла, а длинные многовопросные обращения обрывались на 120-й секунде.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import pytest

import classifier_agent as ca
from classifier_agent import ClassifierAgent


class _FakeClient:
    created: list[dict] = []

    def __init__(self, **kwargs):
        _FakeClient.created.append(kwargs)


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    _FakeClient.created = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    yield


def test_ollama_client_honours_the_configured_timeout(monkeypatch):
    monkeypatch.setattr(ca, "LLM_TIMEOUT_SEC", 300)
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent._get_ollama_client()
    assert _FakeClient.created[0]["timeout"] == 300


def test_raising_the_setting_raises_the_ollama_timeout(monkeypatch):
    """Иначе на стенде настройка есть, а эффекта от неё нет."""
    monkeypatch.setattr(ca, "LLM_TIMEOUT_SEC", 600)
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent._get_ollama_client()
    assert _FakeClient.created[0]["timeout"] == 600


def test_no_hardcoded_timeout_left_for_ollama():
    """Второй такой клиент создаётся в __init__ — там было то же число."""
    source = (Path(__file__).parent.parent / "src" / "classifier_agent.py").read_text(
        encoding="utf-8")
    assert "timeout=120" not in source, "остался зашитый таймаут"

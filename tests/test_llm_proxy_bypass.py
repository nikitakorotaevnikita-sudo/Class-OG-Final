"""Модель во внутренней сети запрашивается мимо системного прокси.

Та же причина, что и у RX: прокси из окружения настроен для интернета, и
внутренний endpoint (vLLM Заказчика, локальная Ollama) через него недоступен.
Разница в том, что у LLM адрес бывает и внешним — Ario, Groq: там прокси,
наоборот, может быть единственным путём в интернет. Поэтому решает адрес, а не
общий выключатель; продавить можно настройкой `LLM_VIA_PROXY`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import pytest

import classifier_agent as ca
import config
import llm_check
from classifier_agent import ClassifierAgent


class _FakeClient:
    created: list[dict] = []

    def __init__(self, **kwargs):
        _FakeClient.created.append(kwargs)

    def post(self, *a, **k):
        class _R:
            status_code = 200
            text = "{}"

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "{}"}}]}

            @staticmethod
            def raise_for_status():
                return None

        return _R()

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    _FakeClient.created = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setattr(ca, "LLM_VIA_PROXY", None, raising=False)
    yield


def _agent(provider: str) -> ClassifierAgent:
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = provider
    return agent


def _call(provider: str) -> dict:
    _agent(provider)._ario_call(provider=provider,
                                messages=[{"role": "user", "content": "x"}])
    return _FakeClient.created[0]


def test_customer_endpoint_in_the_internal_network_goes_direct(monkeypatch):
    monkeypatch.setattr(ca, "CUSTOM_LLM_BASE_URL", "http://10.112.103.34/v1")
    monkeypatch.setattr(ca, "CUSTOM_LLM_API_KEY", "")
    monkeypatch.setattr(ca, "CUSTOM_LLM_MODEL", "openai/gpt-oss-20b")
    assert _call("custom")["trust_env"] is False


def test_ario_keeps_the_env_proxy(monkeypatch):
    """Ario — внешний адрес: где нет интернета без прокси, он нужен."""
    monkeypatch.setattr(ca, "ARIO_BASE_URL", "https://llm.ario.directum360.ru/v1")
    monkeypatch.setattr(ca, "ARIO_API_KEY", "k")
    monkeypatch.setattr(ca, "ARIO_MODEL", "Qwen/Qwen3.8-27B-Ario")
    assert _call("ario")["trust_env"] is True


def test_setting_can_force_the_proxy_for_an_internal_endpoint(monkeypatch):
    monkeypatch.setattr(ca, "CUSTOM_LLM_BASE_URL", "http://10.112.103.34/v1")
    monkeypatch.setattr(ca, "CUSTOM_LLM_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(ca, "LLM_VIA_PROXY", True, raising=False)
    assert _call("custom")["trust_env"] is True


def test_ollama_client_goes_direct(monkeypatch):
    monkeypatch.setattr(ca, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent._get_ollama_client()
    assert _FakeClient.created[0]["trust_env"] is False


# ── Кнопка проверки связи ───────────────────────────────────────────────────

def test_connection_check_follows_the_same_rule(monkeypatch):
    asked = {}

    def fake_get(url, **kwargs):
        asked.update(kwargs, url=url)

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"data": []}

        return _R()

    monkeypatch.setattr(llm_check.httpx, "get", fake_get)

    llm_check.check_connection(base_url="http://10.112.103.34/v1")
    assert asked["trust_env"] is False, "внутренний адрес — напрямую"

    llm_check.check_connection(base_url="https://llm.ario.directum360.ru/v1")
    assert asked["trust_env"] is True, "внешний — как настроено в окружении"


def test_setting_defaults_to_deciding_by_address():
    assert config.LLM_VIA_PROXY is None

"""Произвольный OpenAI-совместимый endpoint как отдельный провайдер.

У Заказчика своя модель (например gpt-oss-20b), поднятая через vLLM/Ollama/LM Studio.
Раньше её можно было подключить только подменив адрес и ключ у провайдера `ario`,
что путает: в настройках написано «Ario», а ходим совсем в другое место.

Теперь есть провайдер `custom` со своими полями и кнопкой проверки связи.
Транспорт тот же (POST {base}/chat/completions с Bearer-токеном).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as ca
import llm_check
import settings_store
from classifier_agent import ClassifierAgent


def _agent():
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = "custom"
    return agent


# ── Провайдер в агенте ──────────────────────────────────────────────────────

def test_resolve_llm_knows_custom_provider(monkeypatch):
    monkeypatch.setattr(ca, "CUSTOM_LLM_MODEL", "gpt-oss-20b")
    provider, model = _agent()._resolve_llm()
    assert provider == "custom"
    assert model == "gpt-oss-20b"


def test_explicit_model_overrides_default(monkeypatch):
    monkeypatch.setattr(ca, "CUSTOM_LLM_MODEL", "gpt-oss-20b")
    _, model = _agent()._resolve_llm("custom", "gpt-oss-120b")
    assert model == "gpt-oss-120b"


def test_endpoint_for_custom_uses_custom_settings(monkeypatch):
    monkeypatch.setattr(ca, "CUSTOM_LLM_BASE_URL", "http://stand.local/v1")
    monkeypatch.setattr(ca, "CUSTOM_LLM_API_KEY", "custom-key")
    base, key = _agent()._openai_endpoint("custom")
    assert base == "http://stand.local/v1"
    assert key == "custom-key"


def test_endpoint_for_ario_stays_ario(monkeypatch):
    monkeypatch.setattr(ca, "ARIO_BASE_URL", "https://llm.ario.directum360.ru/v1")
    monkeypatch.setattr(ca, "ARIO_API_KEY", "ario-key")
    base, key = _agent()._openai_endpoint("ario")
    assert base == "https://llm.ario.directum360.ru/v1"
    assert key == "ario-key"


def test_unknown_provider_still_rejected():
    try:
        _agent()._resolve_llm("nonexistent")
    except ValueError as exc:
        assert "nonexistent" in str(exc)
    else:
        raise AssertionError("должно быть ValueError")


# ── Настройки ───────────────────────────────────────────────────────────────

def test_custom_is_allowed_provider():
    assert "custom" in settings_store.LLM_PROVIDERS


def test_settings_expose_custom_fields():
    keys = set(settings_store.FIELDS_BY_KEY)
    assert {"CUSTOM_LLM_BASE_URL", "CUSTOM_LLM_MODEL", "CUSTOM_LLM_API_KEY"} <= keys


def test_custom_api_key_is_secret():
    assert settings_store.FIELDS_BY_KEY["CUSTOM_LLM_API_KEY"]["kind"] == "secret"


# ── Проверка связи ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def test_check_reports_models_on_success(monkeypatch):
    payload = {"data": [{"id": "gpt-oss-20b"}, {"id": "other"}]}
    monkeypatch.setattr(llm_check.httpx, "get", lambda *a, **k: _Resp(200, payload))
    res = llm_check.check_connection(base_url="http://stand.local/v1", api_key="k")
    assert res["ok"] is True
    assert "gpt-oss-20b" in res["models"]


def test_check_warns_when_model_absent(monkeypatch):
    payload = {"data": [{"id": "llama-3"}]}
    monkeypatch.setattr(llm_check.httpx, "get", lambda *a, **k: _Resp(200, payload))
    res = llm_check.check_connection(base_url="http://x/v1", api_key="k", model="gpt-oss-20b")
    assert res["ok"] is True
    assert "gpt-oss-20b" in res["detail"], "должно быть сказано, что модели нет в списке"


def test_check_reports_http_error(monkeypatch):
    monkeypatch.setattr(llm_check.httpx, "get", lambda *a, **k: _Resp(401, {"error": "no"}))
    res = llm_check.check_connection(base_url="http://x/v1", api_key="bad")
    assert res["ok"] is False
    assert "401" in res["detail"]


def test_check_reports_network_failure(monkeypatch):
    def boom(*a, **k):
        raise llm_check.httpx.ConnectError("no route")
    monkeypatch.setattr(llm_check.httpx, "get", boom)
    res = llm_check.check_connection(base_url="http://unreachable/v1", api_key="k")
    assert res["ok"] is False
    assert "ConnectError" in res["detail"] or "no route" in res["detail"]


def test_check_requires_base_url():
    res = llm_check.check_connection(base_url="", api_key="k")
    assert res["ok"] is False

"""Кнопка «Проверить связь с LLM» проверяет активный провайдер.

Дефект, из-за которого появился этот файл: проверка всегда читала
`CUSTOM_LLM_*`, независимо от `LLM_PROVIDER`. На стенде и локально провайдер —
`ario`, поля `custom` пустые, и кнопка отвечала «Не задан base URL», хотя
классификация в это же время работала. Оператор читал это как «связи с LLM
нет» и искал поломку там, где её не было.

Проверка обязана идти туда, куда реально пойдёт классификация.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import config
import llm_check
import settings_store


# ── Куда идёт проверка при каждом провайдере ─────────────────────────────────

@pytest.mark.parametrize("provider,base_key,model_key", [
    ("ario", "ARIO_BASE_URL", "ARIO_MODEL"),
    ("custom", "CUSTOM_LLM_BASE_URL", "CUSTOM_LLM_MODEL"),
    ("ollama", "OLLAMA_BASE_URL", "OLLAMA_MODEL"),
])
def test_endpoint_comes_from_the_active_provider(provider, base_key, model_key, monkeypatch):
    monkeypatch.setattr(config, base_key, f"http://{provider}.local/v1", raising=False)
    monkeypatch.setattr(config, model_key, f"model-{provider}", raising=False)

    resolved = llm_check.resolve_endpoint(provider)

    assert resolved["provider"] == provider
    assert resolved["base_url"] == f"http://{provider}.local/v1"
    assert resolved["model"] == f"model-{provider}"
    assert resolved["supported"] is True


def test_groq_base_url_is_its_openai_compatible_endpoint(monkeypatch):
    """У groq адрес не настраивается — он фиксирован в самом SDK."""
    monkeypatch.setattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile")
    resolved = llm_check.resolve_endpoint("groq")
    assert resolved["base_url"] == "https://api.groq.com/openai/v1"
    assert resolved["model"] == "llama-3.3-70b-versatile"


def test_ollama_needs_no_api_key(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
    assert llm_check.resolve_endpoint("ollama")["api_key"] == ""


def test_gemini_cannot_be_checked_this_way():
    """Gemini у нас ходит своим SDK Google, OpenAI-совместимого /models нет."""
    resolved = llm_check.resolve_endpoint("gemini")
    assert resolved["supported"] is False
    assert "gemini" in resolved["detail"].lower()


def test_provider_defaults_to_config(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ario")
    monkeypatch.setattr(config, "ARIO_BASE_URL", "https://ario.example/v1")
    assert llm_check.resolve_endpoint(None)["base_url"] == "https://ario.example/v1"


def test_unknown_provider_is_named_in_the_answer():
    resolved = llm_check.resolve_endpoint("нетакого")
    assert resolved["supported"] is False
    assert "нетакого" in resolved["detail"]


# ── Незаполненные поля формы не затирают конфиг ──────────────────────────────

def test_form_values_win_over_config(monkeypatch):
    monkeypatch.setattr(config, "ARIO_BASE_URL", "https://old.example/v1")
    resolved = llm_check.resolve_endpoint(
        "ario", {"ARIO_BASE_URL": "https://new.example/v1"})
    assert resolved["base_url"] == "https://new.example/v1"


def test_other_providers_fields_are_ignored(monkeypatch):
    """Форма отдаёт все поля сразу; для ario значения custom роли не играют."""
    monkeypatch.setattr(config, "ARIO_BASE_URL", "https://ario.example/v1")
    resolved = llm_check.resolve_endpoint(
        "ario", {"CUSTOM_LLM_BASE_URL": "http://stand.local/v1"})
    assert resolved["base_url"] == "https://ario.example/v1"


def test_missing_base_url_names_the_setting_to_fill(monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_LLM_BASE_URL", "")
    res = llm_check.check_active("custom")
    assert res["ok"] is False
    assert "CUSTOM_LLM_BASE_URL" in res["detail"], "оператору нужно имя поля"


# ── Что видит оператор ──────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def test_check_active_probes_the_resolved_url(monkeypatch):
    monkeypatch.setattr(config, "ARIO_BASE_URL", "https://ario.example/v1")
    monkeypatch.setattr(config, "ARIO_MODEL", "Qwen/Qwen3.8-27B-Ario")
    asked = {}

    def fake_get(url, **kwargs):
        asked["url"] = url
        return _Resp(200, {"data": [{"id": "Qwen/Qwen3.8-27B-Ario"}]})

    monkeypatch.setattr(llm_check.httpx, "get", fake_get)
    res = llm_check.check_active("ario")

    assert asked["url"] == "https://ario.example/v1/models"
    assert res["ok"] is True
    assert res["provider"] == "ario"
    assert "ario" in res["detail"], "провайдер и адрес должны быть видны в ответе"


def test_gemini_check_does_not_hit_the_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("сети быть не должно")

    monkeypatch.setattr(llm_check.httpx, "get", boom)
    assert llm_check.check_active("gemini")["ok"] is False


# ── Форма и запрос не разъезжаются ──────────────────────────────────────────

def _llm_setting_keys() -> set[str]:
    return {
        field["key"]
        for group in settings_store.SETTINGS_SPEC if group["group"] == "llm"
        for field in group["fields"]
    }


def test_request_model_accepts_every_llm_field_of_the_form():
    """Иначе значение из формы молча потеряется и проверится не то."""
    import api_server
    assert _llm_setting_keys() <= set(api_server.LlmTestRequest.model_fields)


def test_frontend_sends_every_llm_field_of_the_form():
    source = (Path(__file__).parent.parent / "src" / "static" / "settings.js").read_text(
        encoding="utf-8")
    missing = [key for key in _llm_setting_keys() if key not in source]
    assert not missing, f"кнопка не отправляет поля: {missing}"


def test_frontend_does_not_send_rx_credentials_to_llm_check():
    """Пароль RX в проверке LLM не нужен — не отправляем его туда."""
    source = (Path(__file__).parent.parent / "src" / "static" / "settings.js").read_text(
        encoding="utf-8")
    body = source.split("async function testLlm")[1].split("async function testRx")[0]
    assert "RX_PASSWORD" not in body

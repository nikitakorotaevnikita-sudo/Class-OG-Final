import sys
sys.path.insert(0, "src")

import pytest

import config
import settings_store


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Изолированный .env, чтобы тесты не трогали настоящий конфиг проекта."""
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_store, "ENV_PATH", path)
    return path


# ── Валидация ───────────────────────────────────────────────────────────────

def test_unknown_key_rejected():
    with pytest.raises(settings_store.SettingsError):
        settings_store.normalize_updates({"SOME_RANDOM_KEY": "x"})


def test_provider_must_be_known():
    with pytest.raises(settings_store.SettingsError):
        settings_store.normalize_updates({"LLM_PROVIDER": "openai"})


def test_provider_accepted():
    assert settings_store.normalize_updates({"LLM_PROVIDER": "groq"}) == {"LLM_PROVIDER": "groq"}


def test_url_must_have_scheme():
    with pytest.raises(settings_store.SettingsError):
        settings_store.normalize_updates({"RX_ODATA_URL": "172.16.104.68/integration/odata"})


def test_newline_rejected():
    """Перенос строки разорвал бы .env на две записи."""
    with pytest.raises(settings_store.SettingsError):
        settings_store.normalize_updates({"RX_USER": "admin\nGROQ_API_KEY=stolen"})


def test_non_secret_cannot_be_empty():
    with pytest.raises(settings_store.SettingsError):
        settings_store.normalize_updates({"RX_USER": "  "})


def test_blank_secret_is_skipped():
    """Пустое поле секрета означает «не менять», а не «стереть»."""
    assert settings_store.normalize_updates({"RX_PASSWORD": ""}) == {}


def test_masked_secret_is_skipped():
    """Форма присылает маску обратно, если пользователь её не трогал."""
    assert settings_store.normalize_updates({"RX_PASSWORD": settings_store.MASK}) == {}


def test_secret_with_value_is_kept():
    assert settings_store.normalize_updates({"RX_PASSWORD": "s3cret"}) == {"RX_PASSWORD": "s3cret"}


def test_values_are_trimmed():
    assert settings_store.normalize_updates({"RX_USER": "  admin  "}) == {"RX_USER": "admin"}


# ── Запись в .env ───────────────────────────────────────────────────────────

def test_existing_key_is_replaced_in_place(env_file):
    env_file.write_text(
        "# комментарий\nRX_USER=old\nRX_ODATA_URL=http://old/odata\n",
        encoding="utf-8",
    )
    settings_store.write_env({"RX_USER": "new"})

    text = env_file.read_text(encoding="utf-8")
    assert "RX_USER=new" in text
    assert "RX_USER=old" not in text
    # Соседние строки и комментарии не пострадали
    assert "# комментарий" in text
    assert "RX_ODATA_URL=http://old/odata" in text


def test_missing_key_is_appended(env_file):
    env_file.write_text("RX_USER=admin\n", encoding="utf-8")
    settings_store.write_env({"GROQ_API_KEY": "gsk_new"})

    text = env_file.read_text(encoding="utf-8")
    assert "RX_USER=admin" in text
    assert "GROQ_API_KEY=gsk_new" in text


def test_commented_key_is_not_touched(env_file):
    """Закомментированный ключ — не настройка; значение должно дописаться отдельно."""
    env_file.write_text("# EMBEDDING_MODEL=models/e5\n# RX_USER=ghost\n", encoding="utf-8")
    settings_store.write_env({"RX_USER": "real"})

    text = env_file.read_text(encoding="utf-8")
    assert "# RX_USER=ghost" in text
    assert "\nRX_USER=real" in text


def test_backup_is_created(env_file):
    env_file.write_text("RX_USER=admin\n", encoding="utf-8")
    backup = settings_store.write_env({"RX_USER": "other"})

    assert backup is not None and backup.exists()
    assert backup.read_text(encoding="utf-8") == "RX_USER=admin\n"


def test_bom_and_broken_encoding_survive_roundtrip(env_file):
    """Настоящий .env содержит BOM и комментарии в битой кодировке — они не должны
    портиться при сохранении настроек."""
    original = "﻿# РСЃР≠ mojibake\nRX_USER=admin\n".encode("utf-8")
    # добавляем «невалидный» байт, какие встречаются в реальном файле
    original += b"# raw \xff byte\n"
    env_file.write_bytes(original)

    settings_store.write_env({"RX_USER": "changed"})

    result = env_file.read_bytes()
    assert result.startswith(b"\xef\xbb\xbf")       # BOM на месте
    assert b"\xff" in result                        # сырой байт уцелел
    assert b"RX_USER=changed" in result


def test_no_updates_writes_nothing(env_file):
    env_file.write_text("RX_USER=admin\n", encoding="utf-8")
    assert settings_store.write_env({}) is None
    assert env_file.read_text(encoding="utf-8") == "RX_USER=admin\n"


# ── Горячее применение ──────────────────────────────────────────────────────

def test_apply_runtime_updates_config_and_importers(monkeypatch):
    """rx_client импортирует RX_USER напрямую — его копию тоже надо обновить."""
    import rx_client

    monkeypatch.setattr(config, "RX_USER", "old", raising=False)
    monkeypatch.setattr(rx_client, "RX_USER", "old", raising=False)
    monkeypatch.delenv("RX_USER", raising=False)

    applied = settings_store.apply_runtime({"RX_USER": "fresh"})

    assert applied == ["RX_USER"]
    assert config.RX_USER == "fresh"
    assert rx_client.RX_USER == "fresh"
    import os
    assert os.environ["RX_USER"] == "fresh"


def test_apply_runtime_resets_cached_llm_client():
    """Смена ключа должна выбросить закешированный клиент, иначе он останется старым."""
    class FakeAgent:
        def __init__(self):
            self.llm = "ario"
            self.groq = object()

    agent = FakeAgent()
    settings_store.apply_runtime({"GROQ_API_KEY": "gsk_new"}, agent=agent)

    assert not hasattr(agent, "groq")


def test_apply_runtime_switches_agent_provider():
    class FakeAgent:
        llm = "ario"

    agent = FakeAgent()
    settings_store.apply_runtime({"LLM_PROVIDER": "groq"}, agent=agent)

    assert agent.llm == "groq"


# ── Чтение ──────────────────────────────────────────────────────────────────

def test_read_settings_masks_secrets(monkeypatch):
    monkeypatch.setattr(config, "RX_PASSWORD", "topsecret", raising=False)

    data = settings_store.read_settings()
    rx_fields = {f["key"]: f for g in data["groups"] if g["group"] == "rx" for f in g["fields"]}
    password = rx_fields["RX_PASSWORD"]

    assert password["value"] == settings_store.MASK
    assert password["has_value"] is True
    assert "topsecret" not in str(data)


def test_read_settings_marks_empty_secret(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "", raising=False)

    data = settings_store.read_settings()
    llm_fields = {f["key"]: f for g in data["groups"] if g["group"] == "llm" for f in g["fields"]}

    assert llm_fields["GEMINI_API_KEY"]["value"] == ""
    assert llm_fields["GEMINI_API_KEY"]["has_value"] is False


def test_read_settings_exposes_plain_values(monkeypatch):
    monkeypatch.setattr(config, "RX_USER", "Administrator", raising=False)

    data = settings_store.read_settings()
    rx_fields = {f["key"]: f for g in data["groups"] if g["group"] == "rx" for f in g["fields"]}

    assert rx_fields["RX_USER"]["value"] == "Administrator"
    assert rx_fields["RX_USER"]["is_secret"] is False

"""worker_main: горячая перезагрузка .env и одиночка агента."""
import os

from core import config
from core import settings_store
from worker import worker_main


def _bump(env, text):
    env.write_text(text, encoding="utf-8")
    st = env.stat()
    os.utime(env, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def _setup(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=groq\n", encoding="utf-8")
    monkeypatch.setattr(settings_store, "ENV_PATH", env)
    monkeypatch.setattr(worker_main, "_env_mtime", env.stat().st_mtime_ns)  # база при импорте
    monkeypatch.setattr(worker_main, "_agent", None)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ario")
    return env


def test_env_change_triggers_refresh(monkeypatch, tmp_path):
    env = _setup(monkeypatch, tmp_path)
    applied = []
    monkeypatch.setattr(worker_main.settings_store, "apply_runtime",
                        lambda updates, agent=None: applied.append(dict(updates)) or list(updates))
    assert worker_main.refresh_settings_if_env_changed() is False
    # правка до первого вызова применяется сразу
    _bump(env, "LLM_PROVIDER=gemini\n")
    assert worker_main.refresh_settings_if_env_changed() is True
    assert applied == [{"LLM_PROVIDER": "gemini"}]
    assert worker_main.refresh_settings_if_env_changed() is False


def test_typed_config_equal_to_env_value_is_not_reported_as_changed(monkeypatch, tmp_path):
    """Числа в config и строки в .env сравниваются по значению: без ложных «изменений»."""
    env = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "SYNC_WAIT_TIMEOUT_SEC", 1800)
    applied = []
    monkeypatch.setattr(worker_main.settings_store, "apply_runtime",
                        lambda updates, agent=None: applied.append(dict(updates)) or list(updates))
    _bump(env, "LLM_PROVIDER=groq\nSYNC_WAIT_TIMEOUT_SEC=01800\n")
    assert worker_main.refresh_settings_if_env_changed() is True
    assert applied == []
    _bump(env, "LLM_PROVIDER=groq\nSYNC_WAIT_TIMEOUT_SEC=900\n")
    st = env.stat()  # вторая запись в тот же тик ФС: mtime мог не сдвинуться
    os.utime(env, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert worker_main.refresh_settings_if_env_changed() is True
    assert applied == [{"SYNC_WAIT_TIMEOUT_SEC": "900"}]


def test_unset_config_value_equals_empty_env_value(monkeypatch, tmp_path):
    env = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "RX_USER", None, raising=False)
    applied = []
    monkeypatch.setattr(worker_main.settings_store, "apply_runtime",
                        lambda updates, agent=None: applied.append(dict(updates)) or list(updates))
    _bump(env, "LLM_PROVIDER=groq\nRX_USER=\n")
    assert worker_main.refresh_settings_if_env_changed() is True
    assert applied == []


def test_module_level_baseline_is_env_mtime_at_import(monkeypatch, tmp_path):
    """Базовая отметка берётся при импорте модуля: правка до первого вызова не теряется."""
    import importlib

    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=ario\n", encoding="utf-8")
    monkeypatch.setattr(settings_store, "ENV_PATH", env)
    # reload перепривязывает имена модуля — monkeypatch вернёт прежние значения
    for name in ("_env_mtime", "_agent", "_lock"):
        monkeypatch.setattr(worker_main, name, getattr(worker_main, name))
    monkeypatch.setattr(config, "LLM_PROVIDER", "ario")

    importlib.reload(worker_main)
    assert worker_main._env_mtime == env.stat().st_mtime_ns
    assert worker_main.refresh_settings_if_env_changed() is False  # без изменений

    applied = []
    monkeypatch.setattr(worker_main.settings_store, "apply_runtime",
                        lambda updates, agent=None: applied.append(dict(updates)) or list(updates))
    _bump(env, "LLM_PROVIDER=gemini\n")
    assert worker_main.refresh_settings_if_env_changed() is True
    assert applied == [{"LLM_PROVIDER": "gemini"}]

    env.unlink()
    importlib.reload(worker_main)
    assert worker_main._env_mtime is None


def test_failed_apply_does_not_fail_get_agent_and_is_retried(monkeypatch, tmp_path):
    env = _setup(monkeypatch, tmp_path)

    class FakeAgent:
        model_version = 1

        def __init__(self, storage=None):
            pass

        def reload_model_if_changed(self):
            return False

    monkeypatch.setattr(worker_main, "ClassifierAgent", FakeAgent)
    monkeypatch.setattr(worker_main, "embed_pending_annotations", lambda a: 0)
    applied = []
    state = {"fail": True}

    def fake_apply(updates, agent=None):
        if state["fail"]:
            raise RuntimeError("boom")
        applied.append(dict(updates))
        return list(updates)

    monkeypatch.setattr(worker_main.settings_store, "apply_runtime", fake_apply)
    _bump(env, "LLM_PROVIDER=gemini\n")
    assert worker_main.get_agent() is not None  # не падает
    assert applied == []
    state["fail"] = False
    assert worker_main.get_agent() is not None
    assert applied == [{"LLM_PROVIDER": "gemini"}]


def test_get_agent_is_singleton(monkeypatch):
    created = []

    class FakeAgent:
        model_version = 1

        def __init__(self, storage=None):
            created.append(self)

        def reload_model_if_changed(self):
            return False

    monkeypatch.setattr(worker_main, "ClassifierAgent", FakeAgent)
    monkeypatch.setattr(worker_main, "_agent", None)
    monkeypatch.setattr(worker_main, "_env_mtime", None)
    monkeypatch.setattr(worker_main, "embed_pending_annotations", lambda a: 0)
    assert worker_main.get_agent() is worker_main.get_agent()
    assert len(created) == 1

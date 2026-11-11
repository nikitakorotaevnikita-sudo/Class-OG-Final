"""worker_main: горячая перезагрузка .env и одиночка агента."""
import os

import config
import settings_store
import worker_main


def test_env_change_triggers_refresh(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=groq\n", encoding="utf-8")
    monkeypatch.setattr(settings_store, "ENV_PATH", env)
    monkeypatch.setattr(worker_main, "_env_mtime", None)
    monkeypatch.setattr(worker_main, "_agent", None)
    applied = []
    monkeypatch.setattr(worker_main.settings_store, "apply_runtime",
                        lambda updates, agent=None: applied.append(dict(updates)) or list(updates))
    key = "LLM_PROVIDER"
    monkeypatch.setattr(config, key, "ario")
    assert key in settings_store.FIELDS_BY_KEY
    assert worker_main.refresh_settings_if_env_changed() is False
    assert worker_main.refresh_settings_if_env_changed() is False
    env.write_text("LLM_PROVIDER=gemini\n", encoding="utf-8")
    st = env.stat()
    os.utime(env, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert worker_main.refresh_settings_if_env_changed() is True
    assert applied == [{key: "gemini"}]
    assert worker_main.refresh_settings_if_env_changed() is False


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

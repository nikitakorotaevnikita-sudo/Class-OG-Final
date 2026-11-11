"""worker_main: горячая перезагрузка .env и одиночка агента."""
import os

import config
import settings_store
import worker_main


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

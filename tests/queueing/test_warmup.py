"""Прогрев агента по сигналам Celery: без прогрева в родителе prefork."""
import pytest

import worker_main
from queueing import warmup


# ── Прогрев и fork (prefork) ────────────────────────────────────────────────

def _fake_agent_cls(created):
    class FakeAgent:
        model_version = 1

        def __init__(self, storage=None):
            self.storage = storage
            created.append(self)

        def reload_model_if_changed(self):
            return False

    return FakeAgent


def _consumer_with_pool(pool_module):
    import importlib
    from types import SimpleNamespace

    pool_cls = importlib.import_module(pool_module).TaskPool
    return SimpleNamespace(pool=object.__new__(pool_cls))


def test_worker_ready_does_not_warm_in_prefork_parent(monkeypatch):
    """Родитель prefork не открывает пул БД и не грузит модель: дети унаследовали бы их."""
    created = []
    monkeypatch.setattr(worker_main, "ClassifierAgent", _fake_agent_cls(created))
    monkeypatch.setattr(worker_main, "_agent", None)
    monkeypatch.setattr(worker_main, "embed_pending_annotations", lambda a: 0)
    warmup.on_worker_ready(sender=_consumer_with_pool("celery.concurrency.prefork"))
    assert created == []
    warmup.on_worker_ready(sender=None)  # пул неизвестен — тоже не греем
    assert created == []


@pytest.mark.parametrize("pool_module", ["celery.concurrency.solo", "celery.concurrency.thread"])
def test_worker_ready_warms_in_solo_and_threads(monkeypatch, pool_module):
    created = []
    monkeypatch.setattr(worker_main, "ClassifierAgent", _fake_agent_cls(created))
    monkeypatch.setattr(worker_main, "_agent", None)
    monkeypatch.setattr(worker_main, "_env_mtime", None)
    monkeypatch.setattr(worker_main, "embed_pending_annotations", lambda a: 0)
    warmup.on_worker_ready(sender=_consumer_with_pool(pool_module))
    assert len(created) == 1


def test_worker_process_init_resets_inherited_state_then_warms(monkeypatch):
    """Дочерний процесс: пул родителя забыт (не закрыт), storage и агент — заново."""
    from storage import db, factory

    class ParentPool:
        closed = False

        def close(self):
            ParentPool.closed = True

    parent_pool = ParentPool()
    parent_storage = object()
    parent_agent = object()
    monkeypatch.setattr(db, "_pool", parent_pool)
    monkeypatch.setattr(factory, "_storage", parent_storage)
    monkeypatch.setattr(worker_main, "_agent", parent_agent)
    monkeypatch.setattr(worker_main, "_env_mtime", None)
    created = []
    monkeypatch.setattr(worker_main, "ClassifierAgent", _fake_agent_cls(created))
    monkeypatch.setattr(worker_main, "embed_pending_annotations", lambda a: 0)

    warmup.on_worker_process_init(sender="host")

    assert db._pool is None
    assert ParentPool.closed is False
    assert len(created) == 1 and worker_main._agent is created[0]
    assert created[0].storage is not parent_storage
    assert factory._storage is created[0].storage


def test_warm_signals_are_connected():
    from celery.signals import worker_process_init, worker_ready

    def receivers(sig):
        return [r() if callable(r) and getattr(r, "__self__", None) is None
                and r.__class__.__name__ in ("ReferenceType", "weakref") else r
                for _, r in sig.receivers]

    assert warmup.on_worker_ready in receivers(worker_ready)
    assert warmup.on_worker_process_init in receivers(worker_process_init)


def test_tasks_module_wires_warmup_without_loading_the_model():
    """Воркер импортирует queueing.tasks в родителе: модель там грузиться не должна."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; sys.path.insert(0, 'src')\n"
        "import queueing.tasks\n"
        "bad = [m for m in ('worker_main', 'classifier_agent', 'torch', "
        "'sentence_transformers') if m in sys.modules]\n"
        "print('WIRED' if 'queueing.warmup' in sys.modules else 'NOT_WIRED', bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(root),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WIRED" in proc.stdout and "NOT_WIRED" not in proc.stdout

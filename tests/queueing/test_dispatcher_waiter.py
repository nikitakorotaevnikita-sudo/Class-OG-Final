"""Диспетчер задач и ожидатель job_done (сторона API).

pytest-asyncio не установлен: асинхронный код гоняется через asyncio.run.
"""
import asyncio
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from kombu.exceptions import OperationalError as KombuOperationalError

from queueing import dispatcher as dispatcher_mod
from queueing import waiter as waiter_mod
from queueing.dispatcher import CeleryDispatcher, DispatchError, LocalDispatcher
from queueing.waiter import JobWaiter
from storage.memory import MemoryStorage

SRC = Path(__file__).resolve().parents[2] / "src"


def _new_job(storage):
    return storage.jobs.create("classify", {"llm_provider": "ario"})


def _finish_later(storage, job_id, delay, result=None):
    def run():
        time.sleep(delay)
        storage.jobs.finish(job_id, result=result or {"ok": True}, error=None)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


async def _with_waiter(waiter, coro_fn):
    await waiter.start()
    try:
        return await coro_fn()
    finally:
        await waiter.stop()


# ── memory backend ───────────────────────────────────────────────────────────

def test_wait_returns_on_notify():
    storage = MemoryStorage()
    job = _new_job(storage)
    waiter = JobWaiter(storage)

    async def scenario():
        _finish_later(storage, job.id, 0.1)
        t0 = time.monotonic()
        # recheck_every велик: вернуть может только уведомление
        result = await waiter.wait(job.id, timeout=5, recheck_every=30)
        return result, time.monotonic() - t0

    result, elapsed = asyncio.run(_with_waiter(waiter, scenario))
    assert result is not None and result.status == "done"
    assert result.result == {"ok": True}
    assert elapsed < 1.0


def test_wait_timeout_returns_none():
    storage = MemoryStorage()
    job = _new_job(storage)
    waiter = JobWaiter(storage)

    async def scenario():
        t0 = time.monotonic()
        result = await waiter.wait(job.id, timeout=0.3, recheck_every=0.1)
        return result, time.monotonic() - t0

    result, elapsed = asyncio.run(_with_waiter(waiter, scenario))
    assert result is None
    assert 0.25 <= elapsed < 2.0
    assert waiter._events == {}  # регистрация снята в finally


def test_waiter_falls_back_to_db_recheck():
    storage = MemoryStorage()
    job = _new_job(storage)
    waiter = JobWaiter(storage)

    async def scenario():
        def finish_silently():
            time.sleep(0.15)
            # завершаем без уведомления слушателей (как пропущенный NOTIFY)
            saved = list(storage.jobs._listeners)
            storage.jobs._listeners.clear()
            try:
                storage.jobs.finish(job.id, result={"ok": 1}, error=None)
            finally:
                storage.jobs._listeners.extend(saved)

        threading.Thread(target=finish_silently, daemon=True).start()
        t0 = time.monotonic()
        result = await waiter.wait(job.id, timeout=5, recheck_every=0.1)
        return result, time.monotonic() - t0

    result, elapsed = asyncio.run(_with_waiter(waiter, scenario))
    assert result is not None and result.status == "done"
    assert elapsed < 1.0


def test_wait_returns_already_finished_job_immediately():
    storage = MemoryStorage()
    job = _new_job(storage)
    storage.jobs.finish(job.id, result=None, error="boom")
    waiter = JobWaiter(storage)

    result = asyncio.run(_with_waiter(
        waiter, lambda: waiter.wait(job.id, timeout=5, recheck_every=30)))
    assert result.status == "error" and result.error == "boom"


def test_recheck_db_error_does_not_abort_wait(monkeypatch):
    """Сбой БД при перепроверке логируется, ожидание продолжается."""
    storage = MemoryStorage()
    job = _new_job(storage)
    waiter = JobWaiter(storage)
    real_get = storage.jobs.get
    calls = {"n": 0}

    def flaky_get(job_id):
        calls["n"] += 1
        if calls["n"] == 2:  # первое чтение проходит, вторая перепроверка падает
            raise ConnectionError("БД недоступна")
        return real_get(job_id)

    monkeypatch.setattr(storage.jobs, "get", flaky_get)

    async def scenario():
        _finish_later(storage, job.id, 0.3)
        return await waiter.wait(job.id, timeout=5, recheck_every=0.1)

    result = asyncio.run(_with_waiter(waiter, scenario))
    assert calls["n"] >= 3
    assert result is not None and result.status == "done"


def test_first_read_error_propagates(monkeypatch):
    storage = MemoryStorage()
    waiter = JobWaiter(storage)

    def broken_get(job_id):
        raise ConnectionError("БД недоступна")

    monkeypatch.setattr(storage.jobs, "get", broken_get)
    with pytest.raises(ConnectionError):
        asyncio.run(waiter.wait("a" * 32, timeout=1, recheck_every=0.1))
    assert waiter._events == {}


def test_recheck_every_is_clamped(monkeypatch):
    """recheck_every=0 не превращается в busy-loop."""
    storage = MemoryStorage()
    job = _new_job(storage)
    waiter = JobWaiter(storage)
    real_get = storage.jobs.get
    calls = {"n": 0}

    def counting_get(job_id):
        calls["n"] += 1
        return real_get(job_id)

    monkeypatch.setattr(storage.jobs, "get", counting_get)
    result = asyncio.run(waiter.wait(job.id, timeout=0.3, recheck_every=0))
    assert result is None
    assert calls["n"] <= 0.3 / 0.05 + 2


def test_concurrent_waiters_on_same_job_all_wake():
    storage = MemoryStorage()
    job = _new_job(storage)
    waiter = JobWaiter(storage)

    async def scenario():
        _finish_later(storage, job.id, 0.1)
        t0 = time.monotonic()
        results = await asyncio.gather(
            *(waiter.wait(job.id, timeout=5, recheck_every=30) for _ in range(3)))
        return results, time.monotonic() - t0

    results, elapsed = asyncio.run(_with_waiter(waiter, scenario))
    assert all(r is not None and r.status == "done" for r in results)
    assert elapsed < 1.0


def test_stop_removes_memory_listener():
    storage = MemoryStorage()
    waiter = JobWaiter(storage)

    async def scenario():
        await waiter.start()
        assert len(storage.jobs._listeners) == 1
        await waiter.stop()

    asyncio.run(scenario())
    assert storage.jobs._listeners == []


# ── диспетчеры ───────────────────────────────────────────────────────────────

def test_celery_dispatcher_wraps_broker_error(monkeypatch):
    from queueing.celery_app import app

    def boom(*a, **kw):
        raise KombuOperationalError("connection refused")

    monkeypatch.setattr(app, "send_task", boom)
    with pytest.raises(DispatchError):
        CeleryDispatcher().send("classify_appeal", {"job_id": "x"}, queue="classify")


def test_celery_dispatcher_wraps_oserror(monkeypatch):
    from queueing.celery_app import app

    def boom(*a, **kw):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(app, "send_task", boom)
    with pytest.raises(DispatchError):
        CeleryDispatcher().send("classify_appeal", {"job_id": "x"}, queue="classify")


def test_celery_dispatcher_sends_task(monkeypatch):
    from queueing.celery_app import app

    calls = []
    monkeypatch.setattr(app, "send_task", lambda *a, **kw: calls.append((a, kw)))
    CeleryDispatcher().send("finetune", {"job_id": "abc"}, queue="training")
    assert calls == [(("finetune",), {"args": [{"job_id": "abc"}], "queue": "training"})]


def test_local_dispatcher_runs_task_and_waiter_sees_result(monkeypatch):
    from queueing import tasks

    storage = MemoryStorage()
    job = _new_job(storage)
    agents = []

    def fake_run(msg, st, agent):
        assert st is storage
        time.sleep(0.05)
        st.jobs.finish(msg["job_id"], result={"agent": agent}, error=None)

    monkeypatch.setattr(tasks, "run_classify_appeal", fake_run)

    def factory():
        agents.append(1)
        return "AGENT"

    disp = LocalDispatcher(storage, agent_factory=factory)
    waiter = JobWaiter(storage)

    async def scenario():
        disp.send("classify_appeal", {"job_id": job.id}, queue="classify")
        return await waiter.wait(job.id, timeout=5, recheck_every=30)

    result = asyncio.run(_with_waiter(waiter, scenario))
    assert result.status == "done" and result.result == {"agent": "AGENT"}

    # фабрика вызывается на каждую задачу: кэш и горячая перезагрузка модели
    # живут в worker_main.get_agent (reload_model_if_changed, настройки .env)
    job2 = _new_job(storage)
    disp.send("classify_appeal", {"job_id": job2.id}, queue="classify")
    asyncio.run(_with_waiter(waiter, lambda: waiter.wait(job2.id, timeout=5)))
    assert agents == [1, 1]


def test_local_dispatcher_finetune_has_no_agent(monkeypatch):
    from queueing import tasks

    storage = MemoryStorage()
    job = storage.jobs.create("finetune", {})

    def fake_finetune(msg, st):
        st.jobs.finish(msg["job_id"], result={"v": 1}, error=None)

    monkeypatch.setattr(tasks, "run_finetune", fake_finetune)

    def factory():
        raise AssertionError("finetune не должен строить агента")

    disp = LocalDispatcher(storage, agent_factory=factory)
    waiter = JobWaiter(storage)
    disp.send("finetune", {"job_id": job.id}, queue="training")
    result = asyncio.run(_with_waiter(waiter, lambda: waiter.wait(job.id, timeout=5)))
    assert result.status == "done"


@pytest.mark.parametrize("exc", [RuntimeError("сломалось"), None])
def test_local_dispatcher_finishes_job_with_error_on_exception(monkeypatch, exc):
    from queueing import tasks

    storage = MemoryStorage()
    job = _new_job(storage)

    def failing(msg, st, agent):
        raise exc if exc is not None else tasks.RetryableError("БД недоступна")

    monkeypatch.setattr(tasks, "run_classify_appeal", failing)
    disp = LocalDispatcher(storage, agent_factory=lambda: object())
    waiter = JobWaiter(storage)
    disp.send("classify_appeal", {"job_id": job.id}, queue="classify")
    result = asyncio.run(_with_waiter(
        waiter, lambda: waiter.wait(job.id, timeout=5, recheck_every=30)))
    assert result is not None and result.status == "error"
    expected = "сломалось" if exc is not None else "БД недоступна"
    assert expected in result.error


def test_local_dispatcher_agent_factory_failure_finishes_job(monkeypatch):
    storage = MemoryStorage()
    job = _new_job(storage)

    def factory():
        raise RuntimeError("нет активной модели")

    disp = LocalDispatcher(storage, agent_factory=factory)
    waiter = JobWaiter(storage)
    disp.send("classify_appeal", {"job_id": job.id}, queue="classify")
    result = asyncio.run(_with_waiter(waiter, lambda: waiter.wait(job.id, timeout=5)))
    assert result.status == "error" and "нет активной модели" in result.error


def test_local_dispatcher_unknown_task():
    disp = LocalDispatcher(MemoryStorage(), agent_factory=lambda: None)
    with pytest.raises(DispatchError):
        disp.send("no_such_task", {"job_id": "x"}, queue="classify")


# ── синглтоны ────────────────────────────────────────────────────────────────

def test_singletons_memory_backend():
    from storage import get_storage

    dispatcher_mod.reset_dispatcher()
    waiter_mod.reset_waiter()
    try:
        d = dispatcher_mod.get_dispatcher()
        w = waiter_mod.get_waiter()
        assert isinstance(d, LocalDispatcher)
        assert d is dispatcher_mod.get_dispatcher()
        assert isinstance(w, JobWaiter) and w is waiter_mod.get_waiter()
        assert w._storage is get_storage()
        assert w._is_memory
    finally:
        dispatcher_mod.reset_dispatcher()
        waiter_mod.reset_waiter()


def test_singletons_postgres_backend(monkeypatch):
    import config

    monkeypatch.setattr(config, "STORAGE_BACKEND", "postgres")
    dispatcher_mod.reset_dispatcher()
    waiter_mod.reset_waiter()
    try:
        assert isinstance(dispatcher_mod.get_dispatcher(), CeleryDispatcher)
        w = waiter_mod.get_waiter()  # пул не открывается: storage ленивый
        assert isinstance(w, JobWaiter) and not w._is_memory
        assert w._dsn == config.DATABASE_URL
    finally:
        dispatcher_mod.reset_dispatcher()
        waiter_mod.reset_waiter()


def test_modules_do_not_import_model_stack():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import queueing.dispatcher, queueing.waiter\n"
        "bad = [m for m in ('torch', 'classifier_agent', 'sentence_transformers', 'worker_main')"
        " if m in sys.modules]\n"
        "print('BAD', bad)\n"
        "sys.exit(1 if bad else 0)\n" % str(SRC)
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120, cwd=str(SRC.parent))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── PostgreSQL ───────────────────────────────────────────────────────────────

def _pg_url():
    # pg_conn уже пропустил тест, если TEST_DATABASE_URL не задан
    return os.environ["TEST_DATABASE_URL"]


@pytest.fixture
def pg_pair(pg_conn):
    """(storage ожидателя на pg_conn, storage «воркера» на отдельном соединении)."""
    import psycopg
    from pgvector.psycopg import register_vector
    from storage.pg import PgStorage

    register_vector(pg_conn)
    search_path = pg_conn.execute("SHOW search_path").fetchone()[0]
    worker_conn = psycopg.connect(_pg_url(), autocommit=True)
    worker_conn.execute(f"SET search_path TO {search_path}")
    register_vector(worker_conn)
    lock = threading.Lock()

    def make(conn):
        @contextmanager
        def connect():
            with lock, conn.transaction():
                yield conn
        return connect

    try:
        yield PgStorage(make(pg_conn)), PgStorage(make(worker_conn))
    finally:
        worker_conn.close()


async def _await_listening(waiter, timeout=10.0, not_pid=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if waiter._listening.is_set() and waiter._backend_pid not in (None, not_pid):
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.db
def test_pg_waiter_wakes_on_notify(pg_pair):
    api_storage, worker_storage = pg_pair
    job = api_storage.jobs.create("classify", {"llm_provider": "ario"})
    waiter = JobWaiter(api_storage, dsn=_pg_url())

    async def scenario():
        assert await _await_listening(waiter)
        _finish_later(worker_storage, job.id, 0.2)
        t0 = time.monotonic()
        result = await waiter.wait(job.id, timeout=10, recheck_every=5.0)
        return result, time.monotonic() - t0

    result, elapsed = asyncio.run(_with_waiter(waiter, scenario))
    assert result is not None and result.status == "done"
    assert elapsed < 1.5  # заметно меньше recheck_every=5


@pytest.mark.db
def test_pg_waiter_survives_reconnect(pg_pair):
    import psycopg

    api_storage, worker_storage = pg_pair
    job = api_storage.jobs.create("classify", {"llm_provider": "ario"})
    waiter = JobWaiter(api_storage, dsn=_pg_url())

    async def scenario():
        assert await _await_listening(waiter)
        old_pid = waiter._backend_pid
        with psycopg.connect(_pg_url(), autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(%s)", (old_pid,))
        assert await _await_listening(waiter, timeout=15, not_pid=old_pid)
        _finish_later(worker_storage, job.id, 0.2)
        t0 = time.monotonic()
        result = await waiter.wait(job.id, timeout=10, recheck_every=30)
        return result, time.monotonic() - t0

    result, elapsed = asyncio.run(_with_waiter(waiter, scenario))
    assert result is not None and result.status == "done"
    assert elapsed < 3.0

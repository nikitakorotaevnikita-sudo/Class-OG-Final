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
    assert waiter_mod.MIN_RECHECK_SEC >= 0.5
    assert calls["n"] <= 2  # первое чтение + одна перепроверка у дедлайна
    calls["n"] = 0
    result = asyncio.run(waiter.wait(job.id, timeout=0.3, recheck_every=-1))
    assert result is None and calls["n"] <= 2


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

    def fake_run(job, st, agent):
        assert st is storage
        time.sleep(0.05)
        st.jobs.finish(job.id, result={"agent": agent}, error=None)

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

    def fake_finetune(job, st):
        st.jobs.finish(job.id, result={"v": 1}, error=None)

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

    def failing(job, st, agent):
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


def test_local_dispatcher_bounds_concurrency(monkeypatch):
    """Не поток на задачу: одновременно выполняется не больше max_workers задач."""
    from queueing import tasks

    storage = MemoryStorage()
    jobs = [_new_job(storage) for _ in range(6)]
    lock = threading.Lock()
    state = {"now": 0, "max": 0}
    gate = threading.Event()

    def slow(job, st, agent):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        gate.wait(5)
        with lock:
            state["now"] -= 1
        st.jobs.finish(job.id, result={"ok": 1}, error=None)

    monkeypatch.setattr(tasks, "run_classify_appeal", slow)
    disp = LocalDispatcher(storage, agent_factory=lambda: "A", max_workers=2)
    before = threading.active_count()
    for job in jobs:
        disp.send("classify_appeal", {"job_id": job.id}, queue="classify")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and state["now"] < 2:
        time.sleep(0.02)
    time.sleep(0.1)  # короткая пауза: убедиться, что третья задача не стартует
    assert state["now"] == 2
    assert threading.active_count() - before <= 2
    assert all(t.daemon for t in disp._threads)
    gate.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
            storage.jobs.get(j.id).status != "done" for j in jobs):
        time.sleep(0.02)
    assert all(storage.jobs.get(j.id).status == "done" for j in jobs)
    assert state["max"] == 2


def test_local_dispatcher_default_workers_from_config(monkeypatch):
    from core import config

    monkeypatch.setattr(config, "CLASSIFY_CONCURRENCY", 3)
    assert LocalDispatcher(MemoryStorage(), agent_factory=lambda: None).max_workers == 3
    monkeypatch.setattr(config, "CLASSIFY_CONCURRENCY", 0)
    assert LocalDispatcher(MemoryStorage(), agent_factory=lambda: None).max_workers == 2


def test_local_dispatcher_clamps_non_positive_workers(monkeypatch):
    from core import config

    assert LocalDispatcher(MemoryStorage(), agent_factory=lambda: None,
                           max_workers=-1).max_workers == 1
    monkeypatch.setattr(config, "CLASSIFY_CONCURRENCY", -3)
    assert LocalDispatcher(MemoryStorage(), agent_factory=lambda: None).max_workers == 1


def test_local_finetune_does_not_hold_classify_slot(monkeypatch):
    """Долгое дообучение идёт в отдельном потоке: классификация не ждёт."""
    from queueing import tasks

    storage = MemoryStorage()
    ft_job, cl_job = _new_job(storage), _new_job(storage)
    gate = threading.Event()
    started = threading.Event()

    def slow_finetune(job, st):
        started.set()
        gate.wait(10)
        st.jobs.finish(job.id, result={"ok": 1}, error=None)

    def fast_classify(job, st, agent):
        st.jobs.finish(job.id, result={"ok": 1}, error=None)

    monkeypatch.setattr(tasks, "run_finetune", slow_finetune)
    monkeypatch.setattr(tasks, "run_classify_appeal", fast_classify)
    disp = LocalDispatcher(storage, agent_factory=lambda: "A", max_workers=1)
    try:
        disp.send("finetune", {"job_id": ft_job.id}, queue="training")
        assert started.wait(5)
        disp.send("classify_appeal", {"job_id": cl_job.id}, queue="classify")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and storage.jobs.get(cl_job.id).status != "done":
            time.sleep(0.02)
        assert storage.jobs.get(cl_job.id).status == "done"
        assert storage.jobs.get(ft_job.id).status != "done"
    finally:
        gate.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and storage.jobs.get(ft_job.id).status != "done":
        time.sleep(0.02)
    assert storage.jobs.get(ft_job.id).status == "done"


def test_local_dispatcher_shutdown_stops_threads(monkeypatch):
    """shutdown() дожидается текущих задач и останавливает потоки пула
    (тесты не оставляют потоков диспетчера после себя)."""
    from queueing import tasks

    storage = MemoryStorage()
    jobs = [_new_job(storage) for _ in range(3)]

    def slow(job, st, agent):
        time.sleep(0.3)
        st.jobs.finish(job.id, result={"ok": 1}, error=None)

    monkeypatch.setattr(tasks, "run_classify_appeal", slow)
    disp = LocalDispatcher(storage, agent_factory=lambda: "A", max_workers=2)
    for job in jobs:
        disp.send("classify_appeal", {"job_id": job.id}, queue="classify")
    threads = list(disp._threads)
    assert threads

    disp.shutdown(timeout=5)

    assert not any(t.is_alive() for t in threads)
    assert all(storage.jobs.get(j.id).status == "done" for j in jobs)
    with pytest.raises(DispatchError):
        disp.send("classify_appeal", {"job_id": jobs[0].id}, queue="classify")


def test_local_dispatcher_shutdown_waits_for_finetune_thread(monkeypatch):
    from queueing import tasks

    storage = MemoryStorage()
    job = _new_job(storage)

    def slow_finetune(job, st):
        time.sleep(0.3)
        st.jobs.finish(job.id, result={"ok": 1}, error=None)

    monkeypatch.setattr(tasks, "run_finetune", slow_finetune)
    disp = LocalDispatcher(storage, agent_factory=lambda: "A")
    disp.send("finetune", {"job_id": job.id}, queue="training")

    disp.shutdown(timeout=5)

    assert storage.jobs.get(job.id).status == "done"
    assert disp._finetune_threads and not any(t.is_alive() for t in disp._finetune_threads)


def test_local_dispatcher_deletes_embed_annotation_after_success():
    """Как воркер: успешная задача векторов аннотации удаляется сразу."""
    import numpy as np
    from storage.models import ModelVersion

    class Agent:
        def embed_passage(self, text):
            return np.ones(768, dtype=np.float32)

    storage = MemoryStorage()
    storage.models.register(ModelVersion(0, "base", "m0", True))
    vid = storage.classifier.add_annotation("0001.0001.0001.0001", "пояснение", 0)
    ok = storage.jobs.create("embed_annotation", {"vector_id": vid})
    bad = storage.jobs.create("embed_annotation", {"vector_id": 999})
    disp = LocalDispatcher(storage, agent_factory=Agent)
    disp.send("embed_annotation", {"job_id": ok.id}, queue="classify")
    disp.send("embed_annotation", {"job_id": bad.id}, queue="classify")
    disp.shutdown(timeout=5)

    assert storage.jobs.get(ok.id) is None
    assert storage.classifier.pending_annotations(0) == []
    assert storage.jobs.get(bad.id).status == "error"  # ошибка остаётся видна


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
    from core import config

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
        "bad = [m for m in ('torch', 'classifier.classifier_agent', 'sentence_transformers', 'worker.worker_main')"
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


# ── поток LISTEN без PostgreSQL (фейковый psycopg.connect) ───────────────────

class _FakeJobs:
    """jobs без add_listener → JobWaiter идёт по ветке PostgreSQL."""

    def __init__(self, job):
        self.job = job
        self.reads = 0
        self.first_read = threading.Event()

    def get(self, job_id):
        from types import SimpleNamespace

        self.reads += 1
        snapshot = SimpleNamespace(**vars(self.job))  # как строка из БД: снимок
        self.first_read.set()
        return snapshot


class _FakeConn:
    def __init__(self, notifies=None):
        self._notifies = notifies or (lambda timeout: (time.sleep(0.02), [])[1])
        self.info = type("Info", (), {"backend_pid": id(self)})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        return None

    def notifies(self, timeout=1.0):
        return self._notifies(timeout)


def _pending_job():
    from types import SimpleNamespace

    return SimpleNamespace(id="a" * 32, status="queued")  # ожидателю нужен только status


def test_listener_connection_enables_tcp_keepalives(monkeypatch):
    import psycopg
    from types import SimpleNamespace

    seen = []
    stop_after = threading.Event()

    def fake_connect(dsn, **kwargs):
        seen.append(kwargs)
        stop_after.set()
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    waiter = JobWaiter(SimpleNamespace(jobs=object()), dsn="postgresql://x")

    async def scenario():
        await waiter.start()
        await asyncio.to_thread(stop_after.wait, 5)
        await waiter.stop()

    asyncio.run(scenario())
    assert seen, "connect не вызывался"
    kwargs = seen[0]
    assert kwargs["autocommit"] is True
    assert (kwargs["keepalives"], kwargs["keepalives_idle"], kwargs["keepalives_interval"],
            kwargs["keepalives_count"]) == (1, 30, 10, 3)


def test_wake_all_after_reconnect_rechecks_waiters(monkeypatch):
    """После переподключения LISTEN ожидатели перечитывают задачу (NOTIFY мог пропасть)."""
    import psycopg
    from types import SimpleNamespace

    job = _pending_job()
    jobs = _FakeJobs(job)
    connects = {"n": 0}

    def fake_connect(dsn, **kwargs):
        connects["n"] += 1
        if connects["n"] == 1:
            # слушатель «лежит», пока ожидатель не начал ждать; задача
            # завершается без уведомления
            jobs.first_read.wait(5)
            job.status = "done"
            raise psycopg.OperationalError("connection lost")
        return _FakeConn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(waiter_mod, "BACKOFF_START", 0.05)
    waiter = JobWaiter(SimpleNamespace(jobs=jobs), dsn="postgresql://x")

    async def scenario():
        t0 = time.monotonic()
        result = await waiter.wait(job.id, timeout=10, recheck_every=30)
        return result, time.monotonic() - t0

    result, elapsed = asyncio.run(_with_waiter(waiter, scenario))
    assert result is not None and result.status == "done"
    assert connects["n"] >= 2
    assert elapsed < 3.0  # разбудил _WAKE_ALL, а не перепроверка через 30 с


def test_notify_after_loop_closed_is_ignored():
    from types import SimpleNamespace

    waiter = JobWaiter(SimpleNamespace(jobs=object()), dsn="postgresql://x")
    loop = asyncio.new_event_loop()
    loop.close()
    waiter._loop = loop
    waiter._notify_threadsafe("a" * 32)  # без исключения
    waiter._notify_threadsafe(waiter_mod._WAKE_ALL)

    class ClosingLoop:  # закрылся между проверкой и вызовом
        def is_closed(self):
            return False

        def call_soon_threadsafe(self, *args):
            raise RuntimeError("Event loop is closed")

    waiter._loop = ClosingLoop()
    waiter._notify_threadsafe("a" * 32)


def test_restart_does_not_revive_old_listener_thread(monkeypatch):
    """stop() с истёкшим join + start(): старый поток всё равно завершается."""
    import psycopg
    from types import SimpleNamespace

    gate = threading.Event()
    connects = {"n": 0}

    def blocking_notifies(timeout):
        gate.wait(5)  # «завис» дольше, чем stop() ждёт join
        return []

    def fake_connect(dsn, **kwargs):
        connects["n"] += 1
        if connects["n"] == 1:
            return _FakeConn(blocking_notifies)
        return _FakeConn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(waiter_mod, "STOP_JOIN_SEC", 0.1)
    waiter = JobWaiter(SimpleNamespace(jobs=object()), dsn="postgresql://x")

    async def scenario():
        await waiter.start()
        deadline = time.monotonic() + 5
        while not waiter._listening.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)     # поток дошёл до notifies() и «завис»
        old = waiter._thread
        await waiter.stop()           # join истёк: старый поток ещё жив
        assert old.is_alive()
        await waiter.start()          # новый поток
        new = waiter._thread
        assert new is not old
        gate.set()                    # старый поток просыпается
        await asyncio.to_thread(old.join, 3)
        old_alive = old.is_alive()
        new_alive = new.is_alive()
        await waiter.stop()
        return old_alive, new_alive

    old_alive, new_alive = asyncio.run(scenario())
    assert old_alive is False, "старый поток LISTEN ожил после перезапуска"
    assert new_alive is True

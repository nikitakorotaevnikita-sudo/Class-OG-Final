"""Ожидание завершения задачи на стороне API (LISTEN job_done).

`JobWaiter.wait(job_id, timeout)` возвращает конечную задачу (done/error) или
None по таймауту. Пробуждение — по уведомлению `job_done` (payload = id задачи);
страховка — повторное чтение задачи из БД каждые `recheck_every` секунд
(уведомление могло потеряться, например при переподключении).

PostgreSQL: отдельный daemon-поток с синхронным autocommit-соединением
(`LISTEN job_done`, цикл `conn.notifies(timeout=1.0)`). AsyncConnection не
используется: psycopg async не работает на ProactorEventLoop, который uvicorn
берёт на Windows. Уведомления передаются в цикл asyncio через
`call_soon_threadsafe`; при сбое — переподключение с паузой 1 → 10 с.

Memory: слушатель `storage.jobs.add_listener` (эмуляция NOTIFY).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

import config
from storage.models import STATUS_DONE, STATUS_ERROR, Job

log = logging.getLogger(__name__)

_TERMINAL = (STATUS_DONE, STATUS_ERROR)
_WAKE_ALL = None  # «разбудить всех»: после переподключения уведомления могли пропасть

BACKOFF_START = 1.0
BACKOFF_MAX = 10.0
MIN_RECHECK_SEC = 0.05  # нижняя граница recheck_every (без busy-loop)


class JobWaiter:
    def __init__(self, storage, dsn: Optional[str] = None) -> None:
        self._storage = storage
        self._dsn = dsn or config.DATABASE_URL
        self._is_memory = hasattr(storage.jobs, "add_listener")
        self._events: dict[str, set[asyncio.Event]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._listener = None
        # PG-поток
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._listening = threading.Event()
        self._backend_pid: Optional[int] = None

    # ── запуск/остановка ─────────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._is_memory:
            if self._listener is None:
                self._listener = self._notify_threadsafe
                self._storage.jobs.add_listener(self._listener)
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._listen_loop, name="job-done-listener", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        if self._is_memory:
            if self._listener is not None:
                self._storage.jobs.remove_listener(self._listener)
                self._listener = None
            return
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join, 5.0)

    # ── ожидание ─────────────────────────────────────────────────────────────

    async def wait(self, job_id: str, timeout: float,
                   recheck_every: float = 5.0) -> Optional[Job]:
        """Конечная задача или None по таймауту.

        Ошибка БД при ПЕРВОМ чтении поднимается (API ответит 503); сбои при
        последующих перепроверках логируются, ожидание продолжается до таймаута.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        recheck_every = max(recheck_every, MIN_RECHECK_SEC)
        event = asyncio.Event()
        # Регистрируемся ДО чтения: уведомление между чтением и ожиданием не теряется.
        self._events.setdefault(job_id, set()).add(event)
        try:
            first = True
            while True:
                event.clear()
                try:
                    job = await asyncio.to_thread(self._storage.jobs.get, job_id)
                except Exception:
                    if first:
                        raise
                    log.warning("Перепроверка задачи %s не удалась; ждём дальше",
                                job_id, exc_info=True)
                    job = None
                first = False
                if job is not None and job.status in _TERMINAL:
                    return job
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(event.wait(), min(recheck_every, remaining))
                except asyncio.TimeoutError:
                    pass  # плановая перепроверка БД
        finally:
            waiters = self._events.get(job_id)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    del self._events[job_id]

    # ── доставка уведомлений (поток цикла asyncio) ───────────────────────────

    def _on_done(self, job_id: Optional[str]) -> None:
        if job_id is _WAKE_ALL:
            groups = list(self._events.values())
        else:
            groups = [self._events.get(job_id, ())]
        for events in groups:
            for event in list(events):
                event.set()

    def _notify_threadsafe(self, job_id: Optional[str]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._on_done, job_id)
        except RuntimeError:  # цикл закрылся между проверкой и вызовом
            pass

    # ── PostgreSQL: поток LISTEN ─────────────────────────────────────────────

    def _listen_loop(self) -> None:
        import psycopg

        backoff = BACKOFF_START
        while not self._stop.is_set():
            try:
                with psycopg.connect(self._dsn, autocommit=True, connect_timeout=5) as conn:
                    conn.execute("LISTEN job_done")
                    self._backend_pid = conn.info.backend_pid
                    self._listening.set()
                    backoff = BACKOFF_START
                    # Пока слушателя не было, уведомления могли пропасть — перечитать.
                    self._notify_threadsafe(_WAKE_ALL)
                    while not self._stop.is_set():
                        for notify in conn.notifies(timeout=1.0):
                            try:
                                self._notify_threadsafe(notify.payload)
                            except Exception:  # noqa: BLE001 — поток не должен умирать
                                log.exception("Сбой доставки job_done %r", notify.payload)
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    break
                log.warning("LISTEN job_done: соединение потеряно (%s: %s), "
                            "переподключение через %.0f с", type(exc).__name__, exc, backoff)
            finally:
                self._listening.clear()
                self._backend_pid = None
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, BACKOFF_MAX)


_waiter: Optional[JobWaiter] = None
_lock = threading.Lock()


def get_waiter() -> JobWaiter:
    """Singleton по STORAGE_BACKEND (хранилище — общее `get_storage()`)."""
    global _waiter
    if _waiter is None:
        with _lock:
            if _waiter is None:
                from storage import get_storage

                _waiter = JobWaiter(get_storage())
    return _waiter


def reset_waiter() -> None:
    global _waiter
    with _lock:
        _waiter = None

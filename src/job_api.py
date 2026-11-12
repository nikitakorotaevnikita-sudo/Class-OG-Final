"""Постановка задачи и ожидание результата — общая часть тонкого API.

Используется синхронными эндпоинтами (`/classify`, `/classify/file`,
`/classifier/search`) и интеграцией RX. Путь запроса:

1. (в потоке) чистка просроченных задач, затем одна транзакция: карточка
   обращения (если есть текст) + строка `jobs`;
2. отправка задачи брокеру (`dispatcher.send` блокирует до ~10 с при
   недоступном RabbitMQ — тоже в потоке); сбой → задача `error`, ответ 503;
3. `await waiter.wait(job_id, timeout)`: None → 504 (задача продолжает
   выполняться, её id — в тексте ошибки), `error` → 500.

Ошибки БД (psycopg.Error) при создании и при первом чтении → 503.
Здесь же — готовность воркеров классификации (worker_status) для /health и
GET /integration/jobs. Модуль не импортирует classifier_agent / torch.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import psycopg
from fastapi import HTTPException

import config
from queueing import dispatcher as dispatcher_mod
from queueing import waiter as waiter_mod
from queueing.dispatcher import DispatchError
from storage import get_storage
from storage.models import STATUS_ERROR, Job

log = logging.getLogger(__name__)

DB_UNAVAILABLE = "База данных недоступна"


@dataclass
class Submitted:
    job: Job
    appeal_id: Optional[str]


def _create_rows(storage, job_type: str, payload: dict, appeal_text: Optional[str],
                 document_id: Optional[int], ip: Optional[str], webhook_url: Optional[str],
                 max_queued: Optional[int], queue_class: str) -> Submitted:
    storage.jobs.cleanup_expired(config.JOB_TTL_HOURS)
    with storage.transaction() as conn:
        kw = {"conn": conn} if conn is not None else {}
        appeal_id = None
        if appeal_text is not None:
            appeal_id = storage.appeals.create(appeal_text, document_id=document_id,
                                               ip_address=ip, **kw)
        job = storage.jobs.create(job_type, payload, appeal_id=appeal_id,
                                  document_id=document_id, webhook_url=webhook_url,
                                  max_queued=max_queued, queue_class=queue_class, **kw)
    return Submitted(job=job, appeal_id=appeal_id)


async def submit_job(job_type: str, task_name: str, queue: str, payload: dict, *,
                     appeal_text: Optional[str] = None, document_id: Optional[int] = None,
                     ip: Optional[str] = None, webhook_url: Optional[str] = None,
                     max_queued: Optional[int] = None) -> Submitted:
    """Создать карточку (если есть текст) и задачу, отправить её в очередь `queue`.

    `QueueFull` (при `max_queued`) пробрасывается — решение (429) за вызывающим.
    """
    storage = get_storage()
    try:
        sub = await asyncio.to_thread(
            _create_rows, storage, job_type, payload, appeal_text, document_id, ip,
            webhook_url, max_queued, queue)
    except psycopg.Error as exc:
        log.error("Не удалось создать задачу %s: %s", job_type, exc)
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from exc

    msg = {"job_id": sub.job.id, "appeal_id": sub.appeal_id, "document_id": document_id}
    try:
        await asyncio.to_thread(dispatcher_mod.get_dispatcher().send, task_name, msg, queue)
    except DispatchError as exc:
        error = f"Брокер недоступен: {exc}"
        try:
            await asyncio.to_thread(storage.jobs.finish, sub.job.id, None, error)
        except Exception:  # noqa: BLE001 — ответ 503 важнее статуса задачи
            log.exception("Не удалось завершить задачу %s после сбоя брокера", sub.job.id)
        raise HTTPException(status_code=503, detail=error) from exc
    return sub


async def wait_job(job_id: str, timeout: float, *,
                   timeout_text: str = "Классификация не завершилась",
                   error_prefix: str = "Ошибка классификации",
                   pollable: bool = True) -> Job:
    """Дождаться конечной задачи: done → Job; таймаут → 504; error → 500.

    pollable=False — задачу нельзя запросить через /integration/jobs/{id}
    (например, поиск): 504 не ссылается на этот адрес.
    """
    try:
        job = await waiter_mod.get_waiter().wait(job_id, timeout)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from exc
    if job is None:
        if not pollable:
            raise HTTPException(
                status_code=504,
                detail=f"{timeout_text} за {timeout:g} с, повторите запрос позже")
        raise HTTPException(
            status_code=504,
            detail=(f"{timeout_text} за {timeout:g} с, задача {job_id} продолжает "
                    f"выполняться: GET /integration/jobs/{job_id}"))
    if job.status == STATUS_ERROR:
        raise HTTPException(status_code=500, detail=f"{error_prefix}: {job.error}")
    return job


# ── Готовность воркеров классификации (/health, GET /integration/jobs) ────────

_PING_CACHE_SEC = 2.0
_ping_cache: dict = {"at": None, "value": (False, False), "task": None}


def _ping_workers() -> tuple[bool, bool]:
    """(agent_ready, broker): слушает ли очередь классификации хоть один воркер.

    Опрос active_queues вместо ping: воркер дообучения (очередь training) на
    ping тоже отвечает, но классификацию не выполняет.
    """
    try:
        from queueing.celery_app import QUEUE_NAMES, app as celery_app
        replies = celery_app.control.inspect(timeout=1).active_queues() or {}
    except Exception as exc:  # noqa: BLE001 — /health не должен падать
        log.warning("Опрос воркеров Celery не удался: %s", exc)
        return False, False
    classify_queues = set(QUEUE_NAMES) - {"training"}
    ready = any(q.get("name") in classify_queues
                for queues in replies.values() for q in (queues or []))
    return ready, True


async def _probe() -> tuple[bool, bool]:
    value = await asyncio.to_thread(_ping_workers)
    _ping_cache.update(at=time.monotonic(), value=value)
    return value


async def worker_status() -> tuple[bool, bool]:
    """(agent_ready, broker) с кэшем ~2 с и одной проверкой на всех ожидающих.

    Параллельные запросы ждут одну и ту же задачу опроса (single-flight):
    медленный брокер не размножает потоки и не забивает пул to_thread.
    """
    if config.STORAGE_BACKEND == "memory":
        return True, True  # LocalDispatcher: задачи выполняются в процессе API
    at = _ping_cache["at"]
    if at is not None and time.monotonic() - at < _PING_CACHE_SEC:
        return _ping_cache["value"]
    loop = asyncio.get_running_loop()
    task = _ping_cache["task"]
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(_probe())
        _ping_cache["task"] = task
    # shield: отменённый запрос не отменяет общий опрос для остальных
    return await asyncio.shield(task)


def sync_wait_timeout() -> float:
    """SYNC_WAIT_TIMEOUT_SEC читается на каждый запрос (меняется из бэк-офиса)."""
    return float(config.SYNC_WAIT_TIMEOUT_SEC)


async def run_sync_job(job_type: str, task_name: str, payload: dict, *,
                       timeout: Optional[float] = None, queue: str = "classify_sync",
                       appeal_text: Optional[str] = None, document_id: Optional[int] = None,
                       ip: Optional[str] = None,
                       timeout_text: str = "Классификация не завершилась",
                       error_prefix: str = "Ошибка классификации",
                       pollable: bool = True) -> Submitted:
    """submit_job + wait_job. Возвращает Submitted с завершённой (done) задачей."""
    sub = await submit_job(job_type, task_name, queue, payload, appeal_text=appeal_text,
                           document_id=document_id, ip=ip)
    sub.job = await wait_job(sub.job.id, sync_wait_timeout() if timeout is None else timeout,
                             timeout_text=timeout_text, error_prefix=error_prefix,
                             pollable=pollable)
    return sub

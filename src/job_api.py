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
Модуль не импортирует classifier_agent / torch.
"""
from __future__ import annotations

import asyncio
import logging
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
                   error_prefix: str = "Ошибка классификации") -> Job:
    """Дождаться конечной задачи: done → Job; таймаут → 504; error → 500."""
    try:
        job = await waiter_mod.get_waiter().wait(job_id, timeout)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from exc
    if job is None:
        raise HTTPException(
            status_code=504,
            detail=(f"{timeout_text} за {timeout:g} с, задача {job_id} продолжает "
                    f"выполняться: GET /integration/jobs/{job_id}"))
    if job.status == STATUS_ERROR:
        raise HTTPException(status_code=500, detail=f"{error_prefix}: {job.error}")
    return job


def sync_wait_timeout() -> float:
    """SYNC_WAIT_TIMEOUT_SEC читается на каждый запрос (меняется из бэк-офиса)."""
    return float(config.SYNC_WAIT_TIMEOUT_SEC)


async def run_sync_job(job_type: str, task_name: str, payload: dict, *,
                       timeout: Optional[float] = None, queue: str = "classify_sync",
                       appeal_text: Optional[str] = None, document_id: Optional[int] = None,
                       ip: Optional[str] = None,
                       timeout_text: str = "Классификация не завершилась",
                       error_prefix: str = "Ошибка классификации") -> Submitted:
    """submit_job + wait_job. Возвращает Submitted с завершённой (done) задачей."""
    sub = await submit_job(job_type, task_name, queue, payload, appeal_text=appeal_text,
                           document_id=document_id, ip=ip)
    sub.job = await wait_job(sub.job.id, sync_wait_timeout() if timeout is None else timeout,
                             timeout_text=timeout_text, error_prefix=error_prefix)
    return sub

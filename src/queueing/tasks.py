"""Работа воркера над задачами: классификация, поиск, векторы аннотаций, дообучение.

`run_*(job, storage, agent)` получают уже захваченную задачу (`jobs.claim_next` в
воркере `worker.run`, `jobs.claim` в локальном диспетчере) и возвращают, перешла
ли задача в конечный статус сейчас (True/False), или None — попытка больше не
владеет задачей. Параметры задачи — в `job.payload`. Любой конечный исход проходит
через `jobs.finish` (NOTIFY job_done); webhook — после commit, при успешной
доставке задача помечается забранной (`mark_delivered`).

Повторы: инфраструктурный сбой (БД), пока `job.attempts < JOB_MAX_ATTEMPTS`,
поднимается как RetryableError — воркер возвращает задачу в очередь с паузой
(`release_for_retry`). Иначе задача завершается с ошибкой. Сбой БД при записи
самой ошибки — тоже RetryableError.

Воркер умер посреди задачи (OOM, SIGKILL): аренда истекает, задачу забирает
другой воркер; захват увеличивает attempts, и при `attempts > JOB_MAX_ATTEMPTS`
задача завершается ошибкой без запуска работы — бесконечного цикла падений нет.
"""
import logging
from typing import Optional

import psycopg

from core import config
from api import responses
from queueing import webhook

log = logging.getLogger(__name__)

INFRA_ERRORS = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
    ConnectionError,
)


class RetryableError(Exception):
    """Инфраструктурный сбой: задачу нужно повторить позже (release_for_retry)."""


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _finish_error(storage, job_id: str, error: str, attempts: Optional[int] = None) -> bool:
    """Завершить задачу с ошибкой в отдельной транзакции (прежняя откатилась).

    attempts — попытка, которая ещё должна владеть задачей (иначе ничего не делаем).
    Сбой БД при этом — RetryableError: иначе задача навсегда осталась бы running.
    """
    try:
        with storage.transaction() as conn:
            return storage.jobs.finish(job_id, result=None, error=error, conn=conn,
                                       attempts=attempts)
    except INFRA_ERRORS as exc:
        raise RetryableError(_error_text(exc)) from exc


def notify_webhook(storage, job, transitioned: bool = True) -> None:
    """После commit: webhook, если задан и задача действительно завершена сейчас.

    Успешная доставка — результат забран (`delivered_at`), строку удалит чистка.
    """
    if not job.webhook_url or not transitioned:
        return
    try:
        finished = storage.jobs.get(job.id)
        if finished is None:
            return
        if webhook.deliver(job.webhook_url, responses.job_status_payload(finished)):
            storage.jobs.mark_delivered(job.id)
    except Exception:  # noqa: BLE001 — webhook не меняет статус задачи
        log.exception("webhook для задачи %s не отправлен", job.id)


def finish_over_limit(storage, job) -> bool:
    """attempts > JOB_MAX_ATTEMPTS: задача завершается ошибкой без запуска работы.

    Возвращает True, если лимит превышен (работать не нужно).
    """
    if job.attempts <= config.JOB_MAX_ATTEMPTS:
        return False
    log.error("Задача %s: превышено число попыток (%d)", job.id, job.attempts)
    transitioned = _finish_error(
        storage, job.id, f"Превышено число попыток обработки ({job.attempts})",
        attempts=job.attempts)
    notify_webhook(storage, job, transitioned)
    return True


class _NotOwned(Exception):
    """Задачей владеет другая попытка (аренда истекла) — результат отбрасывается."""


def _run(job, storage, work) -> Optional[bool]:
    """Общий каркас: лимит попыток → work(job, conn) в транзакции → finish → webhook.

    `work` возвращает словарь результата либо бросает исключение.

    Владение: транзакция работы начинается с jobs.lock_owned (строка задачи под
    FOR UPDATE, только если она running с attempts этой попытки), а finish —
    условный по attempts. Если аренда тем временем истекла и задачу забрал другой
    воркер, устаревшая попытка откатывает свою транзакцию и ничего не пишет:
    ни карточку, ни статус, ни webhook.
    """
    if finish_over_limit(storage, job):
        return True
    job_id = job.id
    try:
        with storage.transaction() as conn:
            if not storage.jobs.lock_owned(job_id, job.attempts, conn=conn):
                raise _NotOwned
            result = work(job, conn)
            transitioned = storage.jobs.finish(job_id, result=result, error=None,
                                               conn=conn, attempts=job.attempts)
            if not transitioned:
                raise _NotOwned  # откат карточки этой попытки
    except _NotOwned:
        log.warning("Задача %s: попытка %d больше не владеет задачей "
                    "(аренда истекла); результат отброшен", job_id, job.attempts)
        return None
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, INFRA_ERRORS) and job.attempts < config.JOB_MAX_ATTEMPTS:
            log.warning("Задача %s: сбой инфраструктуры (попытка %d/%d): %s",
                        job_id, job.attempts, config.JOB_MAX_ATTEMPTS, exc)
            raise RetryableError(_error_text(exc)) from exc
        log.exception("Задача %s завершилась с ошибкой", job_id)
        transitioned = _finish_error(storage, job_id, _error_text(exc), attempts=job.attempts)
    notify_webhook(storage, job, transitioned)
    return transitioned


class JobDataError(Exception):
    """Ошибка данных задачи (нет текста, нет вектора) — без повтора."""


def run_classify_appeal(job, storage, agent) -> Optional[bool]:
    appeal_id = job.appeal_id

    def work(job, conn):
        text = None if appeal_id is None else storage.appeals.get_text(appeal_id)
        if text is None:
            raise JobDataError(f"текст обращения {appeal_id} не найден")
        payload = job.payload or {}
        result = agent.classify(
            text, payload.get("llm_provider"), payload.get("llm_model"),
            appeal_id=appeal_id, log_conn=conn,
        )
        if payload.get("response_kind", "classify") == "document":
            response = responses.build_document_response(result, job.document_id)
        else:
            response = responses.build_classify_response(
                result, agent.format_for_operator(result),
                appeal_id=payload.get("client_appeal_id"),
                was_truncated=bool(payload.get("was_truncated", False)),
            )
        return response.model_dump(mode="json")

    return _run(job, storage, work)


def run_search_classifier(job, storage, agent) -> Optional[bool]:
    def work(job, conn):
        payload = job.payload or {}
        q = payload.get("q") or ""
        top_k = int(payload.get("top_k") or 5)
        return {"query": q, "results": agent._search_candidates(q)[:top_k]}

    return _run(job, storage, work)


def run_embed_annotation(job, storage, agent) -> Optional[bool]:
    def work(job, conn):
        vector_id = (job.payload or {}).get("vector_id")
        text = None if vector_id is None else storage.classifier.vector_text(vector_id)
        if text is None:
            raise JobDataError(f"вектор аннотации {vector_id} не найден")
        storage.classifier.set_embedding(vector_id, agent.embed_passage(text))
        return {"vector_id": vector_id}

    return _run(job, storage, work)


def _still_owner(storage, job) -> bool:
    """Задача всё ещё принадлежит этой попытке (своя короткая транзакция)."""
    with storage.transaction() as conn:
        return storage.jobs.lock_owned(job.id, job.attempts, conn=conn)


def run_finetune(job, storage, agent=None) -> Optional[bool]:
    """Дообучение идёт вне транзакции (долго); повторов при сбое нет.

    Версию модели регистрирует только владелец задачи: run_training проверяет
    владение (`still_owner`) непосредственно перед регистрацией.
    """
    if finish_over_limit(storage, job):
        return True
    payload = job.payload or {}
    try:
        from training.finetune_model import run_training  # тяжёлый импорт — лениво

        status = run_training(storage, source=payload.get("source", "combined"),
                              force=payload.get("force", False),
                              still_owner=lambda: _still_owner(storage, job))
    except Exception as exc:  # noqa: BLE001 — дообучение не повторяется автоматически
        log.exception("Дообучение (задача %s) упало", job.id)
        transitioned = _finish_error(storage, job.id, _error_text(exc), attempts=job.attempts)
        notify_webhook(storage, job, transitioned)
        return transitioned
    error: Optional[str] = None
    if isinstance(status, dict) and status.get("status") == "failed":
        error = str(status.get("message") or "дообучение не удалось")
    try:
        with storage.transaction() as conn:
            transitioned = storage.jobs.finish(job.id, result=status, error=error,
                                               conn=conn, attempts=job.attempts)
    except INFRA_ERRORS as exc:
        raise RetryableError(_error_text(exc)) from exc
    notify_webhook(storage, job, transitioned)
    return transitioned

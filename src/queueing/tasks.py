"""Задачи Celery-воркера: классификация, поиск, векторы аннотаций, дообучение.

Логика — в обычных функциях `run_*(msg, storage, agent)`: их вызывают тесты и
локальный диспетчер. Celery-задачи лишь подставляют `get_storage()` и агента
процесса (`worker_main.get_agent()`, импорт ленивый — модуль не тянет модель).

Сообщение (ТЗ): `{"job_id", "appeal_id", "document_id"}`; параметры — в `jobs.payload`.
Любой конечный исход проходит через `jobs.finish` (NOTIFY job_done).

Повторы: при acks_late исключение задачи НЕ приводит к повторной доставке
(только падение воркера). Поэтому инфраструктурный сбой (БД/брокер), пока
`job.attempts < JOB_MAX_ATTEMPTS`, поднимается как RetryableError, а
Celery-обёртка превращает его в `self.retry(...)`. Иначе задача завершается
с ошибкой.
"""
import logging
from typing import Optional

import psycopg
from kombu.exceptions import OperationalError as KombuOperationalError

import config
import responses
from queueing import webhook
from queueing.celery_app import app
from storage import get_storage

log = logging.getLogger(__name__)

INFRA_ERRORS = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
    KombuOperationalError,
    ConnectionError,
)


class RetryableError(Exception):
    """Инфраструктурный сбой: задачу нужно повторить (Celery retry)."""


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _finish_error(storage, job_id: str, error: str) -> bool:
    """Завершить задачу с ошибкой в отдельной транзакции (прежняя откатилась)."""
    with storage.transaction() as conn:
        return storage.jobs.finish(job_id, result=None, error=error, conn=conn)


def _notify_webhook(storage, job, transitioned: bool) -> None:
    """После commit: webhook, если задан и задача действительно завершена сейчас."""
    if not job.webhook_url or not transitioned:
        return
    try:
        finished = storage.jobs.get(job.id)
        if finished is None:
            return
        webhook.deliver(job.webhook_url, responses.job_status_payload(finished))
    except Exception:  # noqa: BLE001 — webhook не меняет статус задачи
        log.exception("webhook для задачи %s не отправлен", job.id)


def _run(msg: dict, storage, work) -> None:
    """Общий каркас: claim → work(job, conn) в транзакции → finish → webhook.

    `work` возвращает словарь результата либо бросает исключение.
    """
    job_id = msg["job_id"]
    try:
        job = storage.jobs.claim(job_id)
    except INFRA_ERRORS as exc:
        raise RetryableError(_error_text(exc)) from exc
    if job is None:
        return  # задача уже завершена (повторная доставка) или не существует

    try:
        with storage.transaction() as conn:
            result = work(job, conn)
            transitioned = storage.jobs.finish(job_id, result=result, error=None, conn=conn)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, INFRA_ERRORS) and job.attempts < config.JOB_MAX_ATTEMPTS:
            log.warning("Задача %s: сбой инфраструктуры (попытка %d/%d): %s",
                        job_id, job.attempts, config.JOB_MAX_ATTEMPTS, exc)
            raise RetryableError(_error_text(exc)) from exc
        log.exception("Задача %s завершилась с ошибкой", job_id)
        transitioned = _finish_error(storage, job_id, _error_text(exc))
    _notify_webhook(storage, job, transitioned)


class JobDataError(Exception):
    """Ошибка данных задачи (нет текста, нет вектора) — без повтора."""


def run_classify_appeal(msg: dict, storage, agent) -> None:
    appeal_id = msg["appeal_id"]

    def work(job, conn):
        text = storage.appeals.get_text(appeal_id)
        if text is None:
            raise JobDataError(f"текст обращения {appeal_id} не найден")
        payload = job.payload or {}
        result = agent.classify(
            text, payload.get("llm_provider"), payload.get("llm_model"),
            appeal_id=appeal_id, log_conn=conn,
        )
        if payload.get("response_kind", "classify") == "document":
            document_id = msg.get("document_id")
            if document_id is None:
                document_id = job.document_id
            response = responses.build_document_response(result, document_id)
        else:
            response = responses.build_classify_response(
                result, agent.format_for_operator(result),
                appeal_id=payload.get("client_appeal_id"),
                was_truncated=bool(payload.get("was_truncated", False)),
            )
        return response.model_dump(mode="json")

    _run(msg, storage, work)


def run_search_classifier(msg: dict, storage, agent) -> None:
    def work(job, conn):
        payload = job.payload or {}
        q = payload.get("q") or ""
        top_k = int(payload.get("top_k") or 5)
        return {"query": q, "results": agent._search_candidates(q)[:top_k]}

    _run(msg, storage, work)


def run_embed_annotation(msg: dict, storage, agent) -> None:
    def work(job, conn):
        vector_id = (job.payload or {}).get("vector_id")
        text = None if vector_id is None else storage.classifier.vector_text(vector_id)
        if text is None:
            raise JobDataError(f"вектор аннотации {vector_id} не найден")
        storage.classifier.set_embedding(vector_id, agent.embed_passage(text))
        return {"vector_id": vector_id}

    _run(msg, storage, work)


def run_finetune(msg: dict, storage) -> None:
    job_id = msg["job_id"]
    try:
        job = storage.jobs.claim(job_id)
    except INFRA_ERRORS as exc:
        raise RetryableError(_error_text(exc)) from exc
    if job is None:
        return
    payload = job.payload or {}
    try:
        from finetune_model import run_training  # Task 11; тяжёлый импорт — лениво

        status = run_training(storage, source=payload.get("source", "combined"),
                              force=payload.get("force", False))
    except Exception as exc:  # noqa: BLE001 — дообучение не повторяется автоматически
        log.exception("Дообучение (задача %s) упало", job_id)
        transitioned = _finish_error(storage, job_id, _error_text(exc))
    else:
        error: Optional[str] = None
        if isinstance(status, dict) and status.get("status") == "failed":
            error = str(status.get("message") or "дообучение не удалось")
        with storage.transaction() as conn:
            transitioned = storage.jobs.finish(job_id, result=status, error=error, conn=conn)
    _notify_webhook(storage, job, transitioned)


# ── Celery-обёртки ───────────────────────────────────────────────────────────

def _agent():
    import worker_main  # лениво: тянет classifier_agent (модель)

    return worker_main.get_agent()


def _retrying(task, fn, *args) -> None:
    try:
        fn(*args)
    except RetryableError as exc:
        raise task.retry(exc=exc, countdown=5, max_retries=config.JOB_MAX_ATTEMPTS)


@app.task(name="classify_appeal", bind=True)
def classify_appeal(self, msg: dict) -> None:
    _retrying(self, run_classify_appeal, msg, get_storage(), _agent())


@app.task(name="search_classifier", bind=True)
def search_classifier(self, msg: dict) -> None:
    _retrying(self, run_search_classifier, msg, get_storage(), _agent())


@app.task(name="embed_annotation", bind=True)
def embed_annotation(self, msg: dict) -> None:
    _retrying(self, run_embed_annotation, msg, get_storage(), _agent())


@app.task(name="finetune", bind=True)
def finetune(self, msg: dict) -> None:
    _retrying(self, run_finetune, msg, get_storage())

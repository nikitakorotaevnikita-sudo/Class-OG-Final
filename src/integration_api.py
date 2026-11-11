"""Роутер интеграции с Directum RX.

RX передаёт текст обращения прямо в запросе (`appeal_text`). Если текста нет,
а указан `document_id`, сервис по-прежнему может забрать тело документа из RX
по OData — прежний способ вызова остаётся рабочим.
Формат ответа в обоих случаях одинаковый.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio

import psycopg
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

import config
import job_api
import rx_client
import text_extractor
from responses import (ClassifyDocumentRequest, ClassifyDocumentResponse, AcceptedResponse,
                       JobStatusResponse, job_status_payload)
from queueing import webhook
from storage import get_storage
from storage.models import QueueFull

router = APIRouter(prefix="/integration", tags=["Интеграция RX"])


def _resolve_text(body: ClassifyDocumentRequest) -> str:
    """Текст обращения: из запроса либо из RX по OData.

    Текст в запросе — основной путь. Обращение к OData нужно только тогда,
    когда текста нет, но передан document_id (прежний способ вызова).
    """
    if body.appeal_text is not None:
        text = body.appeal_text
        if not text.strip():
            raise HTTPException(status_code=400, detail="Текст обращения пустой")
        return text

    if body.document_id is not None:
        try:
            text, _filename = rx_client.get_document_text(body.document_id)
        except rx_client.DocumentNotFound:
            raise HTTPException(status_code=404,
                                detail=f"Документ {body.document_id} не найден в RX")
        except rx_client.BodyFetchError as e:
            raise HTTPException(status_code=502,
                                detail=f"Не удалось получить тело документа из RX: {e}")
        except text_extractor.ScanNotSupportedError:
            # Скан без текстового слоя. Не 500: повторять вызов бессмысленно,
            # документ нужно отправлять на распознавание.
            raise HTTPException(
                status_code=400,
                detail=(f"Документ {body.document_id} — скан без текстового слоя. "
                        f"Распознавание (OCR) сервис не выполняет."))
        except text_extractor.TextExtractionError as e:
            # Битый файл, неподдерживаемый формат — тоже не повторяемо.
            raise HTTPException(
                status_code=400,
                detail=f"Из документа {body.document_id} не удалось извлечь текст: {e}")
        if not text or not text.strip():
            raise HTTPException(status_code=400,
                                detail="Документ не содержит извлекаемого текста")
        return text

    raise HTTPException(status_code=400, detail="Нужен appeal_text либо document_id")


_WEBHOOK_REJECTED = "webhook_url не разрешён: хост не входит в WEBHOOK_ALLOWED_HOSTS"
_JOB_TYPE = "classify"


def _document_payload() -> dict:
    return {"llm_provider": None, "llm_model": None, "response_kind": "document"}


async def _db(fn, *args):
    """Обращение к хранилищу вне event loop; сбой БД → 503."""
    try:
        return await asyncio.to_thread(fn, *args)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=job_api.DB_UNAVAILABLE) from exc


@router.post("/classify-document", response_model=ClassifyDocumentResponse)
async def classify_document(body: ClassifyDocumentRequest):
    """Синхронный вызов: ответ приходит по завершении классификации.

    Оставлен без изменений для уже работающей интеграции. Задача идёт через
    очередь `classify_sync`; если ответа нет за SYNC_WAIT_TIMEOUT_SEC — 504, а
    результат можно забрать опросом `GET /integration/jobs/{job_id}`. Если
    вызывающая сторона не может держать соединение минутами, использовать
    `/classify-document-async`.
    """
    text = _resolve_text(body)
    sub = await job_api.run_sync_job("classify", "classify_appeal", _document_payload(),
                                     appeal_text=text, document_id=body.document_id)
    return ClassifyDocumentResponse(**sub.job.result)


@router.post("/classify-document-async", response_model=AcceptedResponse, status_code=202)
async def classify_document_async(body: ClassifyDocumentRequest, response: Response):
    """Принять обращение в обработку и сразу вернуть идентификатор задачи.

    Нужен, чтобы вызывающая сторона не держала процесс заблокированным: модель
    отвечает от десятков секунд до нескольких минут. Результат забирается
    опросом `GET /integration/jobs/{job_id}` либо приходит на `webhook_url`
    (хост должен входить в WEBHOOK_ALLOWED_HOSTS).

    Ошибки в самом запросе (нет текста и document_id, документ не найден,
    webhook не разрешён) возвращаются сразу — до постановки в очередь.
    """
    text = _resolve_text(body)
    if body.webhook_url and not webhook.is_allowed(body.webhook_url):
        raise HTTPException(status_code=400, detail=_WEBHOOK_REJECTED)

    try:
        sub = await job_api.submit_job(
            _JOB_TYPE, "classify_appeal", "classify", _document_payload(),
            appeal_text=text, document_id=body.document_id,
            webhook_url=body.webhook_url or None, max_queued=config.JOB_MAX_QUEUED)
    except QueueFull as e:
        # 429 — вызывающей стороне нужно повторить позже, а не считать это сбоем.
        raise HTTPException(status_code=429, detail=f"Очередь заполнена: {e}")

    # Воркер может подхватить задачу за миллисекунды, поэтому статус читается
    # заново: иначе в ответе оказывалось «queued» без позиции в очереди.
    jobs = get_storage().jobs
    fresh = await _db(jobs.get, sub.job.id) or sub.job
    position = await _db(jobs.position, sub.job.id)
    response.headers["Location"] = f"/integration/jobs/{sub.job.id}"
    return AcceptedResponse(job_id=sub.job.id, status=fresh.status, queue_position=position,
                            poll_url=f"/integration/jobs/{sub.job.id}")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str):
    """Статус задачи и результат, когда он готов.

    Пока задача не завершена, `result` пустой — это не ошибка, а нормальное
    состояние: `queued` (ждёт) или `running` (обрабатывается). Здесь же
    забирается результат синхронного вызова, закончившегося 504.
    """
    jobs = get_storage().jobs
    job = await _db(jobs.get, job_id)
    if job is None or job.job_type != _JOB_TYPE:
        raise HTTPException(status_code=404,
                            detail=f"Задача {job_id} не найдена: неверный идентификатор "
                                   f"либо срок хранения результата истёк")
    position = await _db(jobs.position, job_id)
    # Тело строится вручную: результат у задач /classify и /classify-document
    # разной формы, он отдаётся как сохранён воркером.
    return JSONResponse(job_status_payload(job, queue_position=position))


@router.get("/jobs")
async def list_jobs(limit: int = 20):
    """Сводка по очереди — для диагностики на стенде."""
    jobs = get_storage().jobs
    stats = await _db(jobs.stats)
    items = await _db(lambda: jobs.list(limit=limit, job_type=_JOB_TYPE))
    return {
        "stats": stats,
        "jobs": [
            {
                "job_id": j.id,
                "status": j.status,
                "elapsed_sec": j.elapsed_sec,
                "document_id": j.document_id,
                "error": j.error,
            }
            for j in items
        ],
    }

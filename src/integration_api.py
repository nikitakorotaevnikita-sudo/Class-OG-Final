"""Роутер интеграции с Directum RX.

RX передаёт текст обращения прямо в запросе (`appeal_text`). Если текста нет,
а указан `document_id`, сервис по-прежнему может забрать тело документа из RX
по OData — прежний способ вызова остаётся рабочим.
Формат ответа в обоих случаях одинаковый.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from typing import Optional

import config
import rx_client
from job_queue import JobQueue, QueueFull, STATUS_DONE, STATUS_ERROR

router = APIRouter(prefix="/integration", tags=["Интеграция RX"])


class ClassifyDocumentRequest(BaseModel):
    # Текст обращения — основной способ: RX присылает его сразу
    appeal_text: Optional[str] = None
    # Идентификатор документа: возвращается в ответе как есть; если текста нет,
    # используется для получения тела документа из RX по OData
    document_id: Optional[int] = None


REASONING_MAX_LEN = 1000  # Ограничение длины обоснования в ответе RX (символов)


def _clip_reasoning(text: str) -> str:
    text = text or ""
    return text if len(text) <= REASONING_MAX_LEN else text[:REASONING_MAX_LEN - 1] + "…"


class RxQuestion(BaseModel):
    code: str
    question: str


class ClassifyDocumentResponse(BaseModel):
    document_id: Optional[int]
    applicant_fio: Optional[str]
    applicant_email: Optional[str] = None
    summary: str
    reasoning: str = ""   # Обоснование модели по всем вопросам, сплошным текстом (<=1000 симв.)
    questions: list[RxQuestion]


def _build_reasoning(questions) -> str:
    """Склеить обоснования по всем вопросам в один сплошной текст (<=1000 симв.).

    При нескольких вопросах каждое обоснование префиксуется кодом, чтобы
    было видно, к какому коду оно относится; всё соединяется пробелами.
    """
    parts = []
    multi = len(questions) > 1
    for q in questions:
        r = (q.reasoning or "").strip()
        if not r:
            continue
        parts.append(f"[{q.code}] {r}" if multi else r)
    return _clip_reasoning(" ".join(parts))


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
        if not text or not text.strip():
            raise HTTPException(status_code=400,
                                detail="Документ не содержит извлекаемого текста")
        return text

    raise HTTPException(status_code=400, detail="Нужен appeal_text либо document_id")


def _build_response(agent, text: str, document_id: Optional[int]) -> ClassifyDocumentResponse:
    """Классификация и сборка ответа. Общая для синхронного и фонового путей."""
    result = agent.classify(text)
    return ClassifyDocumentResponse(
        document_id=document_id,
        applicant_fio=result.applicant_fio,
        applicant_email=result.applicant_email,
        summary=result.summary,
        reasoning=_build_reasoning(result.questions),
        questions=[RxQuestion(code=q.code, question=q.name)
                   for q in result.questions],
    )


def _get_agent(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")
    return agent


@router.post("/classify-document", response_model=ClassifyDocumentResponse)
async def classify_document(body: ClassifyDocumentRequest, request: Request):
    """Синхронный вызов: ответ приходит по завершении классификации.

    Оставлен без изменений для уже работающей интеграции. Если вызывающая
    сторона не может держать соединение минутами, использовать
    `/classify-document-async`.
    """
    agent = _get_agent(request)
    text = _resolve_text(body)
    try:
        return _build_response(agent, text, body.document_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка классификации: {str(e)}")


# ── Фоновая обработка ────────────────────────────────────────────────────────

_jobs: Optional[JobQueue] = None


def get_job_queue() -> JobQueue:
    """Очередь создаётся при первом обращении: на старте сервиса она не нужна."""
    global _jobs
    if _jobs is None:
        _jobs = JobQueue(jobs_dir=config.JOBS_DIR,
                         ttl_hours=config.JOB_TTL_HOURS,
                         max_queued=config.JOB_MAX_QUEUED)
    return _jobs


class AcceptedResponse(BaseModel):
    job_id: str
    status: str
    queue_position: Optional[int] = None
    poll_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str                                   # queued | running | done | error
    queue_position: Optional[int] = None          # только пока задача ждёт
    elapsed_sec: float = 0.0
    document_id: Optional[int] = None
    result: Optional[ClassifyDocumentResponse] = None
    error: Optional[str] = None


@router.post("/classify-document-async", response_model=AcceptedResponse, status_code=202)
async def classify_document_async(body: ClassifyDocumentRequest, request: Request,
                                  response: Response):
    """Принять обращение в обработку и сразу вернуть идентификатор задачи.

    Нужен, чтобы вызывающая сторона не держала процесс заблокированным: модель
    отвечает от десятков секунд до нескольких минут. Результат забирается
    опросом `GET /integration/jobs/{job_id}`.

    Ошибки в самом запросе (нет текста и document_id, документ не найден)
    возвращаются сразу, как и раньше — до постановки в очередь.
    """
    agent = _get_agent(request)
    text = _resolve_text(body)
    document_id = body.document_id
    jobs = get_job_queue()

    def work() -> dict:
        return _build_response(agent, text, document_id).model_dump()

    try:
        job = jobs.submit(work, meta={"document_id": document_id,
                                      "text_len": len(text)})
    except QueueFull as e:
        # 429 — вызывающей стороне нужно повторить позже, а не считать это сбоем.
        raise HTTPException(status_code=429, detail=f"Очередь заполнена: {e}")

    # Воркер может подхватить задачу за миллисекунды, поэтому статус читается
    # заново: иначе в ответе оказывалось «queued» без позиции в очереди.
    fresh = jobs.get(job.id) or job
    response.headers["Location"] = f"/integration/jobs/{job.id}"
    return AcceptedResponse(job_id=job.id, status=fresh.status,
                            queue_position=jobs.position(job.id),
                            poll_url=f"/integration/jobs/{job.id}")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str):
    """Статус задачи и результат, когда он готов.

    Пока задача не завершена, `result` пустой — это не ошибка, а нормальное
    состояние: `queued` (ждёт) или `running` (обрабатывается).
    """
    jobs = get_job_queue()
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404,
                            detail=f"Задача {job_id} не найдена: неверный идентификатор "
                                   f"либо срок хранения результата истёк")

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        queue_position=jobs.position(job.id),
        elapsed_sec=job.elapsed_sec,
        document_id=job.meta.get("document_id"),
        result=job.result if job.status == STATUS_DONE else None,
        error=job.error if job.status == STATUS_ERROR else None,
    )


@router.get("/jobs")
async def list_jobs(limit: int = 20):
    """Сводка по очереди — для диагностики на стенде."""
    jobs = get_job_queue()
    return {
        "stats": jobs.stats(),
        "jobs": [
            {
                "job_id": j.id,
                "status": j.status,
                "elapsed_sec": j.elapsed_sec,
                "document_id": j.meta.get("document_id"),
                "error": j.error,
            }
            for j in jobs.list(limit=limit)
        ],
    }

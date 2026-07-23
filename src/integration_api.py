"""Роутер интеграции с Directum RX: классификация документа по id."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

import rx_client

router = APIRouter(prefix="/integration", tags=["Интеграция RX"])


class ClassifyDocumentRequest(BaseModel):
    document_id: int


REASONING_MAX_LEN = 1000  # Ограничение длины обоснования в ответе RX (символов)


def _clip_reasoning(text: str) -> str:
    text = text or ""
    return text if len(text) <= REASONING_MAX_LEN else text[:REASONING_MAX_LEN - 1] + "…"


class RxQuestion(BaseModel):
    code: str
    question: str


class ClassifyDocumentResponse(BaseModel):
    document_id: int
    applicant_fio: Optional[str]
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


@router.post("/classify-document", response_model=ClassifyDocumentResponse)
async def classify_document(body: ClassifyDocumentRequest, request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")

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

    try:
        result = agent.classify(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка классификации: {str(e)}")

    return ClassifyDocumentResponse(
        document_id=body.document_id,
        applicant_fio=result.applicant_fio,
        summary=result.summary,
        reasoning=_build_reasoning(result.questions),
        questions=[RxQuestion(code=q.code, question=q.name)
                   for q in result.questions],
    )

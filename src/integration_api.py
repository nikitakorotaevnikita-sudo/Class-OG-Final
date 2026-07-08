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


class RxQuestion(BaseModel):
    code: str
    question: str


class ClassifyDocumentResponse(BaseModel):
    document_id: int
    applicant_fio: Optional[str]
    summary: str
    questions: list[RxQuestion]


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
        questions=[RxQuestion(code=q.code, question=q.name) for q in result.questions],
    )

"""
FastAPI-сервер для агента классификации обращений граждан.
Предоставляет REST API для интеграции с Directum RX и другими системами.

Запуск:
    uvicorn src.api_server:app --host 0.0.0.0 --port 8000 --reload

Документация API:
    http://localhost:8000/docs
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from dataclasses import asdict
from classifier_agent import ClassifierAgent, ClassificationResult

app = FastAPI(
    title="Агент классификации обращений граждан",
    description="Автоматическая классификация по 59-ФЗ и классификатору обращений граждан РФ",
    version="1.0.0",
)

# Инициализация агента при старте сервера
agent: Optional[ClassifierAgent] = None

@app.on_event("startup")
async def startup():
    global agent
    agent = ClassifierAgent()
    print("Агент классификации запущен и готов к работе.")


# ── Схемы запросов/ответов ─────────────────────────────────────────────────────

class ClassifyRequest(BaseModel):
    appeal_text: str
    appeal_id: Optional[str] = None        # ID обращения в Directum RX (опционально)
    operator_comment: Optional[str] = None # Дополнительный контекст от оператора

class AlternativeItem(BaseModel):
    code: str
    name: str
    full_path: str

class QuestionResult(BaseModel):
    question_text: str
    code: str
    name: str
    level: int
    full_path: str
    predmet_vedeniya: str
    confidence: float
    reasoning: str
    alternatives: list[AlternativeItem]

class ClassifyResponse(BaseModel):
    appeal_id: Optional[str]
    log_id: Optional[str]            # Для последующего /verify
    vid_obrascheniya: str
    tip_obrascheniya: str
    is_ustnoe: bool
    questions: list[QuestionResult]
    overall_confidence: float
    needs_verification: bool
    operator_card: str              # Текст карточки верификации для оператора


# ── Эндпоинты ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Проверка работоспособности сервера"""
    entries_count = len(agent.metadata) if agent else 0
    return {
        "status": "ok",
        "agent_ready": agent is not None,
        "classifier_entries": entries_count,
    }


@app.post("/classify", response_model=ClassifyResponse)
async def classify_appeal(request: ClassifyRequest):
    """
    Классифицировать обращение гражданина.

    Возвращает:
    - Вид и тип обращения (по 59-ФЗ)
    - Классификацию по классификатору обращений (код, наименование, путь)
    - Предмет ведения
    - Уровень уверенности
    - Флаг необходимости верификации оператором
    - Текст карточки для оператора
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")

    if not request.appeal_text or len(request.appeal_text.strip()) < 10:
        raise HTTPException(status_code=400, detail="Текст обращения слишком короткий")

    try:
        result: ClassificationResult = agent.classify(request.appeal_text)
        operator_card = agent.format_for_operator(result)

        questions_out = [
            QuestionResult(
                question_text=q.question_text,
                code=q.code,
                name=q.name,
                level=q.level,
                full_path=q.full_path,
                predmet_vedeniya=q.predmet_vedeniya,
                confidence=q.confidence,
                reasoning=q.reasoning,
                alternatives=[AlternativeItem(**a) for a in q.alternatives],
            )
            for q in result.questions
        ]

        return ClassifyResponse(
            appeal_id=request.appeal_id,
            log_id=result.log_id,
            vid_obrascheniya=result.vid_obrascheniya,
            tip_obrascheniya=result.tip_obrascheniya,
            is_ustnoe=result.is_ustное,
            questions=questions_out,
            overall_confidence=result.overall_confidence,
            needs_verification=result.needs_verification,
            operator_card=operator_card,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка классификации: {str(e)}")


@app.get("/classifier/search")
async def search_classifier(q: str, top_k: int = 5):
    """
    Поиск по классификатору (для отладки и ручного поиска).
    Пример: /classifier/search?q=вывоз мусора&top_k=5
    """
    if not agent:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")

    candidates = agent._search_candidates(q)
    return {"query": q, "results": candidates[:top_k]}

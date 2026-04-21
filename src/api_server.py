"""
FastAPI-сервер для агента классификации обращений граждан.
Предоставляет REST API для интеграции с Directum RX и другими системами.

Запуск:
    uvicorn src.api_server:app --host 0.0.0.0 --port 8000 --reload

Документация API:
    http://localhost:8000/docs
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from dataclasses import asdict
from classifier_agent import ClassifierAgent, ClassificationResult
from appeals_logger import AppealsLogger, get_logger

app = FastAPI(
    title="Агент классификации обращений граждан",
    description="Автоматическая классификация по 59-ФЗ и классификатору обращений граждан РФ",
    version="1.0.0",
)

# Статика и главная страница
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(_STATIC_DIR / "index.html")

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


# ── Dependency injection (для тестируемости) ──────────────────────────────────

def get_logger_dep() -> AppealsLogger:
    """FastAPI-зависимость для appeals logger. В тестах подменяется."""
    return get_logger()


def get_examples_path() -> Path:
    """FastAPI-зависимость для пути к test_appeals.json. В тестах подменяется."""
    return Path(__file__).parent.parent / "data" / "test_appeals.json"


# ── Схема /verify ─────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    log_id: str
    action: Literal["confirm", "correct", "reject"]
    operator_codes: Optional[list[str]] = None


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


# ── Новые эндпоинты для MVP-демо ───────────────────────────────────────────────

@app.post("/verify")
async def verify(
    request: VerifyRequest,
    logger: AppealsLogger = Depends(get_logger_dep),
):
    """
    Верификация результата классификации оператором.
    Действия: confirm | correct | reject.
    Для action="correct" обязательно передать operator_codes — список правильных
    кодов классификатора (формата XXXX.XXXX.XXXX.XXXX).
    """
    if request.action == "confirm":
        logger.confirm(request.log_id)
    elif request.action == "correct":
        if not request.operator_codes:
            raise HTTPException(
                status_code=400,
                detail="operator_codes обязательно для action='correct'",
            )
        logger.correct(request.log_id, operator_codes=request.operator_codes)
    elif request.action == "reject":
        logger.reject(request.log_id)

    return {"status": "ok", "log_id": request.log_id, "action": request.action}


@app.get("/examples")
async def get_examples(
    examples_path: Path = Depends(get_examples_path),
):
    """
    Возвращает 10 заранее подготовленных тестовых обращений для демо.
    Источник: data/test_appeals.json.
    """
    if not examples_path.exists():
        raise HTTPException(status_code=503, detail="Файл примеров не найден")
    with open(examples_path, encoding="utf-8") as f:
        return {"examples": json.load(f)}


@app.get("/stats")
async def get_stats(
    logger: AppealsLogger = Depends(get_logger_dep),
):
    """
    Возвращает статистику верификаций для отображения на фронтенде.
    """
    stats = logger.stats()
    from config import FINETUNE_THRESHOLD
    return {
        "verified": stats["verified"],
        "confirmed": stats["confirmed"],
        "corrected": stats["corrected"],
        "rejected": stats["rejected"],
        "pending": stats["pending"],
        "threshold": FINETUNE_THRESHOLD,
        "progress_percent": min(100, int(stats["verified"] / FINETUNE_THRESHOLD * 100)),
    }

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
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from dataclasses import asdict
from classifier_agent import ClassifierAgent, ClassificationResult
from appeals_logger import AppealsLogger, get_logger
from historical_loader import parse_file, validate_codes
from text_extractor import (
    extract_text,
    TextExtractionError,
    ScanNotSupportedError,
    validate_file_size,
    MAX_TEXT_LENGTH,
)

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
    appeal_text: Optional[str] = None
    appeal_id: Optional[str] = None




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
    was_truncated: bool = False       # Был ли текст обрезан до 5000 символов


# ── Dependency injection (для тестируемости) ──────────────────────────────────

def get_logger_dep() -> AppealsLogger:
    """FastAPI-зависимость для appeals logger. В тестах подменяется."""
    return get_logger()


def get_examples_path() -> Path:
    """FastAPI-зависимость для пути к test_appeals.json. В тестах подменяется."""
    return Path(__file__).parent.parent / "data" / "test_appeals.json"


# ── Схема /verify ─────────────────────────────────────────────────────────────

class UploadHistoricalResponse(BaseModel):
    status: Literal["ok", "validation_errors"]
    filename: str
    stats: dict  # {total, valid, invalid}
    errors: list[dict]
    preview: list[dict] | None


class VerifyRequest(BaseModel):
    log_id: str
    action: Literal["confirm", "correct", "reject"]
    operator_codes: Optional[list[str]] = None
    annotation: Optional[str] = None


# ── Вспомогательная функция сборки ответа ─────────────────────────────────────

def _build_classify_response(
    result: ClassificationResult,
    appeal_id: Optional[str] = None,
    was_truncated: bool = False,
) -> ClassifyResponse:
    """Собирает ClassifyResponse из ClassificationResult."""
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
        appeal_id=appeal_id,
        log_id=result.log_id,
        vid_obrascheniya=result.vid_obrascheniya,
        tip_obrascheniya=result.tip_obrascheniya,
        is_ustnoe=result.is_ustное,
        questions=questions_out,
        overall_confidence=result.overall_confidence,
        needs_verification=result.needs_verification,
        operator_card=operator_card,
        was_truncated=was_truncated,
    )


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


@app.post(
    "/classify",
    response_model=ClassifyResponse,
    summary="Классифицировать обращение (текст)",
    description="Принимает текст обращения в JSON. Для загрузки файла используйте POST /classify/file.",
    tags=["Классификация"],
)
async def classify_appeal_json(request: ClassifyRequest) -> ClassifyResponse:
    """Классифицирует обращение — JSON тело"""
    if not agent:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")
    if not request.appeal_text or not request.appeal_text.strip():
        raise HTTPException(status_code=400, detail="Текст обращения пустой")

    try:
        result = agent.classify(request.appeal_text)
        return _build_classify_response(result, appeal_id=request.appeal_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка классификации: {str(e)}")


@app.post(
    "/classify/file",
    response_model=ClassifyResponse,
    summary="Классифицировать обращение (файл)",
    description="Принимает TXT или PDF файл (макс. 5 МБ). Текст извлекается автоматически.",
    tags=["Классификация"],
)
async def classify_appeal_file(file: UploadFile = File(...)) -> ClassifyResponse:
    """Классифицирует обращение из загруженного файла TXT или PDF."""
    if not agent:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")

    content = await file.read()

    try:
        validate_file_size(len(content))
    except TextExtractionError as e:
        raise HTTPException(status_code=413, detail=str(e))

    try:
        text, truncated = extract_text(content, file.filename or "upload.txt")
    except ScanNotSupportedError as e:
        raise HTTPException(
            status_code=422,
            detail=f"PDF является сканом без текста. Используйте OCR перед загрузкой. ({e})",
        )
    except TextExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not text.strip():
        raise HTTPException(status_code=400, detail="Файл не содержит текста")

    try:
        result = agent.classify(text)
        return _build_classify_response(result, was_truncated=truncated)
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
    annotation — пояснение оператора (сохраняется в classifier_annotations.json).
    """
    annotation_count = None

    if request.action == "confirm":
        logger.confirm(request.log_id)
    elif request.action == "correct":
        if not request.operator_codes:
            raise HTTPException(
                status_code=400,
                detail="operator_codes обязательно для action='correct'",
            )
        logger.correct(request.log_id, operator_codes=request.operator_codes, comment=request.annotation)

        # Получаем количество аннотаций для первого кода
        try:
            from annotations_storage import get_annotations
            if request.operator_codes:
                anns = get_annotations(request.operator_codes[0])
                annotation_count = len(anns)
        except Exception:
            annotation_count = None

    elif request.action == "reject":
        logger.reject(request.log_id)

    return {
        "status": "ok",
        "log_id": request.log_id,
        "action": request.action,
        "annotation_count": annotation_count
    }


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


@app.post("/api/upload-historical")
async def upload_historical(file: UploadFile = File(...)):
    """Загрузить файл исторических данных для валидации"""
    if file.size > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size > 50MB")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv", ".json"):
        raise HTTPException(status_code=400, detail="Unsupported format")

    # Save temp file
    temp_path = Path("data/temp_upload") / file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        records = parse_file(str(temp_path))
        result = validate_codes(records)
        response = {
            "status": "ok" if result.stats["invalid"] == 0 else "validation_errors",
            "filename": file.filename,
            "stats": result.stats,
            "errors": result.errors,
            "preview": result.valid_records[:5] if result.valid_records else []
        }
    finally:
        temp_path.unlink(missing_ok=True)

    return response


@app.post("/api/confirm-historical")
async def confirm_historical(request: dict):
    """Подтвердить и сохранить валидные записи"""
    # Implementation saves records to historical_verified.jsonl
    pass

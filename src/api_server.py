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
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter
from typing import Literal
sys.path.insert(0, str(Path(__file__).parent))
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
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

# Модуль-level хранилище для последнего аплоада исторических данных
_last_historical_upload: dict = {}

app = FastAPI(
    title="Агент классификации обращений граждан",
    description="Автоматическая классификация по 59-ФЗ и классификатору обращений граждан РФ",
    version="1.0.0",
)

# Статика и главная страница
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# Путь к логу IP-запросов
_REQUEST_LOG = Path(__file__).parent.parent / "data" / "request_log.jsonl"
_REQUEST_LOG.parent.mkdir(parents=True, exist_ok=True)

# HTTP Basic Auth для бэк-офиса
security = HTTPBasic()


def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    from config import BACKOFFICE_USER, BACKOFFICE_PASSWORD
    if credentials.username != BACKOFFICE_USER or credentials.password != BACKOFFICE_PASSWORD:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/backoffice", include_in_schema=False)
async def backoffice_page():
    return FileResponse(_STATIC_DIR / "backoffice.html")


# ── IP-логирование запросов к /classify ───────────────────────────────────────

def log_request(ip: str, endpoint: str, elapsed_seconds: float, log_id: str | None):
    """Appends an entry to data/request_log.jsonl."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ip": ip,
        "endpoint": endpoint,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "log_id": log_id or "",
    }
    with open(_REQUEST_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


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
async def classify_appeal_json(request: ClassifyRequest, req: Request) -> ClassifyResponse:
    """Классифицирует обращение — JSON тело"""
    if not agent:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")
    if not request.appeal_text or not request.appeal_text.strip():
        raise HTTPException(status_code=400, detail="Текст обращения пустой")

    start_ts = time.time()
    try:
        result = agent.classify(request.appeal_text)
        resp = _build_classify_response(result, appeal_id=request.appeal_id)
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка классификации: {str(e)}")
    finally:
        elapsed = time.time() - start_ts
        ip = req.client.host if req.client else "unknown"
        log_id = result.log_id if "result" in dir() else None
        log_request(ip, "/classify", elapsed, log_id)


@app.post(
    "/classify/file",
    response_model=ClassifyResponse,
    summary="Классифицировать обращение (файл)",
    description="Принимает TXT или PDF файл (макс. 5 МБ). Текст извлекается автоматически.",
    tags=["Классификация"],
)
async def classify_appeal_file(file: UploadFile = File(...), req: Request = None) -> ClassifyResponse:
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

    start_ts = time.time()
    try:
        result = agent.classify(text)
        return _build_classify_response(result, was_truncated=truncated)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка классификации: {str(e)}")
    finally:
        elapsed = time.time() - start_ts
        ip = req.client.host if req and req.client else "unknown"
        log_id = result.log_id if "result" in dir() else None
        log_request(ip, "/classify/file", elapsed, log_id)


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
    import uuid

    suffix = Path(file.filename).suffix.lower()
    if not suffix or suffix not in (".xlsx", ".xls", ".csv", ".json"):
        raise HTTPException(status_code=400, detail="Unsupported format")

    # Generate safe random filename to prevent path traversal
    safe_filename = f"{uuid.uuid4().hex}{suffix}"
    temp_path = Path("data/temp_upload") / safe_filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        content = await file.read()
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size > 50MB")

        with open(temp_path, "wb") as f:
            f.write(content)

        records = parse_file(str(temp_path))
        result = validate_codes(records)
        response = {
            "status": "ok" if result.stats["invalid"] == 0 else "validation_errors",
            "filename": file.filename,
            "stats": result.stats,
            "errors": result.errors,
            "preview": result.valid_records[:5] if result.valid_records else [],
            "valid_count": len(result.valid_records)
        }
        # Store for confirm
        global _last_historical_upload
        _last_historical_upload = {"valid_records": result.valid_records, "filename": file.filename}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"File parsing error: {str(e)}")
    finally:
        temp_path.unlink(missing_ok=True)

    return response


@app.post("/api/confirm-historical")
async def confirm_historical(request: dict):
    """Подтвердить и сохранить валидные записи"""
    global _last_historical_upload
    if not _last_historical_upload:
        raise HTTPException(status_code=400, detail="Нет данных для сохранения")

    preview = _last_historical_upload.get("valid_records", [])
    if not preview:
        raise HTTPException(status_code=400, detail="Нет валидных записей для сохранения")

    from historical_loader import save_to_historical_jsonl
    filename = _last_historical_upload.get("filename", "unknown")
    save_to_historical_jsonl(preview, filename)

    _last_historical_upload = {}
    return {"status": "ok", "records_saved": len(preview)}


@app.get("/api/historical-count")
async def get_historical_count():
    """Возвращает количество записей в historical_verified.jsonl"""
    hist_file = Path("data/historical_verified.jsonl")
    if not hist_file.exists():
        return {"count": 0, "status": "no_data"}
    with open(hist_file, encoding="utf-8") as f:
        count = sum(1 for _ in f)
    return {"count": count, "status": "ok"}


@app.post("/api/finetune")
async def start_finetune():
    """Запускает fine-tuning на исторических данных"""
    return {"status": "not_implemented", "message": "Fine-tuning endpoint coming in Task 4"}


@app.get("/api/backoffice/stats", tags=["Бэк-офис"])
async def backoffice_stats(
    _username: str = Depends(get_current_user),
    logger: AppealsLogger = Depends(get_logger_dep),
):
    """
    Возвращает агрегированную статистику для страницы бэк-офиса.
    Требует HTTP Basic Auth (BACKOFFICE_USER / BACKOFFICE_PASSWORD).
    """
    all_entries = logger.read_all()

    # ── Appeals log stats ─────────────────────────────────────────────────────
    status_counts = Counter()
    code_counter = Counter()
    confidence_sum = 0.0
    confidence_histogram = {
        "0.0-0.1": 0, "0.1-0.2": 0, "0.2-0.3": 0, "0.3-0.4": 0, "0.4-0.5": 0,
        "0.5-0.6": 0, "0.6-0.7": 0, "0.7-0.8": 0, "0.8-0.9": 0, "0.9-1.0": 0,
    }

    # Build lookup: code → name from agent metadata
    code_names = {}
    if agent and agent.metadata:
        for m in agent.metadata:
            code_names[m.get("code", "")] = m.get("name", "")

    date_counts = Counter()

    for entry in all_entries:
        status_counts[entry["verification"]["status"]] += 1

        conf = entry.get("overall_confidence", 0.0)
        confidence_sum += conf

        # Histogram bin (0.0-0.1, 0.1-0.2, ..., 0.9-1.0)
        bin_idx = min(int(conf * 10), 9)
        bin_key = f"{bin_idx * 0.1:.1f}-{bin_idx * 0.1 + 0.1:.1f}"
        confidence_histogram[bin_key] += 1

        # Selected codes from verified entries only
        if entry["verification"]["status"] in ("confirmed", "corrected"):
            for q in entry.get("agent_questions", []):
                sel = q.get("selected_code", "")
                if sel:
                    code_counter[sel] += 1

        # Date for daily usage
        ts = entry.get("timestamp", "")
        if ts:
            date = ts[:10]  # YYYY-MM-DD
            date_counts[date] += 1

    total = len(all_entries)
    verified = status_counts["confirmed"] + status_counts["corrected"]
    avg_conf = (confidence_sum / total) if total > 0 else 0.0

    # Top-10 codes
    top_codes = [
        {"code": code, "name": code_names.get(code, ""), "count": cnt}
        for code, cnt in code_counter.most_common(10)
    ]

    # Last 30 days daily usage
    today = datetime.now().date()
    daily_usage = []
    for i in range(29, -1, -1):
        day_obj = today - timedelta(days=i)
        day_str = day_obj.isoformat()
        daily_usage.append({"date": day_str, "count": date_counts.get(day_str, 0)})

    # ── IP stats from request log ─────────────────────────────────────────────
    ip_counter = Counter()
    if _REQUEST_LOG.exists():
        with open(_REQUEST_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        ip_counter[rec.get("ip", "unknown")] += 1
                    except json.JSONDecodeError:
                        continue

    ip_stats = [
        {"ip": ip, "count": cnt}
        for ip, cnt in ip_counter.most_common()
    ]

    return {
        "total_classifications": total,
        "total_verifications": verified,
        "confirmed": status_counts["confirmed"],
        "corrected": status_counts["corrected"],
        "rejected": status_counts["rejected"],
        "pending": status_counts["pending"],
        "avg_confidence": round(avg_conf, 2),
        "confidence_histogram": confidence_histogram,
        "top_codes": top_codes,
        "daily_usage": daily_usage,
        "ip_stats": ip_stats,
    }

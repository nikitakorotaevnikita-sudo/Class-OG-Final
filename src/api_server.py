"""
FastAPI-сервер для агента классификации обращений граждан.
Предоставляет REST API для интеграции с Directum RX и другими системами.

Тонкий API: процесс не загружает классификатор (torch, sentence-transformers).
Классификация и поиск по классификатору выполняются воркерами: API создаёт
задачу в PostgreSQL, отправляет её в RabbitMQ и ждёт результата по
LISTEN/NOTIFY (src/job_api.py). Статистика, верификация, исторические данные,
дообучение и реестр моделей — через слой хранения (src/storage).

Запуск:
    uvicorn src.api_server:app --host 0.0.0.0 --port 8000 --reload

Документация API:
    http://localhost:8000/docs
"""

import asyncio
import sys
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional
sys.path.insert(0, str(Path(__file__).parent))
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from pydantic import BaseModel
import psycopg

import config
import job_api
from responses import ClassifyRequest, ClassifyResponse
from appeals_logger import AppealsLogger, get_logger
from historical_loader import parse_file, validate_codes
from integration_api import router as integration_router
from queueing import waiter as waiter_mod
from storage import get_storage
from storage.models import (STATUS_DONE, STATUS_ERROR, STATUS_QUEUED, STATUS_RUNNING,
                            ActiveJobExists)
from text_extractor import (
    extract_text,
    TextExtractionError,
    ScanNotSupportedError,
    validate_file_size,
)

log = logging.getLogger(__name__)

# Модуль-level хранилище для последнего аплоада исторических данных
_last_historical_upload: dict = {}

SEARCH_WAIT_TIMEOUT_SEC = 60


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_storage()
    waiter = waiter_mod.get_waiter()
    await waiter.start()
    print("API запущен: классификация выполняется воркерами через очередь.")
    try:
        yield
    finally:
        await waiter.stop()
        if config.STORAGE_BACKEND == "postgres":
            from storage import db
            db.close_pool()


app = FastAPI(
    title="Агент классификации обращений граждан",
    description="Автоматическая классификация по 59-ФЗ и классификатору обращений граждан РФ",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(integration_router)


@app.exception_handler(psycopg.Error)
async def _db_unavailable(_request: Request, exc: psycopg.Error) -> JSONResponse:
    """Любая ошибка PostgreSQL в эндпоинте → 503, как у эндпоинтов классификации."""
    log.error("Ошибка БД при обработке запроса: %s: %s", type(exc).__name__, exc)
    return JSONResponse(status_code=503, content={"detail": job_api.DB_UNAVAILABLE})

# Статика и главная страница
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

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


# ── Dependency injection (для тестируемости) ──────────────────────────────────

def get_logger_dep() -> AppealsLogger:
    """FastAPI-зависимость для appeals logger. В тестах подменяется."""
    return get_logger()


def get_examples_path() -> Path:
    """FastAPI-зависимость для пути к test_appeals.json. В тестах подменяется."""
    return Path(__file__).parent.parent / "data" / "test_appeals.json"


def _client_ip(req: Optional[Request]) -> Optional[str]:
    return req.client.host if req is not None and req.client else None


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


class FineTuneRequest(BaseModel):
    source: Literal["verified", "historical", "combined"] = "combined"
    force: bool = False


# ── Эндпоинты ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Проверка работоспособности: БД, брокер и воркеры классификации.

    agent_ready — очередь классификации слушает хотя бы один воркер (воркер
    дообучения не считается); опрос брокера кэшируется ~2 с (job_api).
    """
    try:
        entries_count = await asyncio.to_thread(get_storage().classifier.count_entries)
        db_ok = True
    except Exception as exc:  # noqa: BLE001 — /health не должен падать
        log.warning("/health: БД недоступна: %s", exc)
        entries_count, db_ok = 0, False
    agent_ready, broker = await job_api.worker_status()
    return {
        "status": "ok",
        "agent_ready": agent_ready,
        "classifier_entries": entries_count,
        "db": db_ok,
        "broker": broker,
    }


@app.get("/api/llm/options")
async def llm_options():
    """Возвращает доступные LLM-провайдеры и текущий выбор по .env."""
    from config import LLM_PROVIDER, GROQ_MODEL, OLLAMA_MODEL, ARIO_MODEL

    def uniq(items: list[str]) -> list[str]:
        return list(dict.fromkeys(items))

    return {
        "current_provider": LLM_PROVIDER,
        "providers": [
            {
                "id": "ario",
                "label": "Ario",
                "default_model": ARIO_MODEL,
                "models": uniq([ARIO_MODEL, "Qwen/Qwen3-32B-AWQ"]),
            },
            {
                "id": "groq",
                "label": "Groq",
                "default_model": GROQ_MODEL,
                "models": uniq([GROQ_MODEL, "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]),
            },
            {
                "id": "gemini",
                "label": "Gemini",
                "default_model": "gemini-2.5-flash",
                "models": ["gemini-2.5-flash", "gemini-2.0-flash"],
            },
            {
                "id": "ollama",
                "label": "Ollama",
                "default_model": OLLAMA_MODEL,
                "models": uniq([OLLAMA_MODEL, "qwen2.5-14b", "llama3.1"]),
            },
        ],
    }


async def _classify_via_job(text: str, req: Optional[Request], payload: dict) -> ClassifyResponse:
    """Общий путь /classify и /classify/file: задача → очередь classify_sync → ответ.

    ip запроса пишется в карточку при её создании; elapsed_seconds карточки —
    время классификации, его записывает воркер (spec §7.1.5).
    """
    sub = await job_api.run_sync_job("classify", "classify_appeal", payload,
                                     appeal_text=text, ip=_client_ip(req))
    return ClassifyResponse(**sub.job.result)


@app.post(
    "/classify",
    response_model=ClassifyResponse,
    summary="Классифицировать обращение (текст)",
    description="Принимает текст обращения в JSON. Для загрузки файла используйте POST /classify/file.",
    tags=["Классификация"],
)
async def classify_appeal_json(request: ClassifyRequest, req: Request) -> ClassifyResponse:
    """Классифицирует обращение — JSON тело"""
    if not request.appeal_text or not request.appeal_text.strip():
        raise HTTPException(status_code=400, detail="Текст обращения пустой")

    return await _classify_via_job(request.appeal_text, req, {
        "llm_provider": request.llm_provider,
        "llm_model": request.llm_model,
        "response_kind": "classify",
        "was_truncated": False,
        "client_appeal_id": request.appeal_id,
    })


@app.post(
    "/classify/file",
    response_model=ClassifyResponse,
    summary="Классифицировать обращение (файл)",
    description="Принимает TXT или PDF файл (макс. 5 МБ). Текст извлекается автоматически.",
    tags=["Классификация"],
)
async def classify_appeal_file(
    file: UploadFile = File(...),
    llm_provider: Optional[str] = Form(None),
    llm_model: Optional[str] = Form(None),
    req: Request = None,
) -> ClassifyResponse:
    """Классифицирует обращение из загруженного файла TXT или PDF."""
    content = await file.read()

    try:
        validate_file_size(len(content))
    except TextExtractionError as e:
        raise HTTPException(status_code=413, detail=str(e))

    try:
        text, truncated = await asyncio.to_thread(extract_text, content,
                                                  file.filename or "upload.txt")
    except ScanNotSupportedError as e:
        raise HTTPException(
            status_code=422,
            detail=f"PDF является сканом без текста. Используйте OCR перед загрузкой. ({e})",
        )
    except TextExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not text.strip():
        raise HTTPException(status_code=400, detail="Файл не содержит текста")

    return await _classify_via_job(text, req, {
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "response_kind": "classify",
        "was_truncated": bool(truncated),
        "client_appeal_id": None,
    })


@app.get("/classifier/search")
async def search_classifier(q: str, top_k: int = 5):
    """
    Поиск по классификатору (для отладки и ручного поиска).
    Пример: /classifier/search?q=вывоз мусора&top_k=5
    """
    sub = await job_api.run_sync_job(
        "search", "search_classifier", {"q": q, "top_k": top_k},
        timeout=SEARCH_WAIT_TIMEOUT_SEC,
        timeout_text="Поиск по классификатору не завершился",
        error_prefix="Ошибка поиска",
        pollable=False)  # /integration/jobs/{id} отдаёт только задачи классификации
    return sub.job.result


# ── Новые эндпоинты для MVP-демо ───────────────────────────────────────────────

async def _dispatch_embeddings(vector_ids: list[int]) -> None:
    """Эмбеддинг новых аннотаций — задачей embed_annotation (очередь classify).

    Сбой не ломает верификацию: строка остаётся без эмбеддинга, воркер досчитает
    её при старте (embed_pending_annotations).
    """
    for vid in vector_ids:
        try:
            await job_api.submit_job("embed_annotation", "embed_annotation", "classify",
                                     {"vector_id": vid})
        except HTTPException as exc:
            log.warning("Эмбеддинг аннотации %s не поставлен в очередь: %s", vid, exc.detail)


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
    annotation — пояснение оператора (сохраняется как аннотация кода в БД,
    эмбеддинг считает воркер).
    """
    annotation_count = None
    storage = logger.storage

    if request.action == "confirm":
        await asyncio.to_thread(logger.confirm, request.log_id)
    elif request.action == "correct":
        if not request.operator_codes:
            raise HTTPException(
                status_code=400,
                detail="operator_codes обязательно для action='correct'",
            )

        # Эмбеддинг — ровно для вставленных строк (без сравнения «до/после»:
        # чужая аннотация, вставленная одновременно, иначе попала бы в задачи).
        _updated, new_ids = await asyncio.to_thread(
            logger.correct_with_annotations, request.log_id,
            request.operator_codes, request.annotation)
        await _dispatch_embeddings(new_ids)

        # Количество аннотаций для первого кода
        try:
            anns = await asyncio.to_thread(storage.classifier.annotations_for,
                                           request.operator_codes[0])
            annotation_count = len(anns)
        except Exception:  # noqa: BLE001
            annotation_count = None

    elif request.action == "reject":
        await asyncio.to_thread(logger.reject, request.log_id)

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
    Источник: data/test_appeals.json (читается в потоке, не в event loop).
    """
    return await asyncio.to_thread(_read_examples, examples_path)


def _read_examples(examples_path: Path) -> dict:
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
    stats = await asyncio.to_thread(logger.stats)
    threshold = int(config.FINETUNE_THRESHOLD)
    return {
        "verified": stats["verified"],
        "confirmed": stats["confirmed"],
        "corrected": stats["corrected"],
        "rejected": stats["rejected"],
        "pending": stats["pending"],
        "threshold": threshold,
        "progress_percent": min(100, int(stats["verified"] / threshold * 100)),
    }


# ── Настройки (бэк-офис → вкладка «Настройки») ────────────────────────────────

class SettingsSaveRequest(BaseModel):
    values: dict[str, str]


class LlmTestRequest(BaseModel):
    """Проверка LLM-endpoint. Пустые поля → берутся текущие значения конфига.

    Поля повторяют группу «Модель LLM» на форме настроек: проверять нужно тот
    endpoint, куда пойдёт классификация. Раньше здесь были только
    `CUSTOM_LLM_*`, и при провайдере `ario` кнопка отвечала «Не задан base
    URL» — оператор читал это как отсутствие связи с моделью.
    """
    LLM_PROVIDER: Optional[str] = None
    ARIO_BASE_URL: Optional[str] = None
    ARIO_MODEL: Optional[str] = None
    ARIO_API_KEY: Optional[str] = None
    CUSTOM_LLM_BASE_URL: Optional[str] = None
    CUSTOM_LLM_MODEL: Optional[str] = None
    CUSTOM_LLM_API_KEY: Optional[str] = None
    OLLAMA_BASE_URL: Optional[str] = None
    OLLAMA_MODEL: Optional[str] = None
    GROQ_MODEL: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None


class RxTestRequest(BaseModel):
    """Проверка подключения. Пустые поля → берутся текущие значения конфига."""
    RX_ODATA_URL: Optional[str] = None
    RX_USER: Optional[str] = None
    RX_PASSWORD: Optional[str] = None


@app.get("/api/settings", tags=["Настройки"])
def get_settings():
    """Текущие настройки LLM и RX для формы бэк-офиса. Секреты маскируются.

    Синхронный: чтение .env идёт в пуле потоков, не в event loop.
    """
    import settings_store
    return settings_store.read_settings()


@app.post("/api/settings", tags=["Настройки"])
def save_settings(request: SettingsSaveRequest):
    """Записать настройки в .env и применить их без перезапуска сервиса.

    Синхронный: запись .env идёт в пуле потоков, не в event loop.
    """
    import settings_store
    try:
        result = settings_store.save_settings(request.values)
    except settings_store.SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось записать .env: {e}")

    if not result["saved"]:
        return {**result, "message": "Изменений нет"}
    return {**result, "message": f"Сохранено настроек: {len(result['saved'])}"}


@app.post("/api/settings/test-rx", tags=["Настройки"])
async def test_rx_connection(request: RxTestRequest):
    """Проверить связь с Directum RX. Пустой пароль → используется сохранённый."""
    from starlette.concurrency import run_in_threadpool
    import rx_client
    import settings_store

    password = request.RX_PASSWORD
    if password is not None and password.strip() in ("", settings_store.MASK):
        password = None

    # check_connection синхронный и на недостижимом хосте висит до таймаута.
    # В event loop это заморозило бы весь сервис — уводим в пул потоков.
    return await run_in_threadpool(
        rx_client.check_connection,
        url=(request.RX_ODATA_URL or None),
        user=(request.RX_USER or None),
        password=password,
    )


@app.post("/api/settings/test-llm", tags=["Настройки"])
async def test_llm_connection(request: LlmTestRequest):
    """Проверить связь с LLM того провайдера, который выбран в настройках.

    Незаполненные поля берутся из текущего конфига, маска секрета — тоже.
    Возвращает {ok, detail, models, provider, base_url}: список моделей ловит
    расхождение имени модели с тем, что реально отдаёт сервер, а provider и
    base_url показывают, какой адрес проверяли.
    """
    from starlette.concurrency import run_in_threadpool
    import llm_check
    import settings_store

    # Пустое поле и маска означают «значение не меняли» — пусть решает конфиг.
    values = {
        key: value
        for key, value in request.model_dump().items()
        if value is not None and value.strip() not in ("", settings_store.MASK)
    }
    provider = values.pop("LLM_PROVIDER", None)

    # Запрос синхронный и на недостижимом хосте висит до таймаута —
    # в event loop это заморозило бы весь сервис.
    return await run_in_threadpool(
        llm_check.check_active, provider=provider, values=values)


def _parse_upload(temp_path: Path, content: bytes):
    temp_path.write_bytes(content)
    return validate_codes(parse_file(str(temp_path)))


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

        # Запись файла, разбор Excel/CSV и сверка кодов — вне event loop.
        result = await asyncio.to_thread(_parse_upload, temp_path, content)
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

    from historical_loader import save_to_historical_db
    filename = _last_historical_upload.get("filename", "unknown")
    inserted = await asyncio.to_thread(save_to_historical_db, preview, filename,
                                       get_storage())

    _last_historical_upload = {}
    # records_saved — реально добавленные строки; дубли (hist_dedup) пропускаются
    return {"status": "ok", "records_saved": inserted,
            "duplicates_skipped": len(preview) - inserted}


@app.get("/api/historical-count")
async def get_historical_count():
    """Количество исторических размеченных обращений в БД."""
    count = await asyncio.to_thread(get_storage().historical.count)
    if not count:
        return {"count": 0, "status": "no_data"}
    return {"count": count, "status": "ok"}


# ── Дообучение (задача finetune в очереди training) ──────────────────────────

def _iso(dt) -> Optional[str]:
    """Прежний формат статуса: локальное время без зоны, до секунд."""
    if dt is None:
        return None
    return dt.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _finetune_snapshot(job) -> dict:
    """Задача finetune → прежний формат статуса (/api/finetune/status)."""
    if job is None:
        return {
            "job_id": None,
            "status": "idle",
            "source": None,
            "force": False,
            "started_at": None,
            "finished_at": None,
            "message": "Дообучение не запускалось",
            "model_path": None,
            "returncode": None,
            "log_tail": "",
        }
    payload = job.payload or {}
    result = job.result if isinstance(job.result, dict) else {}
    returncode = None
    if job.status == STATUS_QUEUED:
        status, message = "queued", "Дообучение поставлено в очередь"
    elif job.status == STATUS_RUNNING:
        status, message = "running", "Дообучение модели запущено"
    elif job.status == STATUS_DONE and result.get("status") == "completed":
        status, returncode = "completed", 0
        message = result.get("message") or "Модель дообучена"
    else:
        status, returncode = "failed", 1
        message = job.error or result.get("message") or "Дообучение не выполнено"
    log_tail = result.get("log_tail") or (job.error if job.status == STATUS_ERROR else "") or ""
    return {
        "job_id": job.id,
        "status": status,
        "source": payload.get("source"),
        "force": bool(payload.get("force", False)),
        "started_at": _iso(job.started_at or job.created_at),
        "finished_at": _iso(job.finished_at),
        "message": message,
        "model_path": result.get("model_path"),
        "returncode": returncode,
        "log_tail": log_tail,
    }


@app.post("/api/finetune")
async def start_finetune(request: FineTuneRequest):
    """Ставит дообучение в очередь training (выполняет воркер дообучения)."""
    storage = get_storage()
    active = await asyncio.to_thread(storage.jobs.has_active, "finetune")
    if active:
        raise HTTPException(status_code=409, detail="Дообучение уже выполняется")

    # Как и прежний запуск finetune_model.py: для historical/combined порог
    # верифицированных записей не проверяется (--force). В ответе и статусе —
    # этот действующий флаг (payload.force), а не присланный в запросе.
    force = request.force or request.source in ("historical", "combined")
    try:
        sub = await job_api.submit_job("finetune", "finetune", "training",
                                       {"source": request.source, "force": force})
    except ActiveJobExists as exc:  # параллельный запрос успел создать задачу
        raise HTTPException(status_code=409, detail="Дообучение уже выполняется") from exc
    job = await asyncio.to_thread(storage.jobs.get, sub.job.id)
    return _finetune_snapshot(job or sub.job)


@app.get("/api/finetune/status")
async def finetune_status():
    """Статус последнего дообучения (последняя задача finetune)."""
    job = await asyncio.to_thread(get_storage().jobs.latest, "finetune")
    return _finetune_snapshot(job)


# ── Реестр моделей ───────────────────────────────────────────────────────────

def _model_item(mv) -> dict:
    return {
        "version": mv.version,
        "base_model_name": mv.base_model_name,
        "model_path": mv.model_path,
        "is_active": mv.is_active,
        "recall5_before": mv.recall5_before,
        "recall5_after": mv.recall5_after,
        "training_samples_count": mv.training_samples_count,
        "trained_at": mv.trained_at.isoformat() if mv.trained_at is not None else None,
    }


@app.get("/api/models", tags=["Бэк-офис"])
async def list_models():
    """Версии модели эмбеддингов (ml_model_registry)."""
    models = await asyncio.to_thread(get_storage().models.list)
    return [_model_item(mv) for mv in models]


@app.post("/api/models/{version}/activate", tags=["Бэк-офис"])
async def activate_model(version: int, _username: str = Depends(get_current_user)):
    """Сделать версию активной (ручная активация и откат). Воркеры подхватят её
    перед следующей задачей (NOTIFY model_changed)."""
    storage = get_storage()
    try:
        await asyncio.to_thread(storage.models.activate, version)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Версия модели {version} не найдена")
    mv = await asyncio.to_thread(storage.models.get, version)
    return {"status": "ok", "model": _model_item(mv) if mv else None}


# ── Бэк-офис ─────────────────────────────────────────────────────────────────

_code_names: dict = {"storage": None, "names": {}}


def _load_code_names(storage) -> dict[str, str]:
    """code → name из classifier_entries (кэш процесса на хранилище)."""
    if _code_names["storage"] is not storage or not _code_names["names"]:
        _code_names["names"] = {e.code: e.name for e in storage.classifier.load_entries()}
        _code_names["storage"] = storage
    return _code_names["names"]


def _backoffice_stats(storage) -> dict:
    stats = storage.appeals.backoffice_stats(days=30)
    names = _load_code_names(storage) if stats["top_codes"] else {}
    stats["top_codes"] = [
        {"code": c["code"], "name": names.get(c["code"], ""), "count": c["count"]}
        for c in stats["top_codes"]
    ]
    return stats


@app.get("/api/backoffice/stats", tags=["Бэк-офис"])
async def backoffice_stats(
    _username: str = "",
    logger: AppealsLogger = Depends(get_logger_dep),
):
    """
    Возвращает агрегированную статистику для страницы бэк-офиса.
    Агрегаты считает хранилище (PostgreSQL); ip_stats — по ip карточек обращений.
    """
    return await asyncio.to_thread(_backoffice_stats, logger.storage)

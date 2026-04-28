"""Главный модуль FastAPI приложения — шаблон.

TEMPLATE: Адаптируй:
- Модели запросов/ответов в src/models/
- Endpoints /api/cases/{case_id} и /api/chat — подключи реальные сервисы
- /api/data/test — укажи реальные тестовые файлы
- /api/data/upload — адаптируй форматы файлов
"""

import json
import re
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.config import get_data_dir, get_backoffice_credentials
from src.services.metrics_storage import (
    init_db, log_request, save_feedback, get_metrics,
    save_chat_feedback, update_chat_feedback_summary, get_chat_feedback,
    get_custom_prompts, create_custom_prompt, update_custom_prompt, delete_custom_prompt,
    get_system_prompt, set_system_prompt, reset_system_prompt,
    get_llm_models, get_llm_model_by_id,
    create_llm_model, update_llm_model, delete_llm_model,
)

# Инициализация базы данных при запуске
init_db()

# Basic Auth для бэкофиса
_security = HTTPBasic()


def _require_backoffice_auth(credentials: HTTPBasicCredentials = Depends(_security)):
    """Проверяет Basic Auth для защищённых endpoints бэкофиса."""
    user, password = get_backoffice_credentials()
    ok_user = secrets.compare_digest(credentials.username.encode(), user.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Неверные учётные данные",
            headers={"WWW-Authenticate": "Basic"},
        )


app = FastAPI(
    # TEMPLATE: Замени title и description
    title="AI Assistant Prototype",
    description="ИИ-помощник — прототип",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Раздача статических файлов
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _get_client_ip(request: Request) -> str:
    """Извлекает IP-адрес клиента (поддержка X-Forwarded-For для nginx/reverse proxy)."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# =============================================================================
# СТРАНИЦЫ
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Главная страница приложения."""
    index_path = static_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html не найден")
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/backoffice", response_class=HTMLResponse)
async def backoffice(request: Request, _: None = Depends(_require_backoffice_auth)):
    """Страница метрик бэк-офиса (защищена Basic Auth)."""
    backoffice_path = static_dir / "backoffice.html"
    if not backoffice_path.exists():
        raise HTTPException(status_code=404, detail="backoffice.html не найден")
    return HTMLResponse(content=backoffice_path.read_text(encoding="utf-8"))


# =============================================================================
# API
# =============================================================================

@app.get("/api/health")
async def health():
    """Healthcheck endpoint."""
    return {"status": "ok"}


# =============================================================================
# ОБЪЕКТЫ И ЭЛЕМЕНТЫ
# TEMPLATE: Замени на реальную загрузку данных из БД или Directum RX API.
# =============================================================================

@app.get("/api/objects")
async def get_objects(request: Request):
    """
    Возвращает список объектов для левой панели.

    TEMPLATE: Замени заглушку на реальный источник данных.
    Ожидаемый формат ответа:
        {"objects": [{"id": 1, "name": "Название"}, ...]}
    Опционально: {"objects": [...], "filters": [...]}
    """
    ip = _get_client_ip(request)
    log_request(ip, "/api/objects")

    # TEMPLATE: Замени на реальную загрузку, например:
    # from src.services.rx_client import get_rx_objects
    # objects = await get_rx_objects()
    # return {"objects": objects}

    return {
        "objects": [],
        # "error": "Объекты не настроены. Адаптируй /api/objects"
    }


@app.get("/api/objects/{object_id}")
async def get_object(object_id: int, request: Request):
    """
    Возвращает контекст объекта и список дочерних элементов.

    TEMPLATE: Замени на реальную загрузку.
    Ожидаемый формат ответа:
        {
            "context": "Текстовый контекст для LLM",
            "items": [{"id": 1, "name": "Элемент", "meta": "..."}, ...]
        }
    """
    ip = _get_client_ip(request)
    log_request(ip, f"/api/objects/{object_id}")

    # TEMPLATE: Замени на реальную загрузку объекта
    raise HTTPException(status_code=501, detail="Загрузка объекта не настроена. Адаптируй /api/objects/{object_id}")


@app.get("/api/items/{item_id}")
async def get_item(item_id: int, request: Request):
    """
    Возвращает детали и контекст дочернего элемента.

    TEMPLATE: Замени на реальную загрузку.
    Ожидаемый формат ответа:
        {"context": "Текстовый контекст для LLM"}
    """
    ip = _get_client_ip(request)
    log_request(ip, f"/api/items/{item_id}")

    # TEMPLATE: Замени на реальную загрузку элемента
    raise HTTPException(status_code=501, detail="Загрузка элемента не настроена. Адаптируй /api/items/{item_id}")


@app.get("/api/data/test")
async def load_test_data(request: Request):
    """
    Загружает тестовые данные из папки /data.
    TEMPLATE: Адаптируй под формат и файлы проекта.
    """
    ip = _get_client_ip(request)
    log_request(ip, "/api/data/test")

    # TEMPLATE: Замени на реальную загрузку тестовых данных
    # Пример:
    # data_dir = get_data_dir()
    # with open(os.path.join(data_dir, "test_data.json"), "r", encoding="utf-8") as f:
    #     raw = json.load(f)
    # return {"data": raw, "items_list": [...], "summary": "..."}

    raise HTTPException(status_code=501, detail="Тестовые данные не настроены. Адаптируй /api/data/test")


@app.post("/api/data/upload")
async def upload_data(
    request: Request,
    json_file: Optional[UploadFile] = File(default=None),
    docx_file: Optional[UploadFile] = File(default=None),
    json_text: Optional[str] = Form(default=None),
):
    """
    Загружает пользовательские файлы.
    TEMPLATE: Адаптируй под форматы данных проекта.
    """
    ip = _get_client_ip(request)
    log_request(ip, "/api/data/upload")

    # TEMPLATE: Замени на реальный парсинг
    raise HTTPException(status_code=501, detail="Загрузка данных не настроена. Адаптируй /api/data/upload")


@app.post("/api/cases/{case_id}")
async def run_case(case_id: int, request: Request):
    """
    Запускает один из кейсов с потоковым ответом (SSE).
    TEMPLATE: Подключи реальный сервис кейсов.

    IMPORTANT: Паттерн SSE:
    - StreamingResponse с media_type="text/event-stream"
    - Заголовок X-Accel-Buffering: no (обязателен для nginx)
    - Чанки отправлять ЦЕЛИКОМ, не разбивать по \\n
    - Финальное событие: data: [DONE]
    """
    ip = _get_client_ip(request)
    log_request(ip, f"/api/cases/{case_id}", case_id=case_id)

    # TEMPLATE: Извлеки model_id и разреши конфигурацию модели:
    # body_json = await request.json()
    # model_id = body_json.get("model_id") or None
    # llm_model_name, llm_api_key, llm_base_url = _resolve_model(model_id)
    # Затем передай llm_model_name, llm_api_key, llm_base_url в сервис:
    # generator = await cases_service.run_case(case_id, ..., model=llm_model_name,
    #                                           api_key=llm_api_key, base_url=llm_base_url)

    # TEMPLATE: Замени на реальную генерацию
    async def sse_stream():
        """Генератор SSE-событий."""
        try:
            # TEMPLATE: Замени на вызов реального сервиса:
            # generator = await cases_service.run_case(case_id=case_id, ...)
            # async for chunk in generator:
            #     yield f"data: {json.dumps(chunk)}\n\n"
            yield f"data: {json.dumps('Кейс ' + str(case_id) + ' — ответ не настроен. Адаптируй /api/cases/')}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps('[ERROR] ' + str(e))}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps('[ERROR] ' + str(e))}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # IMPORTANT: без этого nginx буферизует SSE
        }
    )


@app.post("/api/chat")
async def chat(request: Request):
    """
    Свободный чат с ИИ-помощником (SSE).
    TEMPLATE: Подключи реальный сервис чата.
    """
    ip = _get_client_ip(request)
    log_request(ip, "/api/chat")

    # TEMPLATE: Аналогично /api/cases — извлеки model_id и разреши конфигурацию:
    # body_json = await request.json()
    # model_id = body_json.get("model_id") or None
    # llm_model_name, llm_api_key, llm_base_url = _resolve_model(model_id)

    # TEMPLATE: Замени на реальную генерацию
    async def sse_stream():
        try:
            yield f"data: {json.dumps('Чат не настроен. Адаптируй /api/chat')}\n\n"
        except Exception as e:
            yield f"data: {json.dumps('[ERROR] ' + str(e))}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/api/feedback")
async def feedback(request: Request):
    """Сохраняет оценку пользователя по кейсу (thumbs up/down)."""
    ip = _get_client_ip(request)
    body = await request.json()

    save_feedback(
        ip=ip,
        case_id=body.get("case_id", 0),
        session_id=body.get("session_id", "unknown"),
        vote=body.get("vote", 0),
        model_label=body.get("model_label") or None,
    )
    return {"success": True}


@app.post("/api/feedback/chat")
async def feedback_chat(request: Request):
    """
    Сохраняет оценку чат-ответа (thumbs up/down).
    Возвращает id записи для последующего summarize.
    """
    body = await request.json()
    record_id = save_chat_feedback(
        session_id=body.get("session_id", "unknown"),
        vote=body.get("vote", 0),
        user_message=body.get("user_message", ""),
        context_type=body.get("context_type", "object"),
        context_name=body.get("context_name", ""),
        model_label=body.get("model_label") or None,
    )
    return {"success": True, "id": record_id}


@app.post("/api/feedback/chat/summarize")
async def feedback_chat_summarize(request: Request):
    """
    Генерирует краткое саммари чат-диалога и сохраняет его к записи фидбека.
    Вызывается асинхронно из фронтенда после сохранения оценки.

    TEMPLATE: Подключи LLM для генерации саммари.
    """
    body = await request.json()
    record_id = body.get("id")
    user_message = body.get("user_message", "")

    if not record_id:
        raise HTTPException(status_code=400, detail="id обязателен")

    # TEMPLATE: Замени на реальную генерацию саммари через LLM, например:
    # summary = await llm_service.summarize(user_message, history)
    summary = f"Вопрос: {user_message[:100]}"

    update_chat_feedback_summary(record_id, summary)
    return {"success": True}


@app.get("/api/metrics")
async def metrics(_: None = Depends(_require_backoffice_auth)):
    """Возвращает агрегированные метрики для бэк-офиса (защищено Basic Auth)."""
    return get_metrics()


@app.get("/api/metrics/chat-feedback")
async def metrics_chat_feedback(
    limit: int = 50,
    _: None = Depends(_require_backoffice_auth),
):
    """Возвращает последние записи чат-фидбека для бэкофиса."""
    return {"items": get_chat_feedback(limit=limit)}


# =============================================================================
# API — УПРАВЛЕНИЕ ПРОМПТАМИ
# TEMPLATE: Используй как есть. Адаптируй get_system_prompt_endpoint:
#   замени DEFAULT_SYSTEM_PROMPT на реальный дефолтный промпт кейса.
# =============================================================================

# TEMPLATE: Замени на реальный дефолтный системный промпт для кейса 1
DEFAULT_SYSTEM_PROMPT = (
    "Ты — ИИ-помощник. Проведи анализ предоставленных данных и дай развёрнутый ответ на русском языке.\n"
    "Используй Markdown-форматирование."
)


@app.get("/api/prompts")
async def list_prompts() -> dict:
    """Возвращает список пользовательских промптов."""
    return {"prompts": get_custom_prompts()}


@app.post("/api/prompts")
async def add_prompt(body: dict) -> dict:
    """Создаёт пользовательский промпт."""
    name = (body.get("name") or "").strip()
    prompt_text = (body.get("prompt_text") or "").strip()
    if not name or not prompt_text:
        raise HTTPException(status_code=422, detail="Название и текст промпта обязательны")
    return create_custom_prompt(name, prompt_text)


@app.put("/api/prompts/{prompt_id}")
async def edit_prompt(prompt_id: int, body: dict) -> dict:
    """Обновляет пользовательский промпт."""
    name = (body.get("name") or "").strip()
    prompt_text = (body.get("prompt_text") or "").strip()
    if not name or not prompt_text:
        raise HTTPException(status_code=422, detail="Название и текст промпта обязательны")
    if not update_custom_prompt(prompt_id, name, prompt_text):
        raise HTTPException(status_code=404, detail="Промпт не найден")
    return {"success": True}


@app.delete("/api/prompts/{prompt_id}")
async def remove_prompt(prompt_id: int) -> dict:
    """Удаляет пользовательский промпт."""
    if not delete_custom_prompt(prompt_id):
        raise HTTPException(status_code=404, detail="Промпт не найден")
    return {"success": True}


@app.get("/api/prompts/system/{prompt_id}")
async def get_system_prompt_endpoint(prompt_id: int) -> dict:
    """Возвращает системный промпт (текущий + дефолтный)."""
    override = get_system_prompt(prompt_id)
    return {
        "text": override if override is not None else DEFAULT_SYSTEM_PROMPT,
        "default_text": DEFAULT_SYSTEM_PROMPT,
        "is_custom": override is not None,
    }


@app.put("/api/prompts/system/{prompt_id}")
async def update_system_prompt_endpoint(prompt_id: int, body: dict) -> dict:
    """Сохраняет переопределение системного промпта."""
    prompt_text = (body.get("prompt_text") or "").strip()
    if not prompt_text:
        raise HTTPException(status_code=422, detail="Текст промпта не может быть пустым")
    set_system_prompt(prompt_id, prompt_text)
    return {"success": True}


@app.delete("/api/prompts/system/{prompt_id}")
async def reset_system_prompt_endpoint(prompt_id: int) -> dict:
    """Сбрасывает системный промпт к дефолтному."""
    reset_system_prompt(prompt_id)
    return {"success": True}


# =============================================================================
# API — СКАЧИВАНИЕ ОТЧЁТА (.docx)
# TEMPLATE: Адаптируй параметры тела запроса и название файла.
# IMPORTANT: Используй filename*=UTF-8''... (RFC 5987) для кириллических имён.
# =============================================================================

@app.post("/api/report/download")
async def download_report(body: dict) -> Response:
    """Генерирует и возвращает отчёт в формате .docx."""
    from src.services.docx_generator import generate_report_docx
    text = body.get("text", "")
    # TEMPLATE: Адаптируй поля под параметры проекта
    title = body.get("title", "Отчёт")
    if not text:
        raise HTTPException(status_code=422, detail="Текст отчёта обязателен")
    try:
        docx_bytes = generate_report_docx(
            response_text=text,
            title=title,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка генерации docx: {e}")

    safe_name = re.sub(r'[^\w\-.]', '_', title[:40])
    filename = f"report_{safe_name}.docx"
    filename_encoded = quote(filename)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"},
    )


# =============================================================================
# API — УПРАВЛЕНИЕ LLM-МОДЕЛЯМИ
# TEMPLATE: Используй как есть. Позволяет добавлять модели с кастомным
# base_url и токеном через бэкофис. model_id передаётся из фронтенда
# в /api/cases/{id} и /api/chat в теле запроса.
# =============================================================================

def _resolve_model(model_id: int | None) -> tuple[str | None, str | None, str | None]:
    """
    Возвращает (model_name, api_key, base_url) для выбранной модели.
    Если model_id не указан или равен 0 — возвращает (None, None, None),
    т.е. используются настройки из .env.

    Raises:
        HTTPException 404: Если model_id указан, но модель не найдена в БД.
    """
    if not model_id:
        return None, None, None
    record = get_llm_model_by_id(model_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail="Выбранная модель недоступна. Обновите страницу и выберите другую модель."
        )
    return record.get("name") or None, record.get("token") or None, record.get("base_url") or None


@app.get("/api/models")
async def list_models_public():
    """
    Возвращает список доступных моделей для селектора в UI.
    Включает модель из .env (id=0) и пользовательские из БД.
    """
    from src.config import get_openai_model
    default_label = get_openai_model() or "default"
    models = [{"id": 0, "label": f"{default_label} (из базовой поставки RX)", "is_default": True}]
    for m in get_llm_models():
        label = m["display_name"] or m["name"]
        models.append({"id": m["id"], "label": label, "is_default": False})
    return {"models": models}


@app.get("/api/admin/models")
async def admin_list_models(_: None = Depends(_require_backoffice_auth)):
    """Возвращает список пользовательских моделей для бэкофиса."""
    return {"models": get_llm_models()}


@app.post("/api/admin/models")
async def admin_create_model(body: dict, _: None = Depends(_require_backoffice_auth)):
    """Создаёт пользовательскую LLM-модель."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Поле name обязательно")
    model_id = create_llm_model(
        name=name,
        base_url=(body.get("base_url") or "").strip() or None,
        token=(body.get("token") or "").strip() or None,
        display_name=(body.get("display_name") or "").strip() or None,
    )
    return {"id": model_id}


@app.put("/api/admin/models/{model_id}")
async def admin_update_model(model_id: int, body: dict, _: None = Depends(_require_backoffice_auth)):
    """
    Обновляет пользовательскую LLM-модель.
    Если "token" отсутствует в теле — поле токена не изменяется.
    """
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Поле name обязательно")
    token = body.get("token")  # None = не трогать, str = обновить
    if token is not None:
        token = token.strip() or None
    ok = update_llm_model(
        model_id=model_id,
        name=name,
        base_url=(body.get("base_url") or "").strip() or None,
        token=token,
        display_name=(body.get("display_name") or "").strip() or None,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Модель не найдена")
    return {"ok": True}


@app.delete("/api/admin/models/{model_id}")
async def admin_delete_model(model_id: int, _: None = Depends(_require_backoffice_auth)):
    """Удаляет пользовательскую LLM-модель."""
    if not delete_llm_model(model_id):
        raise HTTPException(status_code=404, detail="Модель не найдена")
    return {"ok": True}

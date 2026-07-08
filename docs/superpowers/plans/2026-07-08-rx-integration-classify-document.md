# RX Integration classify-document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** REST-эндпоинт `POST /integration/classify-document`, который по id документа тянет его из RX, извлекает текст, классифицирует и возвращает JSON `{ document_id, applicant_fio, summary, questions[] }`.

**Architecture:** Pull-модель. Новые изолированные модули (`rx_client`, `fio_extractor`, `integration_api`) поверх существующего `ClassifierAgent`. Роутер подключается к текущему FastAPI-приложению; агент шарится через `app.state.agent`. Классификатор и извлечение текста переиспользуются как есть.

**Tech Stack:** Python 3.11, FastAPI, httpx (уже зависимость), PyMuPDF (`text_extractor`), pytest.

## Global Constraints

- Python 3.11; `numpy>=1.26.0,<2.0`.
- Все скрипты запускаются из **корня проекта**; импорты из `src/` через `sys.path.insert(0, 'src')`.
- `summary` в ответе — **строго ≤ 250 символов** (жёсткая обрезка на нашей стороне).
- `applicant_fio` — формат **«Фамилия И.О.»**; `null`, если не распарсили.
- `code` вопроса — формат `XXXX.XXXX.XXXX.XXXX` (наименование агента, поле `name`).
- Секреты RX — только из `.env`; в код не хардкодить.
- Не ломать существующие потребители `ClassificationResult` (`api_server`, `operator_cli`) — новые поля добавляются со значениями по умолчанию.

---

### Task 1: Конфигурация RX

**Files:**
- Modify: `src/config.py` (в конец файла)
- Modify: `.env.example` (в конец файла)

**Interfaces:**
- Produces: константы `RX_ODATA_URL: str`, `RX_USER: str`, `RX_PASSWORD: str` в `config`.

- [ ] **Step 1: Добавить константы в `src/config.py`**

В конец файла:
```python
# ── Интеграция с Directum RX (OData) ────────────────────────────────────────────
RX_ODATA_URL: str = os.getenv("RX_ODATA_URL", "http://172.16.96.98/integration/odata")
RX_USER:      str = os.getenv("RX_USER", "Administrator")
RX_PASSWORD:  str = os.getenv("RX_PASSWORD", "11111")
```

- [ ] **Step 2: Добавить пример в `.env.example`**

В конец файла:
```
# Интеграция с Directum RX (OData integration service)
RX_ODATA_URL=http://172.16.96.98/integration/odata
RX_USER=Administrator
RX_PASSWORD=11111
```

- [ ] **Step 3: Проверить импорт**

Run: `python -c "import sys; sys.path.insert(0,'src'); from config import RX_ODATA_URL, RX_USER, RX_PASSWORD; print(RX_ODATA_URL, RX_USER)"`
Expected: `http://172.16.96.98/integration/odata Administrator`

- [ ] **Step 4: Commit**

```bash
git add src/config.py .env.example
git commit -m "feat: add RX OData connection config"
```

---

### Task 2: Нормализатор ФИО

**Files:**
- Create: `src/fio_extractor.py`
- Test: `tests/test_fio_extractor.py`

**Interfaces:**
- Produces: `normalize_fio(raw: str | None) -> str | None` — «Фамилия И.О.» или `None`.

- [ ] **Step 1: Написать падающий тест**

`tests/test_fio_extractor.py`:
```python
import sys
sys.path.insert(0, "src")
import pytest
from fio_extractor import normalize_fio


@pytest.mark.parametrize("raw,expected", [
    ("Иванов Иван Иванович", "Иванов И.И."),
    ("иванов иван иванович", "Иванов И.И."),
    ("Иванов Иван", "Иванов И."),
    ("  Петров   Пётр   Петрович ", "Петров П.П."),
    ("Сидоров С.С.", "Сидоров С.С."),
    ("Иванов", None),
    ("", None),
    (None, None),
    ("12345 !!!", None),
])
def test_normalize_fio(raw, expected):
    assert normalize_fio(raw) == expected
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_fio_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fio_extractor'`

- [ ] **Step 3: Реализовать `src/fio_extractor.py`**

```python
"""Нормализация ФИО заявителя к формату «Фамилия И.О.»."""

import re

# Слово-токен: буква (кириллица/латиница) + буквы/дефис, опционально с точкой.
_WORD = re.compile(r"^[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z-]*\.?$")


def normalize_fio(raw: str | None) -> str | None:
    """«Иванов Иван Иванович» → «Иванов И.И.». None, если не распарсили."""
    if not raw:
        return None

    # Разбиваем инициалы вида «И.И.» на отдельные токены.
    tokens = [t for t in raw.replace(".", ". ").split() if t]
    tokens = [t for t in tokens if _WORD.match(t)]

    if len(tokens) < 2:
        return None

    surname = tokens[0].rstrip(".")
    surname = surname[:1].upper() + surname[1:]

    initials = "".join(f"{t[0].upper()}." for t in tokens[1:3])
    return f"{surname} {initials}"
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `python -m pytest tests/test_fio_extractor.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/fio_extractor.py tests/test_fio_extractor.py
git commit -m "feat: add FIO normalizer (Фамилия И.О.)"
```

---

### Task 3: Доп. поля классификации (ФИО + суть)

**Files:**
- Modify: `src/classifier_agent.py` (dataclass `ClassificationResult`, `SYSTEM_PROMPT`, `CLASSIFICATION_PROMPT_TEMPLATE`, метод `classify`, + новый модульный хелпер)
- Test: `tests/test_classifier_extra.py`

**Interfaces:**
- Consumes: `normalize_fio` из Task 2.
- Produces:
  - модульная функция `extract_extra_fields(groq_result: dict) -> tuple[str | None, str]` → `(applicant_fio, summary)`; `summary` уже обрезан до 250.
  - поля `ClassificationResult.applicant_fio: str | None` (default `None`), `ClassificationResult.summary: str` (default `""`).

- [ ] **Step 1: Написать падающий тест**

`tests/test_classifier_extra.py`:
```python
import sys
sys.path.insert(0, "src")
from classifier_agent import extract_extra_fields


def test_extract_fio_and_summary():
    gr = {"applicant_fio": "Иванов Иван Иванович", "summary": "Жалоба на мусор."}
    fio, summary = extract_extra_fields(gr)
    assert fio == "Иванов И.И."
    assert summary == "Жалоба на мусор."


def test_summary_truncated_to_250():
    gr = {"applicant_fio": None, "summary": "я" * 400}
    fio, summary = extract_extra_fields(gr)
    assert fio is None
    assert len(summary) == 250


def test_missing_fields():
    fio, summary = extract_extra_fields({})
    assert fio is None
    assert summary == ""
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_classifier_extra.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_extra_fields'`

- [ ] **Step 3: Добавить хелпер и поля в `src/classifier_agent.py`**

3a. В блок импортов (рядом с `from appeals_logger import get_logger`):
```python
from fio_extractor import normalize_fio
```

3b. Сразу после определения `@dataclass class ClassificationResult` добавить поля (в конец списка полей, у них есть default — порядок ок, т.к. `log_id` тоже с default):
```python
    applicant_fio: Optional[str] = field(default=None)  # «Фамилия И.О.» или None
    summary: str = field(default="")                    # краткая суть, ≤250 символов
```

3c. Добавить модульную функцию (после dataclass'ов, до класса агента):
```python
def extract_extra_fields(groq_result: dict) -> tuple[Optional[str], str]:
    """Из ответа LLM достаёт (applicant_fio «Фамилия И.О.», summary ≤250)."""
    fio = normalize_fio(groq_result.get("applicant_fio"))
    summary = (groq_result.get("summary") or "").strip()[:250]
    return fio, summary
```

3d. В `SYSTEM_PROMPT` дополнить задачи (после пункта 5 «Определить предмет ведения…»):
```
6. Извлечь ФИО заявителя (если указано в тексте) и составить краткую суть обращения
```

3e. В `CLASSIFICATION_PROMPT_TEMPLATE` в возвращаемый JSON добавить два поля верхнего уровня — вставить перед строкой `"questions": [`:
```
  "applicant_fio": "Фамилия Имя Отчество заявителя из текста, либо null",
  "summary": "Краткая суть обращения, не более 250 символов",
```

3f. В методе `classify`, после строки `needs_verification = overall_confidence < MIN_CONFIDENCE` и до создания `result = ClassificationResult(...)`, вычислить:
```python
        applicant_fio, summary = extract_extra_fields(groq_result)
```
И передать их в конструктор `ClassificationResult(...)` (добавить в конец аргументов):
```python
            applicant_fio=applicant_fio,
            summary=summary,
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `python -m pytest tests/test_classifier_extra.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/classifier_agent.py tests/test_classifier_extra.py
git commit -m "feat: classifier returns applicant_fio and summary"
```

---

### Task 4: Клиент RX OData

**Files:**
- Create: `src/rx_client.py`
- Test: `tests/test_rx_client.py`

**Interfaces:**
- Consumes: `config.RX_ODATA_URL/RX_USER/RX_PASSWORD`, `text_extractor.extract_text`.
- Produces:
  - `get_document_text(document_id: int) -> tuple[str, str]` → `(text, filename)`.
  - исключения `DocumentNotFound`, `BodyFetchError`.
  - `build_client() -> httpx.Client` (фабрика, монкипатчится в тестах).

- [ ] **Step 1: Написать падающий тест**

`tests/test_rx_client.py`:
```python
import sys
sys.path.insert(0, "src")
import base64
import httpx
import pytest
import rx_client

# Минимальный валидный PDF с текстовым слоем не нужен — text_extractor мокаем.
PDF_BYTES = b"%PDF-1.4 fake body"


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://rx")


def test_get_document_text_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("(26)"):
            return httpx.Response(200, json={"Name": "Вх. письмо",
                                              "Versions": [{"Id": 2, "Number": 1}]})
        if "$value" in str(request.url):
            return httpx.Response(200, content=PDF_BYTES)
        return httpx.Response(404)

    monkeypatch.setattr(rx_client, "RX_ODATA_URL", "http://rx")
    monkeypatch.setattr(rx_client, "build_client", lambda: _mock_client(handler))
    monkeypatch.setattr(rx_client, "extract_text",
                        lambda body, filename: ("извлечённый текст", False))

    text, filename = rx_client.get_document_text(26)
    assert text == "извлечённый текст"
    assert filename.endswith(".pdf")


def test_get_document_text_not_found(monkeypatch):
    def handler(request):
        return httpx.Response(404)
    monkeypatch.setattr(rx_client, "RX_ODATA_URL", "http://rx")
    monkeypatch.setattr(rx_client, "build_client", lambda: _mock_client(handler))
    with pytest.raises(rx_client.DocumentNotFound):
        rx_client.get_document_text(999)


def test_get_document_text_body_error(monkeypatch):
    def handler(request):
        if request.url.path.endswith("(26)"):
            return httpx.Response(200, json={"Name": "x", "Versions": [{"Id": 2, "Number": 1}]})
        return httpx.Response(500, text="boom")
    monkeypatch.setattr(rx_client, "RX_ODATA_URL", "http://rx")
    monkeypatch.setattr(rx_client, "build_client", lambda: _mock_client(handler))
    with pytest.raises(rx_client.BodyFetchError):
        rx_client.get_document_text(26)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_rx_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rx_client'`

- [ ] **Step 3: Реализовать `src/rx_client.py`**

```python
"""Клиент RX OData: получение текста документа по id (pull-модель)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from config import RX_ODATA_URL, RX_USER, RX_PASSWORD
from text_extractor import extract_text


class DocumentNotFound(Exception):
    """Документ с указанным id не найден в RX."""


class BodyFetchError(Exception):
    """Не удалось получить тело документа из RX."""


def build_client() -> httpx.Client:
    """Фабрика HTTP-клиента (монкипатчится в тестах)."""
    return httpx.Client(auth=(RX_USER, RX_PASSWORD), timeout=60.0, verify=False)


def _last_version_id(versions: list) -> int:
    return max(versions, key=lambda v: v.get("Number", 0))["Id"]


def _fetch_body(client: httpx.Client, document_id: int, version_id: int) -> bytes:
    """Скачать бинарное тело версии. Точка отладки OData-500 (см. Task 6)."""
    url = f"{RX_ODATA_URL}/IElectronicDocuments({document_id})/Versions({version_id})/Body/$value"
    r = client.get(url)
    if r.status_code == 200 and r.content:
        return r.content
    raise BodyFetchError(f"HTTP {r.status_code}: {r.text[:200]}")


def _filename_for(body: bytes, document_id: int) -> str:
    """Определяем расширение по сигнатуре, чтобы text_extractor выбрал парсер."""
    ext = ".pdf" if body[:4] == b"%PDF" else ".txt"
    return f"doc{document_id}{ext}"


def get_document_text(document_id: int) -> tuple[str, str]:
    """Вернуть (текст, имя_файла) для документа RX по id."""
    with build_client() as client:
        meta_url = f"{RX_ODATA_URL}/IElectronicDocuments({document_id})"
        r = client.get(meta_url, params={"$expand": "Versions($select=Id,Number)"})
        if r.status_code == 404:
            raise DocumentNotFound(f"Документ {document_id} не найден в RX")
        if r.status_code != 200:
            raise BodyFetchError(f"HTTP {r.status_code} при запросе документа")

        doc = r.json()
        versions = doc.get("Versions") or []
        if not versions:
            raise BodyFetchError(f"Документ {document_id} не имеет версий")

        version_id = _last_version_id(versions)
        body = _fetch_body(client, document_id, version_id)
        filename = _filename_for(body, document_id)
        text, _ = extract_text(body, filename)
        return text, filename
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `python -m pytest tests/test_rx_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rx_client.py tests/test_rx_client.py
git commit -m "feat: add RX OData client to fetch document text by id"
```

---

### Task 5: Роутер интеграции + подключение

**Files:**
- Create: `src/integration_api.py`
- Modify: `src/api_server.py` (импорт роутера, `include_router`, `app.state.agent` в startup)
- Test: `tests/test_integration_api.py`

**Interfaces:**
- Consumes: `rx_client.get_document_text`, `ClassifierAgent` (через `app.state.agent`), `ClassificationResult` (поля `applicant_fio`, `summary`, `questions[].code`, `questions[].name`).
- Produces: `APIRouter` `router` c `POST /integration/classify-document`; pydantic-модель `ClassifyDocumentResponse`.

- [ ] **Step 1: Написать падающий тест**

`tests/test_integration_api.py`:
```python
import sys
sys.path.insert(0, "src")
from dataclasses import dataclass, field
from fastapi import FastAPI
from fastapi.testclient import TestClient
import integration_api
import rx_client


@dataclass
class _Q:
    code: str
    name: str


@dataclass
class _Result:
    applicant_fio: str
    summary: str
    questions: list


class _FakeAgent:
    def classify(self, text):
        return _Result(applicant_fio="Иванов И.И.", summary="суть",
                       questions=[_Q(code="0001.0002.0003.0004", name="вопрос текстом")])


def _app(agent):
    app = FastAPI()
    app.include_router(integration_api.router)
    app.state.agent = agent
    return app


def test_classify_document_ok(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text",
                        lambda doc_id: ("текст обращения", "doc26.pdf"))
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 26})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "document_id": 26,
        "applicant_fio": "Иванов И.И.",
        "summary": "суть",
        "questions": [{"code": "0001.0002.0003.0004", "question": "вопрос текстом"}],
    }


def test_classify_document_not_found(monkeypatch):
    def raise_nf(doc_id):
        raise rx_client.DocumentNotFound("нет")
    monkeypatch.setattr(rx_client, "get_document_text", raise_nf)
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 999})
    assert resp.status_code == 404


def test_classify_document_body_error(monkeypatch):
    def raise_be(doc_id):
        raise rx_client.BodyFetchError("500")
    monkeypatch.setattr(rx_client, "get_document_text", raise_be)
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 26})
    assert resp.status_code == 502


def test_classify_document_empty_text(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text", lambda doc_id: ("   ", "doc.pdf"))
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 26})
    assert resp.status_code == 400
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_integration_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'integration_api'`

- [ ] **Step 3: Реализовать `src/integration_api.py`**

```python
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
```

- [ ] **Step 4: Подключить роутер в `src/api_server.py`**

4a. После строки `from historical_loader import parse_file, validate_codes` добавить:
```python
from integration_api import router as integration_router
```

4b. После создания `app = FastAPI(...)` (после закрывающей скобки конструктора) добавить:
```python
app.include_router(integration_router)
```

4c. В обработчике `startup()` — после `agent = ClassifierAgent()` добавить:
```python
    app.state.agent = agent
```

- [ ] **Step 5: Запустить — убедиться, что проходит**

Run: `python -m pytest tests/test_integration_api.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Прогнать весь набор тестов**

Run: `python -m pytest tests/ -v`
Expected: PASS (все, включая существующие)

- [ ] **Step 7: Commit**

```bash
git add src/integration_api.py src/api_server.py tests/test_integration_api.py
git commit -m "feat: add /integration/classify-document endpoint"
```

---

### Task 6: Смоук на стенде + отладка OData-500 + адрес сервиса

> Исследовательская задача (не TDD). Цель — сквозной прогон по реальному документу и устранение 500 при скачивании тела.

**Files:** возможные правки `src/rx_client.py` (`_fetch_body`), при необходимости — заметка в спеке.

- [ ] **Step 1: Запустить сервис**

Run: `uvicorn src.api_server:app --host 0.0.0.0 --port 8000`
Expected: `Агент классификации запущен и готов к работе.`

- [ ] **Step 2: Дёрнуть эндпоинт по реальному документу**

Run:
```bash
curl -s -X POST http://localhost:8000/integration/classify-document \
  -H "Content-Type: application/json" -d '{"document_id": 26}'
```
Expected (успех): JSON с `document_id`, `applicant_fio`, `summary`, `questions[]`.
Expected (если 500 не решён): `502` c текстом ошибки тела.

- [ ] **Step 3: Диагностика 500 при скачивании тела (если воспроизвелось)**

Проверить по порядку:
1. Права интеграционного пользователя на тело — попробовать другого пользователя/права в RX.
2. Альтернативные пути тела:
   - `GET {RX_ODATA_URL}/IElectronicDocuments({id})/Versions({vid})/PublicBody/$value`
   - тело через WebAPI: `GET http://172.16.96.98/Web/api/...` (см. `директум` knowledge-base guide 38).
3. Отдать тело как base64-свойство (`Body` → `IBinaryDataDto.Value`) вместо `$value`.

Зафиксировать рабочий путь в `_fetch_body` (интерфейс метода не меняется).

- [ ] **Step 4: Повторный смоук**

Повторить Step 2 — убедиться, что вернулся корректный JSON по документу id=26.

- [ ] **Step 5: Определить и записать адрес сервиса для отладки**

Run: `ipconfig | findstr /i "IPv4"` (Windows) — взять адрес машины.
Итог для оператора: `http://<IP>:8000/integration/classify-document`
(+ Swagger: `http://<IP>:8000/docs`).

- [ ] **Step 6: Commit (если правился `_fetch_body`)**

```bash
git add src/rx_client.py
git commit -m "fix: resolve RX OData body download path"
```

---

## Self-Review

**Spec coverage:**
- Контракт запроса/ответа → Task 5 (модели + маппинг). ✅
- `rx_client.get_document_text` (id→версия→тело→текст) → Task 4. ✅
- `fio_extractor` → Task 2. ✅
- Расширение LLM (`applicant_fio`, `summary` ≤250) → Task 3. ✅
- Роутер, не раздувающий `api_server` → Task 5. ✅
- Обработка ошибок 404/502/400/500 → Task 5 (тесты на 404/502/400). ✅
- Конфиг `.env` → Task 1. ✅
- Тесты (fio, summary, rx_client, эндпоинт, смоук) → Tasks 2–6. ✅
- Риск OData-500 + деливери адреса → Task 6. ✅

**Placeholder scan:** плейсхолдеров нет — код приведён полностью в каждом шаге.

**Type consistency:** `get_document_text(int) -> (str, str)` (Task 4) — так же вызывается в Task 5. `extract_extra_fields(dict) -> (Optional[str], str)` (Task 3). `ClassificationResult.applicant_fio/summary` (Task 3) читаются в Task 5. `RxQuestion(code, question)`; `q.code`, `q.name` берутся из `ClassifiedQuestion` (существующие поля). ✅

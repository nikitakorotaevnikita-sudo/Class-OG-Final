# MVP-демо «Веб-UI с операторским workflow» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Двухдневное живое демо: оператор открывает браузер, выбирает пример (или вставляет текст), видит карточку классификации и проходит цикл [Подтвердить / Исправить / Отклонить] поверх существующего `appeals_logger`.

**Architecture:** Один HTML + vanilla JS поверх существующего FastAPI. Классификатор не трогаем. К `api_server.py` добавляем 3 эндпоинта (`/verify`, `/examples`, `GET /`) и пробрасываем `log_id` в ответ `/classify`. Frontend — `src/static/index.html` + `src/static/app.js` (Tailwind через CDN).

**Tech Stack:** FastAPI, Pydantic, vanilla JS + Tailwind CDN, pytest (прямой async-вызов функций для бэкенда), ручная проверка для UI.

**Спека:** [docs/2026-04-21_SPEC_MVP-демо-веб-ui.md](2026-04-21_SPEC_MVP-демо-веб-ui.md)

---

## Decomposition: файлы и зоны ответственности

| Файл | Тип | Зона ответственности |
|---|---|---|
| `src/api_server.py` | modify | Расширить `ClassifyResponse` (`log_id`), добавить `/verify`, `/examples`, `GET /`, монтаж `StaticFiles`. Новые DI-функции `get_logger_dep`, `get_examples_path` — для тестируемости. |
| `data/test_appeals.json` | create | 10 заранее подготовленных обращений (id, title, text). |
| `src/static/index.html` | create | Разметка страницы + Tailwind CDN + подключение `app.js`. |
| `src/static/app.js` | create | Загрузка примеров, отправка `/classify`, рендер карточки, обработчики верификации, цветовая шкала, JSON-просмотр. |
| `tests/test_api_endpoints.py` | create | Тесты `/verify` и `/examples` через прямой async-вызов функций. |

Тесты для frontend не пишем — для 2-дневного MVP-демо ручной прогон 10 примеров достаточен (см. Task 12).

---

## Task 1: Тесты для `/verify` и `/examples` (TDD-первая задача)

**Files:**
- Create: `tests/test_api_endpoints.py`

- [ ] **Step 1.1: Создать файл с failing-тестами**

Файл `tests/test_api_endpoints.py`:

```python
"""Тесты новых эндпоинтов: /verify и /examples.

Используем прямой async-вызов функций эндпоинтов — это избегает
запуска startup-event'а (который грузит ML-модель и требует GROQ_API_KEY).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

# Делаем src импортируемым
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api_server import verify, get_examples, VerifyRequest
from appeals_logger import AppealsLogger


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_logger(tmp_path):
    """Логгер с временным JSONL-файлом — не пишет в продовый лог."""
    log_path = tmp_path / "test_log.jsonl"
    return AppealsLogger(log_path=log_path)


@pytest.fixture
def examples_file(tmp_path):
    """Временный test_appeals.json с двумя примерами."""
    path = tmp_path / "test_appeals.json"
    data = [
        {"id": "ex01", "title": "Пример 1", "text": "Текст обращения 1"},
        {"id": "ex02", "title": "Пример 2", "text": "Текст обращения 2"},
    ]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ── Тесты /verify ─────────────────────────────────────────────────────────────

def test_verify_confirm_marks_entry_confirmed(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="тестовое обращение",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.9,
    )

    result = asyncio.run(verify(
        VerifyRequest(log_id=log_id, action="confirm"),
        logger=isolated_logger,
    ))

    assert result == {"status": "ok", "log_id": log_id, "action": "confirm"}
    entries = isolated_logger.read_all()
    assert entries[0]["verification"]["status"] == "confirmed"


def test_verify_correct_writes_operator_codes(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="тестовое обращение",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.5,
    )

    result = asyncio.run(verify(
        VerifyRequest(
            log_id=log_id,
            action="correct",
            operator_codes=["0005.0005.0051.1103"],
        ),
        logger=isolated_logger,
    ))

    assert result["action"] == "correct"
    entry = isolated_logger.read_all()[0]
    assert entry["verification"]["status"] == "corrected"
    assert entry["verification"]["operator_codes"] == ["0005.0005.0051.1103"]


def test_verify_correct_without_codes_returns_400(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="x",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.5,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify(
            VerifyRequest(log_id=log_id, action="correct"),
            logger=isolated_logger,
        ))

    assert exc_info.value.status_code == 400
    assert "operator_codes" in exc_info.value.detail


def test_verify_reject_marks_entry_rejected(isolated_logger):
    log_id = isolated_logger.log(
        appeal_text="спам",
        candidates=[],
        agent_questions=[],
        overall_confidence=0.3,
    )

    asyncio.run(verify(
        VerifyRequest(log_id=log_id, action="reject"),
        logger=isolated_logger,
    ))

    assert isolated_logger.read_all()[0]["verification"]["status"] == "rejected"


# ── Тесты /examples ───────────────────────────────────────────────────────────

def test_examples_returns_list_from_file(examples_file):
    result = asyncio.run(get_examples(examples_path=examples_file))

    assert "examples" in result
    assert len(result["examples"]) == 2
    assert result["examples"][0]["id"] == "ex01"
    assert result["examples"][0]["title"] == "Пример 1"
```

- [ ] **Step 1.2: Прогнать тесты — должны падать с ImportError**

Run: `python -m pytest tests/test_api_endpoints.py -v`

Expected: FAIL — `ImportError: cannot import name 'verify' from 'api_server'` (и `get_examples`, `VerifyRequest`).

- [ ] **Step 1.3: Коммит тестов как failing baseline**

```bash
cd "c:/Users/Администратор/Desktop/Class OG Final"
git add tests/test_api_endpoints.py
git commit -m "test: failing tests for /verify and /examples endpoints"
```

---

## Task 2: Расширить `ClassifyResponse` — добавить `log_id`

**Files:**
- Modify: `src/api_server.py:61-69` (схема `ClassifyResponse`), `src/api_server.py:123-132` (return блок)

- [ ] **Step 2.1: Добавить поле `log_id` в `ClassifyResponse`**

Найти класс `ClassifyResponse` (около строки 61) и заменить целиком на:

```python
class ClassifyResponse(BaseModel):
    appeal_id: Optional[str]
    log_id: Optional[str]               # ← НОВОЕ: для последующего /verify
    vid_obrascheniya: str
    tip_obrascheniya: str
    is_ustnoe: bool
    questions: list[QuestionResult]
    overall_confidence: float
    needs_verification: bool
    operator_card: str
```

- [ ] **Step 2.2: Пробросить `log_id` в `return` функции `classify_appeal`**

Найти `return ClassifyResponse(...)` (около строки 123) и добавить строку `log_id=result.log_id,` сразу после `appeal_id=request.appeal_id,`:

```python
        return ClassifyResponse(
            appeal_id=request.appeal_id,
            log_id=result.log_id,                  # ← НОВОЕ
            vid_obrascheniya=result.vid_obrascheniya,
            tip_obrascheniya=result.tip_obrascheniya,
            is_ustnoe=result.is_ustное,
            questions=questions_out,
            overall_confidence=result.overall_confidence,
            needs_verification=result.needs_verification,
            operator_card=operator_card,
        )
```

- [ ] **Step 2.3: Проверить, что прежние тесты не сломались**

Run: `python -m pytest tests/test_search.py -v`

Expected: 6/6 PASS (эти тесты не зависят от api_server, но прогоняем для уверенности).

- [ ] **Step 2.4: Коммит**

```bash
git add src/api_server.py
git commit -m "feat(api): expose log_id in /classify response for verification"
```

---

## Task 3: Создать `data/test_appeals.json` — 10 примеров

**Files:**
- Create: `data/test_appeals.json`

- [ ] **Step 3.1: Записать файл**

Создать `data/test_appeals.json` с содержимым:

```json
[
  {
    "id": "ex01",
    "title": "Ремонт дороги (ЖКХ)",
    "text": "Прошу провести ремонт дорожного покрытия по улице Ленина у дома №15. На проезжей части образовалась глубокая яма, представляющая опасность для автомобилей и пешеходов. Жалобы в управляющую компанию остались без ответа."
  },
  {
    "id": "ex02",
    "title": "Жалоба на вывоз мусора",
    "text": "Жалуюсь на регулярные срывы графика вывоза твёрдых коммунальных отходов с контейнерной площадки по адресу: ул. Гагарина, д. 7. Мусор не вывозится по 4-5 дней, контейнеры переполнены, появились грызуны."
  },
  {
    "id": "ex03",
    "title": "Субсидия ЖКУ пенсионеру",
    "text": "Прошу разъяснить, как оформить субсидию на оплату жилищно-коммунальных услуг. Я пенсионер, проживаю одна, размер пенсии — 14 200 рублей, расходы на ЖКУ превышают 25% дохода. Куда нужно обратиться и какие документы подготовить?"
  },
  {
    "id": "ex04",
    "title": "Жалоба на бездействие полиции",
    "text": "Жалуюсь на бездействие участкового уполномоченного полиции по моему заявлению о шумных соседях. Заявление подано две недели назад, никаких действий не принято, ответа не получено. Прошу провести проверку."
  },
  {
    "id": "ex05",
    "title": "Земельный участок под ИЖС",
    "text": "Прошу содействия в предоставлении земельного участка под индивидуальное жилищное строительство. Я многодетная мать (трое детей), стою в очереди уже 4 года. Прошу сообщить о моём текущем номере в очереди и предполагаемых сроках."
  },
  {
    "id": "ex06",
    "title": "Очередь в детский сад",
    "text": "Прошу предоставить место в муниципальном детском саду для моего ребёнка (2 года, дата рождения 15.03.2024). Заявление подано через Госуслуги, очередь не двигается. Прошу разъяснить ситуацию и сообщить ожидаемые сроки."
  },
  {
    "id": "ex07",
    "title": "Незаконная стройка во дворе",
    "text": "Во дворе дома №42 по улице Советской ведётся капитальное строительство пристройки к магазину без видимого ограждения и информационного щита. Жители опасаются за безопасность детей. Прошу провести проверку законности работ."
  },
  {
    "id": "ex08",
    "title": "Задержка пенсии",
    "text": "Жалуюсь на задержку выплаты страховой пенсии за апрель. Дата выплаты по графику — 12 число, сегодня уже 18-е, средства на счёт не поступили. Прошу разобраться и сообщить причину задержки."
  },
  {
    "id": "ex09",
    "title": "Предложение по благоустройству сквера",
    "text": "Предлагаю включить в программу благоустройства на следующий год реконструкцию сквера у школы №3: установить новые скамейки, восстановить освещение, обустроить детскую площадку. Готов представить инициативную группу жителей и проект."
  },
  {
    "id": "ex10",
    "title": "Многовопросное: отопление + капремонт + двор",
    "text": "Жалуюсь сразу на несколько проблем нашего дома №18 по ул. Мира. Во-первых, отопление включают с задержкой и температура в квартире не превышает 17°C. Во-вторых, капитальный ремонт фасада, запланированный на 2025 год, до сих пор не начат. В-третьих, во дворе разрушено асфальтовое покрытие, после дождя невозможно пройти к подъезду. Прошу принять меры по всем трём вопросам."
  }
]
```

- [ ] **Step 3.2: Проверить валидность JSON**

Run:
```bash
python -c "import json; data = json.load(open('data/test_appeals.json', encoding='utf-8')); print(f'OK, {len(data)} examples loaded')"
```

Expected: `OK, 10 examples loaded`

- [ ] **Step 3.3: Коммит**

```bash
git add data/test_appeals.json
git commit -m "data: add 10 test appeals for MVP demo"
```

---

## Task 4: Реализовать `POST /verify` (через DI для тестируемости)

**Files:**
- Modify: `src/api_server.py` (добавить импорты, `VerifyRequest`, `get_logger_dep`, `verify`)

- [ ] **Step 4.1: Расширить импорты в начале файла**

Найти блок импортов в верху `src/api_server.py` (строки 12-20). Заменить на:

```python
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from dataclasses import asdict
from classifier_agent import ClassifierAgent, ClassificationResult
from appeals_logger import AppealsLogger, get_logger
```

- [ ] **Step 4.2: Добавить DI-функцию `get_logger_dep` сразу после блока «Схемы запросов/ответов»**

Найти комментарий `# ── Эндпоинты ─────────────────────` (около строки 72) и **перед ним** вставить:

```python
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
```

- [ ] **Step 4.3: Добавить эндпоинт `/verify` в конец файла**

В самый конец `src/api_server.py` дописать:

```python
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
```

- [ ] **Step 4.4: Прогнать тесты `/verify` — должны пройти**

Run: `python -m pytest tests/test_api_endpoints.py -v -k verify`

Expected: 4 PASS — `test_verify_confirm_marks_entry_confirmed`, `test_verify_correct_writes_operator_codes`, `test_verify_correct_without_codes_returns_400`, `test_verify_reject_marks_entry_rejected`.

Expected fail: тест `test_examples_returns_list_from_file` всё ещё падает (это Task 5).

- [ ] **Step 4.5: Коммит**

```bash
git add src/api_server.py
git commit -m "feat(api): add POST /verify endpoint for operator verification"
```

---

## Task 5: Реализовать `GET /examples`

**Files:**
- Modify: `src/api_server.py` (дописать эндпоинт)

- [ ] **Step 5.1: Добавить эндпоинт `/examples` в конец файла**

В конец `src/api_server.py` (после `/verify`):

```python
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
```

- [ ] **Step 5.2: Прогнать тест `/examples` — должен пройти**

Run: `python -m pytest tests/test_api_endpoints.py -v -k examples`

Expected: 1 PASS — `test_examples_returns_list_from_file`.

- [ ] **Step 5.3: Прогнать всю папку tests/**

Run: `python -m pytest tests/ -v`

Expected: 11 PASS (6 search + 4 verify + 1 examples).

- [ ] **Step 5.4: Коммит**

```bash
git add src/api_server.py
git commit -m "feat(api): add GET /examples endpoint serving test_appeals.json"
```

---

## Task 6: Подключить статику и `GET /` → `index.html`

**Files:**
- Create: `src/static/index.html` (минимальная заглушка для прохода теста)
- Modify: `src/api_server.py` (StaticFiles + GET /)

- [ ] **Step 6.1: Создать минимальный `src/static/index.html`**

Создать файл `src/static/index.html`:

```html
<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>OG Demo</title></head>
<body><p>Загрузка...</p></body>
</html>
```

(Полное содержимое заполним в Task 8 — пока нужна заглушка, чтобы FileResponse не падал.)

- [ ] **Step 6.2: Добавить монтаж статики и `GET /` в `api_server.py`**

В `src/api_server.py` сразу **после** `app = FastAPI(...)` (около строки 26, до `agent: Optional[ClassifierAgent] = None`) вставить:

```python
# Статика и главная страница
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(_STATIC_DIR / "index.html")
```

- [ ] **Step 6.3: Запустить сервер и проверить ручную отдачу**

Run в одном терминале:
```bash
cd "c:/Users/Администратор/Desktop/Class OG Final"
.\venv\Scripts\activate
uvicorn src.api_server:app --host 127.0.0.1 --port 8000
```

В другом терминале (или браузере):
```bash
curl http://127.0.0.1:8000/
```

Expected: HTML «Загрузка...». Если ругается на `_STATIC_DIR` — проверить, что папка создалась.

Также проверить:
```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/examples
```

Expected: первый — `{"status":"ok",...}`, второй — JSON с 10 примерами.

- [ ] **Step 6.4: Остановить сервер (Ctrl+C) и закоммитить**

```bash
git add src/api_server.py src/static/index.html
git commit -m "feat(api): mount static dir and serve index.html on /"
```

---

## Task 7: Полная HTML-разметка `index.html`

**Files:**
- Modify: `src/static/index.html` (полная замена)

- [ ] **Step 7.1: Заменить содержимое `src/static/index.html`**

Полная замена файла:

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Классификация обращений граждан</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 text-gray-900 min-h-screen">
  <div class="max-w-3xl mx-auto p-6">

    <header class="mb-6 flex items-end justify-between">
      <h1 class="text-2xl font-semibold">Классификация обращений граждан</h1>
      <span class="text-sm text-gray-500">v.1 · MVP demo</span>
    </header>

    <!-- Форма ввода -->
    <section class="bg-white rounded-lg shadow p-5 mb-6">
      <label class="block mb-2 font-medium">Примеры обращений:</label>
      <select id="examples-select" class="w-full border rounded px-3 py-2 mb-4">
        <option value="">— Выберите пример —</option>
      </select>

      <label class="block mb-2 font-medium">Текст обращения:</label>
      <textarea
        id="appeal-text"
        rows="8"
        class="w-full border rounded px-3 py-2 mb-4 font-mono text-sm"
        placeholder="Вставьте или введите текст обращения..."></textarea>

      <button
        id="classify-btn"
        class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-3 rounded">
        Классифицировать
      </button>

      <div id="loading" class="hidden mt-4 text-center text-gray-500">
        <span class="inline-block animate-spin">⏳</span> Классификация...
      </div>
    </section>

    <!-- Результат -->
    <section id="result" class="hidden bg-white rounded-lg shadow p-5 mb-6">
      <div id="result-header" class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold">Результат</h2>
        <span id="overall-confidence" class="text-sm font-medium"></span>
      </div>

      <div id="result-meta" class="grid grid-cols-2 gap-3 text-sm mb-4"></div>

      <div id="needs-verification" class="hidden bg-amber-50 border border-amber-200 text-amber-900 rounded px-3 py-2 mb-4 text-sm">
        ⚠️ Требует верификации оператором
      </div>

      <div id="questions" class="space-y-3 mb-4"></div>

      <div class="flex gap-2 mb-3">
        <button id="btn-confirm" class="flex-1 bg-green-600 hover:bg-green-700 text-white py-2 rounded">✓ Подтвердить</button>
        <button id="btn-correct" class="flex-1 bg-amber-500 hover:bg-amber-600 text-white py-2 rounded">✎ Исправить</button>
        <button id="btn-reject" class="flex-1 bg-red-600 hover:bg-red-700 text-white py-2 rounded">✗ Отклонить</button>
      </div>

      <div id="correct-form" class="hidden border-t pt-3 mt-3">
        <label class="block text-sm font-medium mb-1">Введите правильный код вопроса:</label>
        <div class="flex gap-2">
          <input id="correct-code" type="text" placeholder="0005.0005.0051.1103"
                 class="flex-1 border rounded px-3 py-2 font-mono text-sm">
          <button id="btn-correct-save" class="bg-amber-500 hover:bg-amber-600 text-white px-4 rounded">Сохранить</button>
        </div>
      </div>

      <details class="mt-4 text-sm">
        <summary class="cursor-pointer text-gray-600 hover:text-gray-900">▸ Показать JSON ответа</summary>
        <pre id="json-pre" class="mt-2 bg-gray-100 rounded p-3 overflow-x-auto text-xs"></pre>
      </details>
    </section>

    <!-- Toast -->
    <div id="toast" class="hidden fixed bottom-4 right-4 bg-gray-900 text-white px-4 py-2 rounded shadow-lg"></div>

  </div>

  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 7.2: Запустить сервер, открыть в браузере**

Run:
```bash
uvicorn src.api_server:app --host 127.0.0.1 --port 8000
```

Открыть `http://127.0.0.1:8000` — увидеть форму с пустым select, textarea, синюю кнопку. Блок результата скрыт.

Если в консоли браузера ошибка `404 /static/app.js` — нормально, его создаём в Task 8.

- [ ] **Step 7.3: Коммит**

```bash
git add src/static/index.html
git commit -m "feat(ui): full markup for classification demo page"
```

---

## Task 8: `app.js` — загрузка примеров и подстановка в textarea

**Files:**
- Create: `src/static/app.js`

- [ ] **Step 8.1: Создать `src/static/app.js` с базовым каркасом и загрузкой примеров**

Файл `src/static/app.js`:

```javascript
// ── Состояние ───────────────────────────────────────────────────────────────
let lastResult = null;        // последний JSON ответа /classify (для верификации)
let lastLogId = null;         // log_id последнего результата

// ── Утилиты ─────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

function toast(message, kind = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = "fixed bottom-4 right-4 px-4 py-2 rounded shadow-lg text-white " +
    (kind === "error" ? "bg-red-600" : kind === "success" ? "bg-green-600" : "bg-gray-900");
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 2500);
}

// ── Загрузка примеров ───────────────────────────────────────────────────────
async function loadExamples() {
  try {
    const res = await fetch("/examples");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const select = $("examples-select");
    for (const ex of data.examples) {
      const opt = document.createElement("option");
      opt.value = ex.id;
      opt.textContent = ex.title;
      opt.dataset.text = ex.text;
      select.appendChild(opt);
    }
  } catch (err) {
    toast("Не удалось загрузить примеры: " + err.message, "error");
  }
}

// ── Подстановка текста при выборе примера ────────────────────────────────────
$("examples-select").addEventListener("change", (e) => {
  const opt = e.target.selectedOptions[0];
  if (opt && opt.dataset.text) {
    $("appeal-text").value = opt.dataset.text;
  }
});

// ── Старт ───────────────────────────────────────────────────────────────────
loadExamples();
```

- [ ] **Step 8.2: Перезагрузить страницу и проверить вручную**

Сервер уже запущен. Жёстко обновить `http://127.0.0.1:8000` (Ctrl+F5).

Expected:
- Dropdown содержит 10 пунктов с заголовками примеров.
- Выбор пункта — текст подставляется в textarea.

- [ ] **Step 8.3: Коммит**

```bash
git add src/static/app.js
git commit -m "feat(ui): load examples and populate textarea on select"
```

---

## Task 9: `app.js` — обработчик «Классифицировать» + рендер карточки

**Files:**
- Modify: `src/static/app.js` (дописать)

- [ ] **Step 9.1: Дописать в конец `src/static/app.js`**

```javascript
// ── Цветовая шкала уверенности ───────────────────────────────────────────────
function confidenceBar(value) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.80 ? "bg-green-500" :
    value >= 0.65 ? "bg-yellow-500" :
                    "bg-red-500";
  return `
    <div class="w-full bg-gray-200 rounded h-2 overflow-hidden">
      <div class="${color} h-2" style="width: ${pct}%"></div>
    </div>
    <div class="text-xs text-gray-500 mt-1">Уверенность: ${pct}%</div>
  `;
}

// ── Рендер одного вопроса ────────────────────────────────────────────────────
function renderQuestion(q, idx, total) {
  const altsHtml = (q.alternatives || []).length
    ? `<div class="text-xs text-gray-500 mt-2">Альтернативы: ${
        q.alternatives.map(a => `<span class="font-mono">${a.code}</span>`).join(", ")
      }</div>`
    : "";
  const header = total > 1 ? `<div class="font-medium mb-2">Вопрос ${idx + 1} из ${total}</div>` : "";
  return `
    <div class="border rounded p-3">
      ${header}
      <div class="grid grid-cols-[140px_1fr] gap-y-1 text-sm">
        <div class="text-gray-500">Код:</div>          <div class="font-mono">${q.code}</div>
        <div class="text-gray-500">Тема:</div>         <div>${q.name}</div>
        <div class="text-gray-500">Путь:</div>         <div class="text-xs text-gray-600">${q.full_path}</div>
        <div class="text-gray-500">Ведение:</div>      <div>${q.predmet_vedeniya}</div>
        <div class="text-gray-500">Обоснование:</div>  <div class="text-gray-700">${q.reasoning || "—"}</div>
      </div>
      ${altsHtml}
      <div class="mt-3">${confidenceBar(q.confidence)}</div>
    </div>
  `;
}

// ── Рендер всего результата ──────────────────────────────────────────────────
function renderResult(data) {
  $("result").classList.remove("hidden");

  const overallPct = Math.round(data.overall_confidence * 100);
  $("overall-confidence").textContent = `общая уверенность: ${overallPct}%`;

  $("result-meta").innerHTML = `
    <div><span class="text-gray-500">Вид обращения:</span> <strong>${data.vid_obrascheniya}</strong></div>
    <div><span class="text-gray-500">Тип:</span> <strong>${data.tip_obrascheniya}</strong></div>
  `;

  $("needs-verification").classList.toggle("hidden", !data.needs_verification);

  $("questions").innerHTML = data.questions
    .map((q, i) => renderQuestion(q, i, data.questions.length))
    .join("");

  $("json-pre").textContent = JSON.stringify(data, null, 2);

  // Сбрасываем форму "Исправить" если была раскрыта
  $("correct-form").classList.add("hidden");
  $("correct-code").value = "";

  // Прокрутить к результату
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── Обработчик "Классифицировать" ────────────────────────────────────────────
$("classify-btn").addEventListener("click", async () => {
  const text = $("appeal-text").value.trim();
  if (text.length < 10) {
    toast("Введите текст обращения (минимум 10 символов)", "error");
    return;
  }

  $("classify-btn").disabled = true;
  $("loading").classList.remove("hidden");
  $("result").classList.add("hidden");

  try {
    const res = await fetch("/classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ appeal_text: text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    lastResult = data;
    lastLogId = data.log_id;
    renderResult(data);
  } catch (err) {
    toast("Ошибка классификации: " + err.message, "error");
  } finally {
    $("classify-btn").disabled = false;
    $("loading").classList.add("hidden");
  }
});
```

- [ ] **Step 9.2: Перезагрузить страницу и прогнать пример вручную**

Сервер уже запущен. Ctrl+F5 на `http://127.0.0.1:8000`.

Действия:
1. Выбрать в dropdown «Ремонт дороги (ЖКХ)» — текст подставится.
2. Нажать «Классифицировать».
3. Дождаться результата (обычно 3–8 сек).

Expected:
- Появляется блок «Результат».
- Видны вид обращения, тип, общая уверенность %, цветная полоса уверенности у вопроса.
- Внизу есть кнопки [Подтвердить / Исправить / Отклонить] (пока не работают — Task 10).
- В `<details>` развёртывается JSON.

Проверить в файле `data/appeals_log.jsonl` — последняя строка должна содержать новую запись с этим обращением.

- [ ] **Step 9.3: Коммит**

```bash
git add src/static/app.js
git commit -m "feat(ui): classify handler with result card and confidence bars"
```

---

## Task 10: `app.js` — обработчики верификации (3 кнопки + инлайн-форма)

**Files:**
- Modify: `src/static/app.js` (дописать)

- [ ] **Step 10.1: Дописать в конец `src/static/app.js`**

```javascript
// ── Верификация: общий хелпер ────────────────────────────────────────────────
async function postVerify(action, operator_codes) {
  if (!lastLogId) {
    toast("Сначала выполните классификацию", "error");
    return;
  }
  const body = { log_id: lastLogId, action };
  if (operator_codes) body.operator_codes = operator_codes;

  try {
    const res = await fetch("/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return true;
  } catch (err) {
    toast("Ошибка верификации: " + err.message, "error");
    return false;
  }
}

// ── Подтвердить ──────────────────────────────────────────────────────────────
$("btn-confirm").addEventListener("click", async () => {
  const ok = await postVerify("confirm");
  if (ok) {
    toast("✓ Подтверждено — записано в лог", "success");
    $("result").classList.add("hidden");
    lastLogId = null;
  }
});

// ── Отклонить ────────────────────────────────────────────────────────────────
$("btn-reject").addEventListener("click", async () => {
  const ok = await postVerify("reject");
  if (ok) {
    toast("✗ Отклонено — записано в лог", "success");
    $("result").classList.add("hidden");
    lastLogId = null;
  }
});

// ── Исправить: раскрыть форму ────────────────────────────────────────────────
$("btn-correct").addEventListener("click", () => {
  $("correct-form").classList.toggle("hidden");
  $("correct-code").focus();
});

// ── Исправить: сохранить ─────────────────────────────────────────────────────
$("btn-correct-save").addEventListener("click", async () => {
  const code = $("correct-code").value.trim();
  if (!code) {
    toast("Введите код вопроса", "error");
    return;
  }
  const ok = await postVerify("correct", [code]);
  if (ok) {
    toast("✎ Исправление сохранено — записано в лог", "success");
    $("result").classList.add("hidden");
    lastLogId = null;
  }
});
```

- [ ] **Step 10.2: Прогнать вручную все три кнопки**

Перезагрузить страницу (Ctrl+F5).

Сценарий A — Подтвердить:
1. Выбрать пример «Ремонт дороги», классифицировать.
2. Нажать [✓ Подтвердить] → toast «Подтверждено», блок результата скрылся.
3. Открыть `data/appeals_log.jsonl`, найти последнюю запись — `verification.status = "confirmed"`.

Сценарий B — Исправить:
1. Выбрать пример «Жалоба на вывоз мусора», классифицировать.
2. Нажать [✎ Исправить] → раскрылась форма.
3. Ввести код `0005.0005.0001.0001`, нажать [Сохранить] → toast «Исправление сохранено».
4. В логе — `verification.status = "corrected"`, `operator_codes: ["0005.0005.0001.0001"]`.

Сценарий C — Отклонить:
1. Любой пример, [✗ Отклонить] → toast «Отклонено».
2. В логе — `verification.status = "rejected"`.

- [ ] **Step 10.3: Коммит**

```bash
git add src/static/app.js
git commit -m "feat(ui): verification handlers (confirm/correct/reject) wired to /verify"
```

---

## Task 11: Финальный прогон 10 примеров + чек-лист готовности

**Files:** ничего не меняем — только проверяем.

- [ ] **Step 11.1: Запустить сервер чисто**

Остановить старый сервер (Ctrl+C), запустить заново:
```bash
uvicorn src.api_server:app --host 127.0.0.1 --port 8000
```

Открыть `http://127.0.0.1:8000` — Ctrl+F5 для сброса кеша.

- [ ] **Step 11.2: Прогнать каждый из 10 примеров и зафиксировать чек-лист**

Для каждого `ex01..ex10`:
- Выбрать в dropdown.
- Классифицировать.
- Записать кратко: «вид: ..., код: ..., уверенность: ...%, ок/проблема».

Отдельно проверить:
- `ex10` (многовопросное) — рисуется **несколько карточек вопросов**.
- Для какого-то примера должен сработать порог `MIN_CONFIDENCE` (по умолчанию 0.65) — должна появиться плашка «Требует верификации». Если ни в одном из 10 такая ситуация не возникла — это нормально, отметить.

Финальные критерии (отметить каждый):
- [ ] Открывается `http://localhost:8000`, видна форма
- [ ] Dropdown содержит 10 пунктов; выбор подставляет текст
- [ ] [Классифицировать] возвращает результат за < 10 сек
- [ ] Карточка показывает вид, тип, все вопросы, цветную шкалу уверенности
- [ ] Multi-вопросный пример (#10) рисует несколько карточек
- [ ] [Подтвердить] записывает `confirmed` в `appeals_log.jsonl`
- [ ] [Исправить] с кодом записывает `corrected` + `operator_codes`
- [ ] [Отклонить] записывает `rejected`
- [ ] При уверенности < 65% — видна плашка «Требует верификации»
- [ ] [Показать JSON] открывает читабельный JSON

- [ ] **Step 11.3: Прогнать pytest целиком**

Run: `python -m pytest tests/ -v`

Expected: 11 PASS (6 search + 4 verify + 1 examples).

- [ ] **Step 11.4: Финальный коммит и обновление статуса в Obsidian**

Если в ходе прогона нашлись косметические правки — внести и закоммитить.

```bash
git status                 # убедиться, что всё закоммичено
git log --oneline -10      # посмотреть историю
```

Открыть `Obsidian vault/Обращения граждан/Работа по проекту/MVP_план_работ.md`, в таблице эпиков пометить EPIC-04 (UI) как «✅ MVP-демо готово (текстовый вход)». Это ручной шаг, делается оператором/пользователем.

---

## Self-Review

**1. Spec coverage:**
- ✅ Бэкенд `log_id` в ответе → Task 2
- ✅ `POST /verify` → Task 4 (+ DI для тестов)
- ✅ `GET /examples` → Task 5
- ✅ `GET /` + статика → Task 6
- ✅ HTML разметка → Task 7
- ✅ Загрузка примеров + dropdown → Task 8
- ✅ Классификация + карточка + цветовая шкала → Task 9
- ✅ Кнопки верификации + инлайн-форма «Исправить» → Task 10
- ✅ Multi-вопрос (рендер нескольких карточек) → Task 9 (renderQuestion цикл) + проверка в Task 11
- ✅ Раскрывающийся «Показать JSON» → Task 7 (разметка) + Task 9 (заполнение)
- ✅ Toast «Требует верификации» — Task 7 (разметка) + Task 9 (показ по флагу)
- ✅ 10 тестовых обращений → Task 3
- ✅ Прогон 10 примеров → Task 11

**2. Placeholder scan:** проверил план — нет `TBD`, нет `«добавить обработку ошибок»` без конкретики, все шаги содержат либо точный код, либо конкретные команды с ожидаемым выводом.

**3. Type consistency:**
- `VerifyRequest(log_id, action, operator_codes)` — согласован между Task 1 (тесты), Task 4 (определение схемы), Task 10 (фронтовый JSON в `postVerify`).
- `get_logger_dep` → используется как `Depends(get_logger_dep)` в `/verify` (Task 4).
- `get_examples_path` → используется как `Depends(get_examples_path)` в `/examples` (Task 5), и подменяется в фикстуре `examples_file` (Task 1) через прямой аргумент.
- Поле `log_id` — добавлено в `ClassifyResponse` (Task 2) + сохраняется в `lastLogId` на фронте (Task 9) + используется в `postVerify` (Task 10). ✅
- В `postVerify` тело — `{log_id, action, operator_codes?}` — совпадает с `VerifyRequest` ✅.

Известный косметический момент: `is_ustnoe`/`is_ustное` (латиница в Pydantic, кириллица в dataclass) — оставляем как есть (это уже работающий код, не цель MVP). Если в Task 11 при прогоне `/classify` вылезет ошибка из-за этого — это будет первый ответ от настоящего вызова, и тогда отдельной правкой переименуем.

---

## Execution Handoff

**Plan complete and saved to `docs/2026-04-21_PLAN_MVP-демо-веб-ui.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — я диспатчу свежий subagent на каждую задачу, делаю review между ними, быстрая итерация.
2. **Inline Execution** — выполняю задачи в этой же сессии через `executing-plans`, батчевое выполнение с чекпоинтами для ревью.

Какой подход выбираете?

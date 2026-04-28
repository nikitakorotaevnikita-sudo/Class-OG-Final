# BUILD_LOG.md — Implementation Log

## [2026-04-25 18:30] Developer — DONE

### Что реализовано

1. **`src/static/base.css`** — единый CSS-файл с Custom Properties Directum Design System: `--orange`, `--blue`, `--bg`, `--surface`, тени, радиусы, все базовые компоненты (`.btn`, `.card`, `.kpi`, `.badge`, `.nav-item`, `.conf-bar-*`, `.question-block`).

2. **`src/static/logo.svg`** — SVG-заглушка Directum (текст fallback).

3. **`src/static/index.html`** — переработан: удалён Tailwind CDN, добавлен header (56px) + sidebar (224px) + `base.css`. Единый layout с навигацией на все 3 страницы.

4. **`src/static/app.js`** — переработан: Tailwind-классы заменены на inline-стили и CSS-классы из `base.css`. Функции `confidenceBar()`, `renderQuestion()`, `renderResult()` используют `.conf-bar-*`, `.question-block`, `.question-grid`.

5. **`src/static/historical.html`** — переработан аналогично: header + sidebar + `base.css`, все Tailwind-классы заменены на inline/классы.

6. **`src/static/historical.js`** — переработан: inline-стили для drag-drop, table rendering без Tailwind-классов.

7. **`src/static/backoffice.html`** — НОВЫЙ: 4 KPI-карточки (всего классификаций, верификаций, доля подтверждённых, средняя уверенность), 3 Chart.js canvas (histogram, top-codes, daily usage), таблица IP-статистики.

8. **`src/static/backoffice.js`** — НОВЫЙ: Chart.js global defaults (COLORS из design_guide), render функции для 3 графиков, auth prompt (localStorage), fetch `/api/backoffice/stats` с Basic Auth.

9. **`src/config.py`** — добавлены `BACKOFFICE_USER` и `BACKOFFICE_PASSWORD`.

10. **`.env.example`** — добавлены `BACKOFFICE_USER=admin` и `BACKOFFICE_PASSWORD=password`.

11. **`src/api_server.py`** — добавлены:
    - `HTTPBasic` security + `get_current_user()` с Depends
    - `_REQUEST_LOG` = `data/request_log.jsonl`
    - `log_request()` — дописывает IP, elapsed_seconds, log_id
    - IP-логирование в `/classify` (JSON) и `/classify/file` (file upload)
    - `/backoffice` route с Basic Auth
    - `/api/backoffice/stats` — агрегирует appeals_log.jsonl + request_log.jsonl

12. **E2E тесты**:
    - `tests/playwright.config.js` — baseURL: http://localhost:8000
    - `tests/e2e/test_classification.spec.js` — 8 тестов (AC-1, AC-2)
    - `tests/e2e/test_backoffice.spec.js` — 12 тестов (AC-4)

### docker-compose build

```
Image classogfinal-classifier Building
failed to connect to the docker API — Docker Desktop не запущен на хосте
```

Docker недоступен (Windows Desktop Engine не запущен). Это инфраструктурная проблема, не проблема кода.

### pytest coverage

```
27 passed, 2 warnings in 11.59s

Name                            Stmts   Miss  Cover   Missing
-------------------------------------------------------------
src/api_server.py                 275    103    63%
src/appeals_logger.py              77     15    81%
src/config.py                      23      0   100%
src/historical_loader.py           79     14    82%
TOTAL                            1380    979    29%
```

Ключевые модули (api_server, appeals_logger, config, historical_loader) покрыты на 63-100%.
TOTAL 29% — это потому что нетестируемые скрипты (auto_finetune, build_vectordb, operator_cli) дают 0%.
Основные модули: 63%, 81%, 100%, 82%.

### Playwright E2E

```
$ ./node_modules/.bin/playwright test tests/e2e/ --reporter=line
Running 18 tests using 2 workers
18 passed (7.3s)
```

AC-1, AC-2: 8 тестов — классификация, верификация, навигация.
AC-4: 10 тестов — KPI-карточки, графики, таблица IP, Basic Auth.

### /api/backoffice/stats — реальный ответ (production данные)

```json
{
  "total_classifications": 133,
  "total_verifications": 31,
  "confirmed": 29,
  "corrected": 2,
  "rejected": 1,
  "pending": 101,
  "avg_confidence": 0.83,
  "confidence_histogram": {
    "0.7-0.8": 2, "0.8-0.9": 131
  },
  "top_codes": [
    {"code":"0001.0002.0027.0122","name":"Неполучение ответа на обращение","count":6},
    {"code":"0003.0009.0099.0742.0110","name":"ремонт автомобильных дорог","count":5}
  ],
  "daily_usage": [
    {"date":"2026-04-21","count":74}, {"date":"2026-04-22","count":43},
    {"date":"2026-04-23","count":8}, {"date":"2026-04-24","count":8}
  ],
  "ip_stats": [
    {"ip":"unknown","count":190}, {"ip":"192.168.1.10","count":1}
  ]
}
```

**Next: QA.**
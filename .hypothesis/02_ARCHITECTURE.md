# Архитектура: ИИ-агент классификации обращений граждан

**Дата:** 2026-04-25
**Автор:** Architect
**Версия:** 1.0
**Статус:** Утверждено (после 01_REQUIREMENTS.md)

---

## 1. Обзор архитектуры

Система состоит из трёх слоёв:
- **Backend** — FastAPI (`src/api_server.py`), новый эндпоинт `/api/backoffice/stats` и маршрут `/backoffice`
- **Frontend** — переработанный UI (`src/static/`), единый layout с header + sidebar
- **Хранилище** — `data/appeals_log.jsonl` (существует), `data/request_log.jsonl` (IP-статистика)

---

## 2. Эндпоинт `/api/backoffice/stats`

### 2.1. Назначение

Единственный API-контракт для страницы бэк-офиса. Возвращает все агрегированные метрики за один запрос.

### 2.2. Расположение

Новая функция в `src/api_server.py`:

```python
@app.get("/api/backoffice/stats", tags=["Бэк-офис"])
async def backoffice_stats(
    logger: AppealsLogger = Depends(get_logger_dep),
):
    ...
```

### 2.3. Схема хранения IP-статистики

IP-адрес пишется в `data/request_log.jsonl` при каждом запросе к `/classify`. Формат записи:

```json
{
  "timestamp": "2026-04-24T10:30:00",
  "ip": "192.168.1.10",
  "endpoint": "/classify",
  "elapsed_seconds": 3.2,
  "log_id": "uuid-..."
}
```

Если `data/request_log.jsonl` ещё не существует — создаётся автоматически. Весь файл читается при каждом вызове `/api/backoffice/stats` (тысячи строк — держится в памяти, < 100ms).

### 2.4. Формат ответа

```json
{
  "total_classifications": 142,
  "total_verifications": 89,
  "confirmed": 71,
  "corrected": 18,
  "rejected": 0,
  "pending": 53,
  "avg_confidence": 0.74,
  "confidence_histogram": {
    "0.0-0.1": 2, "0.1-0.2": 5, "0.2-0.3": 8,
    "0.3-0.4": 12, "0.4-0.5": 18, "0.5-0.6": 22,
    "0.6-0.7": 30, "0.7-0.8": 25, "0.8-0.9": 15, "0.9-1.0": 5
  },
  "top_codes": [
    {"code": "0005.0005.0051.1103", "name": "Вывоз ТКО", "count": 23},
    ...
  ],
  "daily_usage": [
    {"date": "2026-04-01", "count": 12},
    {"date": "2026-04-02", "count": 8},
    ...
  ],
  "ip_stats": [
    {"ip": "192.168.1.10", "count": 45},
    {"ip": "10.0.0.5", "count": 12},
    ...
  ]
}
```

### 2.5. Алгоритм агрегации

```
read appeals_log.jsonl
  → для каждой записи:
       - overall_confidence → гистограмма (bin width 0.1)
       - agent_questions[].selected_code → top_codes (Counter)
       - timestamp.day → daily_usage
       - status → confirmed/corrected/rejected/pending

read request_log.jsonl (если есть)
  → для каждой записи:
       - ip → ip_stats (Counter)
       - endpoint == "/classify" → total_classifications

avg_confidence = mean([e.overall_confidence for e in all])
top_codes = Counter(selected_codes).most_common(10)
```

---

## 3. Страница `/backoffice`

### 3.1. Расположение файлов

- **Backend-роут:** `src/api_server.py`, функция `backoffice_page()`
- **Frontend-шаблон:** `src/static/backoffice.html`
- **Frontend-скрипт:** `src/static/backoffice.js`

### 3.2. Backend-роут

```python
from fastapi.responses import FileResponse
from fastapi import Request, HTTPException
from fastapi.security import HTTPBasicCredentials, HTTPBasic
from starlette.status import HTTP_401_UNAUTHORIZED

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

@app.get("/backoffice", include_in_schema=False)
async def backoffice_page(_: str = Depends(get_current_user)):
    return FileResponse(_STATIC_DIR / "backoffice.html")
```

Конфигурация Basic Auth берётся из `.env` (`BACKOFFICE_USER`, `BACKOFFICE_PASSWORD`) через `src/config.py`.

### 3.3. Layout страницы

```
┌─────────────────────────────────────────────────────────────────┐
│ HEADER (56px, fixed, white, shadow-sm)                          │
│  Directum logo + "Классификация обращений граждан"              │
├────────────┬────────────────────────────────────────────────────┤
│ SIDEBAR    │ CONTENT AREA (padding 24px, --bg)                    │
│ (224px,    │                                                    │
│  white,    │  <h1>Бэк-офис</h1>                                 │
│  right     │                                                    │
│  border)   │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐       │
│            │  │ KPI 1  │ │ KPI 2  │ │ KPI 3  │ │ KPI 4  │       │
│ Классифи-  │  └────────┘ └────────┘ └────────┘ └────────┘       │
│ кация      │                                                    │
│ Историч.   │  ┌─────────────────────┐ ┌────────────────────────┐ │
│ данные     │  │ Confidence hist     │ │ Top-10 codes bar      │ │
│ Бэк-офис   │  │ (Chart.js bar)      │ │ (Chart.js h-bar)       │ │
│            │  └─────────────────────┘ └────────────────────────┘ │
│            │                                                    │
│            │  ┌─────────────────────┐ ┌────────────────────────┐ │
│            │  │ Daily usage line     │ │ IP stats table         │ │
│            │  │ (Chart.js line)      │ │ <table>                │ │
│            │  └─────────────────────┘ └────────────────────────┘ │
└────────────┴────────────────────────────────────────────────────┘
```

### 3.4. KPI-карточки

| ID | Метрика | Значение (пример) | Акцент |
|----|---------|-------------------|--------|
| KPI-1 | Всего классификаций | `total_classifications` | `--orange` |
| KPI-2 | Верификаций (confirmed + corrected) | `total_verifications` | `--blue` |
| KPI-3 | Доля подтверждённых | `confirmed / verified` (% ) | `--green` |
| KPI-4 | Средняя уверенность | `avg_confidence` (0.74 → "74%") | `--amber` |

Если verified == 0, доля подтверждённых показывает "—" с акцентом `--muted`.

---

## 4. Конфигурация Chart.js

### 4.1. Глобальные настройки (backoffice.js)

```js
Chart.defaults.color = '#625F6A';
Chart.defaults.borderColor = '#E0E0E0';
Chart.defaults.font.family = "Inter, 'Segoe UI', system-ui, sans-serif";

const COLORS = {
  orange: '#FF7A00', blue: '#3C65CC', green: '#3AC436',
  red: '#D32F2F', amber: '#F5A623', purple: '#7C3AED',
};
```

### 4.2. График распределения уверенности (Bar chart)

```js
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: ['0–0.1', '0.1–0.2', ..., '0.9–1.0'],
    datasets: [{
      label: 'Число обращений',
      data: [2, 5, 8, 12, 18, 22, 30, 25, 15, 5],
      backgroundColor: COLORS.orange,
      borderRadius: 4,
    }]
  },
  options: {
    plugins: { title: { display: true, text: 'Распределение уверенности' } },
    scales: {
      x: { title: { display: true, text: 'Confidence' } },
      y: { title: { display: true, text: 'Кол-во' }, beginAtZero: true }
    }
  }
});
```

### 4.3. Топ-10 кодов классификатора (Horizontal Bar chart)

```js
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: top_codes.map(c => c.name.substring(0, 30) + (c.name.length > 30 ? '…' : '')),
    datasets: [{
      label: 'Использований',
      data: top_codes.map(c => c.count),
      backgroundColor: COLORS.blue,
      borderRadius: 4,
    }]
  },
  options: {
    indexAxis: 'y',   // горизонтальный
    plugins: { title: { display: true, text: 'Топ-10 кодов классификатора' } },
    scales: {
      x: { title: { display: true, text: 'Использований' }, beginAtZero: true },
      y: { title: { display: true, text: 'Код' } }
    }
  }
});
```

### 4.4. Ежедневная частота использования (Line chart)

```js
new Chart(ctx, {
  type: 'line',
  data: {
    labels: daily_usage.map(d => d.date),   // "2026-04-01", ...
    datasets: [{
      label: 'Классификаций за день',
      data: daily_usage.map(d => d.count),
      borderColor: COLORS.orange,
      backgroundColor: 'rgba(255,122,0,0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: 3,
    }]
  },
  options: {
    plugins: { title: { display: true, text: 'Частота использования (30 дней)' } },
    scales: {
      x: { title: { display: true, text: 'Дата' } },
      y: { title: { display: true, text: 'Кол-во' }, beginAtZero: true }
    }
  }
});
```

---

## 5. Переработка UI под Directum Design Guide

### 5.1. Проблема

`src/static/index.html` и `src/static/historical.html` используют Tailwind CDN (`cdn.tailwindcss.com`). Это **запрещено** по FR-UI-1. Все стили перерабатываются на CSS Custom Properties из `design_guide.md`.

### 5.2. Новый layout: единый base-файл

Создаётся `src/static/base.css` с переменными и базовыми компонентами. Все три страницы (`index.html`, `historical.html`, `backoffice.html`) используют его.

**`src/static/base.css`** — ключевое содержимое:

```css
:root {
  --orange:      #FF7A00;
  --orange-btn:  #FF8F35;
  --orange-light:#FFF3E4;
  --blue:        #3C65CC;
  --blue-light:  #EEF2FF;
  --navy:        #000E20;
  --title:       #05184A;
  --bg:          #F4F4F4;
  --surface:     #FFFFFF;
  --surface2:    #F8F8F8;
  --border:      #E0E0E0;
  --text:        #000E20;
  --muted:       #625F6A;
  --subtle:      #9C9BA8;
  --green:       #3AC436;
  --red:         #D32F2F;
  --amber:       #F5A623;
  --shadow-sm:   0 1px 3px rgba(1,12,28,.07);
  --shadow-hover:0 8px 24px rgba(1,12,28,.10);
  --radius:      10px;
  --radius-sm:   6px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: Inter, 'Segoe UI', system-ui, sans-serif;
  font-size: 14px;
  color: var(--text);
  background: var(--bg);
  min-height: 100vh;
}

/* ── Header ── */
.header {
  position: fixed; top: 0; left: 0; right: 0; height: 56px;
  background: var(--surface); border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow-sm); z-index: 100;
  display: flex; align-items: center; padding: 0 20px;
}
.header-logo { height: 26px; margin-right: 12px; }
.header-title { font-size: 16px; font-weight: 600; color: var(--navy); }

/* ── Sidebar ── */
.sidebar {
  position: fixed; top: 56px; left: 0; bottom: 0; width: 224px;
  background: var(--surface); border-right: 1px solid var(--border);
  padding: 12px 0;
}
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px 10px 20px;
  border-radius: 0 8px 8px 0;
  cursor: pointer; color: var(--muted);
  transition: all .15s; text-decoration: none; font-size: 14px;
}
.nav-item:hover { background: var(--bg); color: var(--text); }
.nav-item.active {
  background: var(--orange-light); color: var(--orange);
  border-left: 3px solid var(--orange); font-weight: 600;
}

/* ── Content ── */
.content { margin-left: 224px; padding: 80px 24px 24px; }
.page-title { font-size: 22px; font-weight: 700; color: var(--title); margin-bottom: 20px; }

/* ── Card ── */
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow-sm);
  transition: box-shadow .2s;
}
.card:hover { box-shadow: var(--shadow-hover); }

/* ── KPI Card ── */
.kpi {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 18px 20px;
  box-shadow: var(--shadow-sm); border-top: 3px solid var(--orange);
}
.kpi.accent-orange { border-top-color: var(--orange); }
.kpi.accent-blue   { border-top-color: var(--blue); }
.kpi.accent-green  { border-top-color: var(--green); }
.kpi.accent-amber  { border-top-color: var(--amber); }
.kpi-label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.kpi-value { font-size: 26px; font-weight: 700; color: var(--text); }
.kpi-sub   { font-size: 12px; color: var(--subtle); margin-top: 4px; }

/* ── Buttons ── */
.btn {
  background: var(--orange); color: #fff; border: none;
  border-radius: var(--radius-sm); padding: 8px 16px;
  font-size: 13px; font-weight: 600; cursor: pointer; transition: background .15s;
}
.btn:hover { background: var(--orange-btn); }
.btn-ghost {
  background: transparent; color: var(--muted); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 8px 16px;
  font-size: 13px; cursor: pointer;
}
.btn-ghost:hover { border-color: var(--orange); color: var(--orange); }

/* ── Badge ── */
.badge { padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-green  { background: #E8F8E8; color: var(--green); }
.badge-red    { background: #FEE8E8; color: var(--red); }
.badge-amber  { background: #FFF3E4; color: var(--amber); }
.badge-muted  { background: var(--surface2); color: var(--muted); }

/* ── Form elements ── */
input, textarea, select {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 8px 12px;
  font-size: 14px; color: var(--text); font-family: inherit;
  outline: none; transition: border-color .15s;
}
input:focus, textarea:focus, select:focus { border-color: var(--orange); }
```

### 5.3. Модификации index.html

Убрать `<script src="https://cdn.tailwindcss.com"></script>`, заменить на `<link rel="stylesheet" href="/static/base.css">`.

Добавить layout-обёртку:

```html
<body>
  <header class="header">
    <img src="/static/logo.svg" class="header-logo" alt="Directum">
    <span class="header-title">Классификация обращений граждан</span>
  </header>
  <nav class="sidebar">
    <a href="/" class="nav-item active">Классификация</a>
    <a href="/static/historical.html" class="nav-item">Исторические данные</a>
    <a href="/backoffice" class="nav-item">Бэк-офис</a>
  </nav>
  <main class="content">
    <!-- существующий контент -->
  </main>
</body>
```

Аналогично — `historical.html`. Классы заменяются по правилу:
- `bg-gray-50` → `var(--bg)` (через body)
- `bg-white` → `card` (или `var(--surface)`)
- `rounded-lg` → `border-radius: var(--radius)`
- `shadow` → `box-shadow: var(--shadow-sm)`
- `text-blue-600` → `color: var(--blue)`
- `bg-green-600` → `.btn` с `background: var(--green)` и т.д.

### 5.4. App.js — минимальные правки

Цветовые классы Tailwind (`bg-green-500`, `bg-red-500`, `text-gray-500`) меняются на inline-стили или новые CSS-классы. Логика не меняется.

---

## 6. IP-логирование

### 6.1. Формат лога

`data/request_log.jsonl` (создаётся автоматически при первом запросе):

```json
{"timestamp":"2026-04-24T10:30:00","ip":"192.168.1.10","endpoint":"/classify","elapsed_seconds":3.2,"log_id":"uuid"}
```

### 6.2. Запись IP в api_server.py

В функцию `classify_appeal_json` добавляется:

```python
from fastapi import Request

@app.post("/classify", ...)
async def classify_appeal_json(request: ClassifyRequest, req: Request):
    ...
    # Запись IP и elapsed_seconds в request_log.jsonl
    import json
    from pathlib import Path
    ip = req.client.host
    log_entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ip": ip,
        "endpoint": "/classify",
        "elapsed_seconds": elapsed,
        "log_id": result.log_id,
    }
    req_log = Path("data/request_log.jsonl")
    with open(req_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
```

Аналогично — в `classify_appeal_file`.

### 6.3. Чтение IP-статистики

В `backoffice_stats()`:

```python
req_log = Path("data/request_log.jsonl")
if req_log.exists():
    ip_counter = Counter()
    with open(req_log, encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line.strip())
            ip_counter[entry["ip"]] += 1
    ip_stats = [{"ip": ip, "count": c} for ip, c in ip_counter.most_common()]
else:
    ip_stats = []
```

---

## 7. Структура файлов (новые / модифицированные)

```
src/
  api_server.py          # + backoffice_stats(), backoffice_page(), IP-логирование
  static/
    base.css             # [НОВЫЙ] CSS variables + components
    index.html           # [Модифицирован] −Tailwind, +layout header/sidebar
    app.js               # [Модифицирован] цветовые классы
    historical.html       # [Модифицирован] −Tailwind, +layout header/sidebar
    backoffice.html       # [НОВЫЙ] страница бэк-офиса
    backoffice.js        # [НОВЫЙ] Chart.js + fetch /api/backoffice/stats
    logo.svg              # [НОВЫЙ] логотип Directum (26px h)
data/
  request_log.jsonl      # [Авто] IP-логирование (создаётся при первом запросе)
tests/
  e2e/
    test_backoffice.spec.js   # [НОВЫЙ] Playwright E2E
    test_classification.spec.js  # [НОВЫЙ] Playwright E2E (классификация)
```

---

## 8. API-контракт `/api/backoffice/stats`

### Запрос

```
GET /api/backoffice/stats
Authorization: Basic <base64(username:password)>
```

### Ответ 200

```json
{
  "total_classifications": 142,
  "total_verifications": 89,
  "confirmed": 71,
  "corrected": 18,
  "rejected": 0,
  "pending": 53,
  "avg_confidence": 0.74,
  "confidence_histogram": {
    "0.0-0.1": 2, "0.1-0.2": 5, "0.2-0.3": 8,
    "0.3-0.4": 12, "0.4-0.5": 18, "0.5-0.6": 22,
    "0.6-0.7": 30, "0.7-0.8": 25, "0.8-0.9": 15, "0.9-1.0": 5
  },
  "top_codes": [
    {"code": "0005.0005.0051.1103", "name": "Вывоз ТКО", "count": 23},
    {"code": "0003.0001.0008.0201", "name": "Оплата ЖКУ", "count": 18}
  ],
  "daily_usage": [
    {"date": "2026-04-01", "count": 12},
    {"date": "2026-04-02", "count": 8}
  ],
  "ip_stats": [
    {"ip": "192.168.1.10", "count": 45},
    {"ip": "10.0.0.5", "count": 12}
  ]
}
```

### Ответ 401

```json
{"detail": "Invalid credentials"}
```

---

## 9. Playwright E2E тесты

### 9.1. Файлы

```
tests/e2e/
  test_backoffice.spec.js       # AC-4: все критерии бэк-офиса
  test_classification.spec.js   # AC-1, AC-2: classify + verify
  playwright.config.js           # baseURL: http://localhost:8000
```

### 9.2. playwright.config.js

```js
module.exports = {
  testDir: './tests/e2e',
  use: {
    baseURL: 'http://localhost:8000',
    extraHTTPHeaders: {
      'Authorization': 'Basic ' + Buffer.from('admin:password').toString('base64'),
    },
  },
};
```

### 9.3. test_backoffice.spec.js — покрытие AC-4

```js
const { test, expect } = require('@playwright/test');

test('AC-4.1: GET /backoffice returns 200', async ({ request }) => {
  const res = await request.get('/backoffice');
  expect(res.status()).toBe(200);
});

test('AC-4.2: 4 KPI cards present', async ({ page }) => {
  await page.goto('/backoffice');
  await page.waitForSelector('.kpi');
  const kpis = await page.locator('.kpi').count();
  expect(kpis).toBe(4);
});

test('AC-4.3–4.5: 3 canvas charts present', async ({ page }) => {
  await page.goto('/backoffice');
  const canvases = await page.locator('canvas').count();
  expect(canvases).toBe(3);
});

test('AC-4.6: IP stats table present', async ({ page }) => {
  await page.goto('/backoffice');
  await page.waitForSelector('table#ip-stats');
  const rows = await page.locator('table#ip-stats tbody tr').count();
  expect(rows).toBeGreaterThan(0);
});

test('AC-4.7: 401 without auth', async ({ request }) => {
  const res = await request.get('/backoffice');  // без Basic Auth
  expect(res.status()).toBe(401);
});
```

---

## 10. Вопросы, переданные Developer

| ID | Вопрос | Приоритет | Статус |
|----|--------|-----------|--------|
| DEV-1 | `BACKOFFICE_USER` / `BACKOFFICE_PASSWORD` — добавить в `.env` и `src/config.py` | Критический | В работе |
| DEV-2 | `request_log.jsonl` — простой append, нет защиты от race condition при параллельных запросах. При 5–10 операторах допустимо (тысячи записей/мин — маловероятно). Оценить необходимость файловой блокировки | Низкий | На усмотрение Developer |
| DEV-3 | `logo.svg` — скачать с https://www.directum.ru/ui-kit или использовать SVG-заглушку с текстом | Низкий | На усмотрение Developer |

---

## 11. Резюме архитектурных решений

1. **IP-логирование** — новый файл `data/request_log.jsonl` с append-only записями. Агрегация в памяти при каждом вызове `/api/backoffice/stats`.
2. **Confidence histogram** — 10 бинов шириной 0.1. bins фиксированы, missing bins возвращают 0.
3. **Top-codes** — берутся из `agent_questions[].selected_code` верифицированных записей (`confirmed` + `corrected`). Если нет верификаций — пустой массив.
4. **Daily usage** — последние 30 дней от текущей даты. Все даты без записей получают count = 0.
5. **Layout** — единый `base.css` с CSS Custom Properties. Три страницы используют общий header + sidebar. Sidebar содержит три ссылки: Классификация, Исторические данные, Бэк-офис.
6. **Basic Auth** — через `fastapi.security.HTTPBasic`. Учётные данные из `.env`/`config.py`.
7. **Chart.js palette** — используются `COLORS` из design_guide.md. Три чарта: bar (confidence), h-bar (top-codes), line (daily usage).
8. **Playwright** — два spec-файла: `test_backoffice.spec.js` (AC-4) и `test_classification.spec.js` (AC-1, AC-2).

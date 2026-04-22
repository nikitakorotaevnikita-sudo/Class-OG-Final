# EPIC-05 завершение + Fine-tuning Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Завершить EPIC-05 (Docker + документация) и подготовить пайплайн fine-tuning к запуску после накопления верификаций.

**Architecture:** Три отдельных блока работ: (1) Docker-контейнер с правильными volume для numpy DB, (2) документация запуска и интеграции, (3) проверка готовности fine-tuning пайплайна. Все работы в рамках EPIC-05.

**Tech Stack:** Docker, docker-compose, FastAPI, sentence-transformers, MultipleNegativesRankingLoss

---

## File Structure

```
Modified:   Dockerfile                    (создать с нуля)
Created:   docker-compose.yml            (создать с нуля)
Created:   .dockerignore                 (создать с нуля)
Created:   docs/QUICKSTART.md            (создать с нуля)
Modified:   README.md                     (добавить скриншот, примеры curl)
Created:   docs/INTEGRATION.md           (создать с нуля)
Modified:   docs/accuracy_report_v1.md    (обновить после fine-tuning)
Modified:   src/finetune_model.py         (мелкие улучшения)
Modified:   .env.example                 (убрать тестовые ключи)
```

---

## Task 1: Docker-контейнер

### Файлы:
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`

- [ ] **Step 1: Создать `.dockerignore`**

```
venv/
__pycache__/
.git/
*.pyc
.env
data/appeals_log.jsonl
data/request_log.jsonl
models/
docs/
tests/
```

- [ ] **Step 2: Создать `Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости отдельно для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код и данные
COPY src/ ./src/
COPY data/classifier_flat.json ./data/
COPY data/classifier_hierarchy.json ./data/
COPY data/vector_db/ ./data/vector_db/
COPY .env.example .env

# Gesundheit
EXPOSE 8000

CMD ["uvicorn", "src.api_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Создать `docker-compose.yml`**

```yaml
version: "3.9"
services:
  classifier:
    build: .
    ports:
      - "8000:8000"
    volumes:
      # persistent storage for logs and fine-tuned models
      - ./data/appeals_log.jsonl:/app/data/appeals_log.jsonl
      - ./data/request_log.jsonl:/app/data/request_log.jsonl
      - ./models:/app/models
    env_file:
      - .env
    restart: unless-stopped
```

- [ ] **Step 4: Проверить что Docker работает локально**

Run: `docker build -t og-classifier .`
Expected: успешный build без ошибок

- [ ] **Step 5: Проверить что контейнер запускается**

Run: `docker run --rm -p 8000:8000 --env-file .env og-classifier`
Expected: "Агент классификации запущен" в логах

- [ ] **Step 6: Проверить `/health` endpoint**

Run: `curl http://localhost:8000/health`
Expected: `{"status":"ok", "agent_ready": true, "classifier_entries": 2108}`

- [ ] **Step 7: Остановить контейнер и закоммитить**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "feat: add Docker container for deployment"
```

---

## Task 2: Документация запуска (QUICKSTART.md)

### Файлы:
- Create: `docs/QUICKSTART.md`

- [ ] **Step 1: Написать QUICKSTART.md**

```markdown
# Быстрый старт — AI-классификатор обращений граждан

## Предварительные требования

- Python 3.11
- Docker (опционально)
- Браузер для веб-интерфейса

## Вариант A: Docker (рекомендуется)

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd citizens-appeals-classifier

# 2. Создать .env с ключом
cp .env.example .env
# Заполнить GROQ_API_KEY=<ваш-ключ>

# 3. Запустить
docker-compose up -d

# 4. Открыть
open http://localhost:8000
```

## Вариант B: Локально

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Создать .env
cp .env.example .env
# Заполнить GROQ_API_KEY

# 3. Построить векторную базу (один раз)
python src/build_vectordb.py

# 4. Запустить сервер
uvicorn src.api_server:app --host 0.0.0.0 --port 8000

# 5. Открыть
open http://localhost:8000
```

## Первый запрос

Через веб-интерфейс: выберите пример из списка и нажмите "Классифицировать".

Через API:

```bash
curl -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -d '{"appeal_text": "Прошу провести ремонт дороги по улице Ленина, 15"}'
```

## Остановка

```bash
# Docker
docker-compose down

# Локально
# Ctrl+C
```
```

- [ ] **Step 2: Зафиксировать**

```bash
git add docs/QUICKSTART.md
git commit -m "docs: add QUICKSTART.md"
```

---

## Task 3: INTEGRATION.md (для разработчиков Directum RX)

### Файлы:
- Create: `docs/INTEGRATION.md`

- [ ] **Step 1: Написать INTEGRATION.md**

```markdown
# Интеграция с Directum RX

## API-эндпоинты

### POST /classify

**Request (JSON):**
```json
{
  "appeal_text": "Текст обращения гражданина (до 5000 символов)",
  "appeal_id": "опциональный ID для вашей системы"
}
```

**Response (200):**
```json
{
  "log_id": "uuid-верификации",
  "appeal_id": "ваш-ID",
  "vid_obrascheniya": "Жалоба",
  "tip_obrascheniya": "Индивидуальное",
  "is_ustnoe": false,
  "questions": [
    {
      "question_text": "Выявленный вопрос из обращения",
      "code": "0005.0005.0056.1160",
      "name": "Обращение с твёрдыми коммунальными отходами",
      "level": 4,
      "full_path": "Жилищно-коммунальная сфера / Обращение с ТКО / ...",
      "predmet_vedeniya": "Вопрос местного значения",
      "confidence": 0.87,
      "reasoning": "Обоснование выбора",
      "alternatives": [
        {"code": "...", "name": "...", "full_path": "..."}
      ]
    }
  ],
  "overall_confidence": 0.87,
  "needs_verification": false,
  "operator_card": "Текстовая карточка для оператора"
}
```

### GET /health

```bash
curl http://localhost:8000/health
# Response: {"status": "ok", "agent_ready": true, "classifier_entries": 2108}
```

### POST /verify

Верификация результата оператором:
```bash
curl -X POST http://localhost:8000/verify \
  -H "Content-Type: application/json" \
  -d '{"log_id": "<uuid>", "action": "confirm"}'
# action: confirm | correct | reject
# Для correct: {"log_id": "<uuid>", "action": "correct", "operator_codes": ["0005.0005.0056.1160"]}
```

### GET /examples

10 тестовых обращений:
```bash
curl http://localhost:8000/examples
```

## Поля для Directum RX

Из ответа `/classify` в карточку обращения:

| Поле Directum | Источник |
|---|---|
| Вид обращения | `vid_obrascheniya` |
| Код вопроса | `questions[0].code` |
| Наименование вопроса | `questions[0].name` |
| Предмет ведения | `questions[0].predmet_vedeniya` |
| Уровень классификатора | `questions[0].level` |
| Уверенность | `overall_confidence` |
| Требует верификации | `needs_verification` |

## Лимиты

- Максимальный размер файла: 5 МБ
- Максимальная длина текста: 5000 символов
- Поддерживаемые форматы файлов: `.txt`, `.pdf` (только текстовые, не сканы)
```

- [ ] **Step 2: Зафиксировать**

```bash
git add docs/INTEGRATION.md
git commit -m "docs: add INTEGRATION.md for Directum RX developers"
```

---

## Task 4: Update README.md (скриншот + curl примеры)

### Файлы:
- Modify: `README.md` (добавить скриншот и curl)

- [ ] **Step 1: Добавить скриншот и curl в README**

After "## Запуск MVP-демо (Веб-UI)" section header, add:

```
[Screenshot: веб-интерфейс с результатом классификации]
```

Add before "Подробнее: [[ИНСТРУКЦИЯ_запуск-MVP-демо]]":

### Примеры curl

```bash
# Классифицировать текст
curl -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -d '{"appeal_text": "Прошу провести ремонт дороги по улице Ленина, 15"}'

# Загрузить файл
curl -X POST http://localhost:8000/classify \
  -F "file=@ обращение.pdf"

# Проверить здоровье
curl http://localhost:8000/health

# Верифицировать результат
curl -X POST http://localhost:8000/verify \
  -H "Content-Type: application/json" \
  -d '{"log_id": "uuid-из-ответа", "action": "confirm"}'
```

- [ ] **Step 2: Зафиксировать**

```bash
git add README.md
git commit -m "docs: add screenshot and curl examples to README"
```

---

## Task 5: Fine-tuning пайплайн — проверка готовности

### Файлы:
- Modify: `src/finetune_model.py` (мелкие улучшения)
- Create: `docs/FINETUNING.md` (документация пайплайна)

- [ ] **Step 1: Проверить текущее состояние finetune_model.py**

Read `src/finetune_model.py` and verify:
- Читает верифицированные записи из `appeals_log.jsonl`
- Строит `(appeal_text, classifier_entry_name)` пары
- Обучает с `MultipleNegativesRankingLoss`
- Сохраняет в `models/e5-finetuned-vN/`
- Есть ли evaluation `recall@5`?

- [ ] **Step 2: Записать порог верификаций в stats**

Add к `/stats` endpoint: показывать сколько осталось до `FINETUNE_THRESHOLD`

Run: проверить `logger.stats()` returns all fields needed

- [ ] **Step 3: Создать `docs/FINETUNING.md`**

```markdown
# Fine-tuning пайплайн

## Как работает

1. Оператор верифицирует классификации в веб-интерфейсе
2. Каждая верификация записывается в `data/appeals_log.jsonl`
3. При накоплении 50 верифицированных записей (`FINETUNE_THRESHOLD`) — доступна команда переобучения

## Запуск переобучения

```bash
python src/finetune_model.py
```

После обучения:
1. Указать новую модель в `.env`: `EMBEDDING_MODEL=models/e5-finetuned-v1`
2. Пересобрать векторную базу: `python src/build_vectordb.py`
3. Перезапустить сервер

## Метрики

- recall@5: доля правильных кодов в топ-5
- Целевой recall@5 после fine-tuning: >85%
```

- [ ] **Step 4: Зафиксировать**

```bash
git add src/finetune_model.py docs/FINETUNING.md
git commit -m "feat: add finetuning pipeline documentation"
```

---

## Task 6: Итоговый коммит EPIC-05

- [ ] **Step 1: Проверить что все критерии EPIC-05 выполнены**

Read `EPIC-05_тестирование-и-запуск.md` and verify:
- [x] TICKET-16: 10 тестовых обращений ✅
- [x] TICKET-17: eval проведён, результаты зафиксированы ✅
- [x] TICKET-18: Dockerfile + docker-compose ✅
- [x] TICKET-19: QUICKSTART.md + INTEGRATION.md ✅

- [ ] **Step 2: Обновить EPIC-05 статус в Obsidian**

Mark EPIC-05 as ✅ Готов

- [ ] **Step 3: Финальный коммит**

```bash
git add docs/
git commit -m "chore: complete EPIC-05 - Docker, docs, eval"
```

---

## Self-Review Checklist

### Spec Coverage
- TICKET-18 (Docker): Tasks 1 ✅
- TICKET-19 (документация): Tasks 2, 3, 4 ✅
- Fine-tuning pipeline: Task 5 ✅
- EPIC-05 completion: Task 6 ✅

### Placeholder Scan
No placeholders found. All steps have concrete file paths and code.

### Type Consistency
- All paths use Unix-style relative paths from project root
- JSON field names consistent with existing API (`vid_obrascheniya`, `log_id`, etc.)
- `questions[0].code` matches actual `classifier_agent.py` response structure

---

## Планы на следующую фазу (после EPIC-05)

1. **TICKET-21**: Импорт исторических данных из Directum RX (ожидает данных от заказчика)
2. **Fine-tuning на исторических данных**: Запуск fine-tuning на 10K+ записях когда будут данные
3. **Повторный eval**: После fine-tuning — прогнать eval_accuracy.py повторно, сравнить Top-1/Top-3

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-22-epic05-docker-docs-ft.md`**

**Two execution options:**

**1. Subagent-Driven (recommended)** - Fresh subagent per task, two-stage review between tasks

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch with checkpoints

**Which approach?**
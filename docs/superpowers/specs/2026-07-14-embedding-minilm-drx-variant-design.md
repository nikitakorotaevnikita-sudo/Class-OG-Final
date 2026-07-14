# MiniLM Embedding Variant — проверка платформенной модели DRX

**Дата:** 2026-07-14
**Ветка:** `feat/embedding-minilm-drx` (от `main` @ `927a9cf`)
**Статус:** design → готово к плану реализации

## 1. Контекст и цель

Прод-прототип классификации обращений граждан использует эмбеддинг-модель
`intfloat/multilingual-e5-base` (768-dim) с векторной БД `data/vector_db_adapted_v3`
(эмбеддинги 2108 кодов + запечённый обучаемый адаптер 768→768).

Заказчик планирует использовать эмбеддинг-модель **платформы DRX/Directum 360** —
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim, ~120 МБ).
Нужен **отдельный вариант прототипа** на этой модели, чтобы проверить, как поведёт
себя пайплайн на платформенной модели.

Модель подставляется **как локальный SentenceTransformer** и БД пересобирается
штатным `build_vectordb.py` — ровно так же, как собиралась эталонная e5-БД. Никакого
HTTP-сервиса эмбеддингов на данном этапе (это уточнено с заказчиком).

**Критерий успеха:** параллельно с e5-контейнером (порт 8010) поднят MiniLM-контейнер
(порт 8011); на 8011 проходят `/health`, `/classify` и OData-эндпоинт
`/integration/classify-document` на реальном обращении 15920.

## 2. Ключевое архитектурное решение

Смена модели — **чисто конфигурационная**, без правок кода приложения:

- `src/build_vectordb.py` уже ветвит префикс: `use_prefix = "e5" in EMBEDDING_MODEL.lower()`
  → для MiniLM `passage:`-префикс не добавляется.
- `src/classifier_agent.py` (`_embed_query`, строка ~514) так же вешает `query:`-префикс
  только для e5 → для MiniLM запрос кодируется как есть.
- Адаптер (Linear 768→768) включается лишь при `ENABLE_EMBEDDING_ADAPTER=true`. Он
  обучен на e5-эмбеддингах и **неприменим к MiniLM (384-dim)** — в варианте остаётся
  выключенным. Вариант работает «чистым dense», что для проверки платформенной модели
  «как есть» корректно.

Это тот же путь, которым в Phase 1 собирался вариант BGE-M3.

## 3. Компоненты и изменения

### 3.1 Конфигурация — `.env.minilm` (только переопределения)
```
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
VECTOR_DB_DIR=data/vector_db_minilm
ENABLE_EMBEDDING_ADAPTER=false
```
Остальные переменные (Ario-LLM, ключи, флаги реранкера как в проде — в т.ч.
`ENABLE_HEURISTIC_RERANKER=false`) наследуются из основного `.env` через
механизм множественного `env_file` (см. 3.3).

- Реальный `.env.minilm` — в `.gitignore` (как `.env`, т.к. может содержать локальные пути).
- В репозиторий кладётся `.env.minilm.example` с этими тремя строками.

### 3.2 Векторная БД — `data/vector_db_minilm/`
Собирается `build_vectordb.py` с MiniLM-окружением:
- `embeddings.npy` — shape (2108, **384**), float32
- `metadata.json` — те же 2108 записей классификатора (структура идентична проду)

Без адаптера, без файла `adapter_*.json`. Директория — в `.gitignore` (как остальные
`data/vector_db*`), в образ попадает через bind-mount (см. 3.3), не через `COPY`.

### 3.3 Docker — второй сервис в `docker-compose.yml`
Новый сервис `classifier-minilm` рядом с существующим `classifier`:
```yaml
  classifier-minilm:
    build: .                      # тот же образ, что и e5
    ports:
      - "8011:8005"               # host 8011 -> внутренний 8005
    volumes:
      # отдельные лог-файлы, чтобы не конфликтовать с e5-контейнером при конкурентном append
      - ./data/appeals_log_minilm.jsonl:/app/data/appeals_log.jsonl
      - ./data/request_log_minilm.jsonl:/app/data/request_log.jsonl
      - ./data/historical_verified.jsonl:/app/data/historical_verified.jsonl:ro
      - ./data/classifier_annotations.json:/app/data/classifier_annotations.json:ro
      - ./data/vector_db_minilm:/app/data/vector_db_minilm:ro
      - ./models:/app/models
      - C:/Users/Korotaev_NO/.cache/huggingface:/root/.cache/huggingface:ro
      - ./data/allowed_codes_top69.json:/app/data/allowed_codes_top69.json:ro
    env_file:
      - .env
      - .env.minilm               # переопределяет модель/путь БД/адаптер
    restart: unless-stopped
```
Сервис e5 (`classifier`, порт 8010) не изменяется. `env_file` с двумя файлами: значения
из `.env.minilm` переопределяют одноимённые из `.env` (поведение docker compose —
последний файл выигрывает).

### 3.4 Веса модели
MiniLM скачивается один раз в хостовый `~/.cache/huggingface` (командой на этапе
сборки БД — `build_vectordb.py` сам стянет модель при первом `SentenceTransformer(...)`).
Контейнер переиспользует веса через уже существующий `:ro`-маунт HF-кэша, как для e5.

## 4. Поток данных

Идентичен продовому пайплайну (RAG dense-поиск → LLM-рассуждение через Ario →
ClassificationResult). Отличается **только** источник векторов: MiniLM-эмбеддинги из
`data/vector_db_minilm` вместо e5+адаптер из `vector_db_adapted_v3`. Эндпоинты, схема
ответа, извлечение ФИО, OData-интеграция — без изменений.

## 5. Обработка ошибок и риски

- **Размерность.** Если адаптер случайно включён — падение по несовпадению 768 vs 384.
  Гард: явный `ENABLE_EMBEDDING_ADAPTER=false` в `.env.minilm`. Дополнительно план
  реализации предусматривает проверку, что при загрузке БД размерность query-эмбеддинга
  совпадает с размерностью матрицы БД (fail-fast с понятным сообщением).
- **Качество ниже e5.** Ожидаемо (MiniLM меньше и слабее). Это проверка платформенной
  модели «как есть», не гонка за метрикой — деградация допустима.
- **Изоляция от прода.** Вариант пишет только в свои файлы (`.env.minilm`,
  `data/vector_db_minilm`, `data/*_minilm.jsonl`). Продовый `.env` и `vector_db_adapted_v3`
  не затрагиваются.
- **Конкуренция логов.** MiniLM-сервис пишет в собственные `appeals_log_minilm.jsonl` /
  `request_log_minilm.jsonl` (внутри контейнера — те же пути `/app/data/*.jsonl`, но на
  хосте — отдельные файлы), чтобы конкурентный append из двух контейнеров не перемешивал
  строки. Файлы `historical_verified.jsonl` и `classifier_annotations.json` монтируются
  read-only (вариант их только читает).

## 6. Верификация (по апруву — только смоук)

1. Собрать `data/vector_db_minilm` через `build_vectordb.py`.
2. `docker compose up -d` — поднять оба сервиса (e5:8010, minilm:8011).
3. На **8011**:
   - `GET /health` → `agent_ready: true`, `classifier_entries: 2108`.
   - `POST /classify {appeal_text: "…ремонт дороги…"}` → 200, осмысленный код + `llm_provider: ario`.
   - `POST /integration/classify-document {document_id: 15920}` → 200, ФИО + суть + код.

Формальный eval на ii25_test и sanity на выборке заказчика (12) — **вне скоупа** этого
варианта (по решению заказчика).

## 7. Вне скоупа

- HTTP embedding-as-a-service (DRX отдаёт векторизацию по сети) — не в этом этапе.
- Обучение адаптера/файнтюн под MiniLM.
- Формальные метрики качества (eval, A/B).
- Изменения продового e5-контейнера.

## 8. Итоговые артефакты

- Ветка `feat/embedding-minilm-drx`.
- `.env.minilm.example` (в репо), `.env.minilm` (локально, gitignored).
- `data/vector_db_minilm/` (локально, gitignored).
- Пустые лог-файлы `data/appeals_log_minilm.jsonl`, `data/request_log_minilm.jsonl`
  создаются перед первым `up` (иначе Windows bind-mount создаст директории); gitignored.
- Правка `docker-compose.yml` (+сервис `classifier-minilm`), `.gitignore`
  (+`.env.minilm`, +`data/*_minilm.jsonl`; `data/vector_db_minilm` уже под общим правилом `vector_db*`).
- Оба контейнера подняты, смоук на 8011 зелёный.

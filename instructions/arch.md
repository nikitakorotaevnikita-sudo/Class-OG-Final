# Архитектура: процессы, классы и методы

## Для кого и о чём этот документ

Документ для разработчика, который будет сопровождать или дорабатывать код. Здесь
описано, из каких процессов состоит сервис, как одно обращение проходит путь от
запроса Directum RX до ответа и какие классы и функции за это отвечают. Описание
идёт по пакетам `src/`, простым языком: что делает метод, что принимает, что
возвращает и что меняет в базе. Как установить и запустить сервис, читайте в
[Setup_instruction.md](Setup_instruction.md). Таблицы базы данных описаны в
[DB_scheme.md](DB_scheme.md), раскладка файлов — в [file_scheme.md](file_scheme.md),
работа оператора — в [User_manual.md](User_manual.md).

## Содержание

1. [Общая картина](#1-общая-картина)
   - [Процессы](#11-процессы)
   - [Жизнь одного обращения](#12-жизнь-одного-обращения)
   - [Дообучение модели](#13-дообучение-модели)
2. [Пакеты и классы](#2-пакеты-и-классы)
   - [api](#21-api--http-интерфейс)
   - [queueing](#22-queueing--очереди-и-задачи)
   - [worker](#23-worker--процесс-воркера)
   - [classifier](#24-classifier--классификация)
   - [storage](#25-storage--слой-хранения)
   - [training](#26-training--дообучение-и-векторы)
   - [integrations](#27-integrations--внешние-системы)
   - [core](#28-core--настройки)
   - [cli](#29-cli--консольные-утилиты)
   - [scripts/db](#210-scriptsdb--схема-и-миграция)
3. [Правила устройства, которые нельзя нарушать](#3-правила-устройства-которые-нельзя-нарушать)

---

## 1. Общая картина

### 1.1. Процессы

Сервис — это не одна программа, а несколько процессов. Каждый делает свою часть
работы и общается с остальными через базу данных и брокер сообщений.

Несколько терминов, которые понадобятся дальше:

- **Задача (job)** — одна единица работы: «классифицировать обращение», «найти коды
  по запросу», «дообучить модель». У каждой задачи есть строка в таблице `jobs`
  с её статусом: `queued` (ждёт), `running` (выполняется), `done` (готово),
  `error` (ошибка).
- **Брокер сообщений** — отдельная программа, которая принимает сообщения от одного
  процесса и раздаёт их другим. У нас это RabbitMQ.
- **Очередь** — именованный «почтовый ящик» в брокере. Сообщение кладут в очередь,
  а забирает его тот, кто эту очередь слушает.
- **Воркер** — процесс, который слушает очередь и выполняет задачи из неё. Воркеры
  построены на библиотеке Celery.
- **Эмбеддинг (вектор)** — список из 768 чисел, который описывает смысл текста.
  Близкие по смыслу тексты дают близкие векторы. Векторы считает модель
  `multilingual-e5-base` (или её дообученная версия).
- **LLM** — большая языковая модель (например, Qwen через провайдера Ario). Она
  читает текст обращения и список кандидатов и выбирает коды.

Процессы:

| Процесс | Что делает | Где код |
|---|---|---|
| **API** | Принимает HTTP-запросы от RX и веб-интерфейса. Модель не загружает: только создаёт задачи и ждёт результат. | `src/api/` |
| **Воркер классификации** | Слушает очереди `classify_sync` и `classify`. Держит в памяти модель эмбеддингов и выполняет классификацию, поиск по классификатору и расчёт векторов аннотаций. | `src/worker/`, `src/queueing/tasks.py`, `src/classifier/` |
| **Воркер дообучения** | Слушает очередь `training`, выполняет по одной задаче за раз. Дообучает модель и регистрирует новую версию. Запускается только по запросу (профиль `training` в compose, ключ `--training` у `start`). | `src/training/` |
| **PostgreSQL 16 + pgvector** | Хранит всё: классификатор, векторы, карточки обращений, задачи, реестр моделей. pgvector — расширение, которое умеет искать похожие векторы прямо в SQL. | `src/storage/` |
| **RabbitMQ** | Брокер. Три очереди типа quorum (надёжные, переживают перезапуск брокера): `classify_sync`, `classify`, `training`. | `src/queueing/celery_app.py` |
| **migrate** (разовый) | При старте compose создаёт схему БД и один раз переносит файловые данные в PostgreSQL. | `scripts/db/` |

Схема связей:

```
 Directum RX / веб-интерфейс
            │ HTTP (порт 8010)
            ▼
 ┌──────────────────────────┐   INSERT карточки и задачи    ┌───────────────────────┐
 │ API (FastAPI, uvicorn)   │ ────────────────────────────► │ PostgreSQL + pgvector │
 │ без модели и без torch   │ ◄──── LISTEN job_done ─────── │                       │
 └──────────────────────────┘                               └───────────────────────┘
            │ send_task                                         ▲            ▲
            ▼                                                   │            │
 ┌──────────────────────────────────────┐                       │            │
 │ RabbitMQ: classify_sync │ classify │ training │             │            │
 └──────────────────────────────────────┘                       │            │
       │ classify_sync, classify          │ training            │            │
       ▼                                  ▼                     │            │
 ┌────────────────────────────┐   ┌────────────────────────────┐            │
 │ Воркер классификации       │   │ Воркер дообучения          │            │
 │ ClassifierAgent в памяти   │   │ fine-tune → векторы →      │            │
 │ результат + jobs=done      │   │ реестр моделей             │            │
 │ + NOTIFY job_done ─────────┼───┼────────────────────────────┼────────────┘
 └────────────────────────────┘   └────────────────────────────┘
       │ (по желанию) webhook на адрес из запроса RX
       ▼
 Directum RX
```

`LISTEN` и `NOTIFY` — встроенный механизм PostgreSQL для уведомлений. Один процесс
подписывается на канал (`LISTEN job_done`), другой отправляет в него сообщение
(`NOTIFY job_done, '<id задачи>'`). Уведомление доставляется только после фиксации
транзакции, поэтому API узнаёт о готовности задачи ровно тогда, когда результат
уже записан.

### 1.2. Жизнь одного обращения

Возьмём обращение «Прошу отремонтировать дорогу во дворе дома №5». RX может
отправить его двумя способами.

**Синхронный вызов** — `POST /integration/classify-document`. RX держит соединение
открытым и ждёт ответа.

1. API получает текст (`appeal_text`). Если текста нет, но есть `document_id`,
   API забирает тело документа из RX по OData (`integrations/rx_client.py`).
2. В отдельном потоке API удаляет задачи старше `JOB_TTL_HOURS` (24 ч по
   умолчанию), затем **одной транзакцией** создаёт карточку обращения
   (`appeal_classifications`) и строку задачи (`jobs`, статус `queued`).
   Код: `api/job_api.py::_create_rows`.
3. API отправляет в RabbitMQ сообщение `{"job_id", "appeal_id", "document_id"}`
   с именем задачи `classify_appeal` в очередь `classify_sync`
   (`queueing/dispatcher.py::CeleryDispatcher.send`). Параметры задачи
   (провайдер LLM, форма ответа) лежат не в сообщении, а в `jobs.payload`.
   Если брокер недоступен, задача сразу помечается `error`, API отвечает 503.
4. API ждёт: `queueing/waiter.py::JobWaiter.wait`. Ожидание просыпается от
   уведомления `job_done` и на всякий случай раз в 5 секунд перечитывает задачу
   из БД. Предел ожидания — `SYNC_WAIT_TIMEOUT_SEC` (1800 с по умолчанию).
5. Воркер получает сообщение и вызывает `queueing/tasks.py::run_classify_appeal`:
   - «забирает» задачу (`jobs.claim`: статус `running`, `attempts + 1`);
   - открывает транзакцию и блокирует строку задачи (`jobs.lock_owned`);
   - читает текст карточки и вызывает `ClassifierAgent.classify(...)`;
   - агент пишет результат (вопросы, коды, уверенность) в карточку **в той же
     транзакции**;
   - собирает ответ в нужной форме (`api/responses.py`) и вызывает `jobs.finish`:
     статус `done`, ответ в `jobs.result`, `NOTIFY job_done`;
   - транзакция фиксируется — карточка, статус и уведомление появляются разом.
6. API просыпается, читает задачу и возвращает RX готовый JSON
   (`ClassifyDocumentResponse`). Если время вышло — 504, а в тексте ошибки
   указан адрес `GET /integration/jobs/{job_id}`, где результат можно забрать
   позже: задача продолжает выполняться. Если задача завершилась ошибкой — 500.

**Асинхронный вызов** — `POST /integration/classify-document-async`. RX не ждёт.

1. API сначала проверяет `webhook_url` (если указан): хост должен точно совпадать
   с записью из `WEBHOOK_ALLOWED_HOSTS`. Иначе — 400, и к RX за документом даже
   не ходим.
2. Создание карточки и задачи — как в синхронном случае, но задача уходит в
   очередь `classify`, и действует предел очереди `JOB_MAX_QUEUED` (100). Если
   в очереди уже столько задач — 429 («повторите позже»).
3. API сразу отвечает 202 с `job_id`, позицией в очереди и адресом опроса
   (`Location: /integration/jobs/{job_id}`).
4. Воркер выполняет задачу так же, как выше. После фиксации транзакции, если у
   задачи есть `webhook_url`, воркер отправляет туда POST с тем же JSON, что
   отдаёт `GET /integration/jobs/{job_id}` (`queueing/webhook.py::deliver`).
5. RX забирает результат опросом `GET /integration/jobs/{job_id}` или получает
   его через webhook.

Две очереди классификации нужны, чтобы срочные синхронные запросы не стояли за
длинной пачкой асинхронных: воркеры слушают обе, но у каждой своя очередь в
RabbitMQ.

Веб-интерфейс (`POST /classify`, `POST /classify/file`) идёт тем же синхронным
путём, только ответ другой формы (`ClassifyResponse` с карточкой для оператора).

### 1.3. Дообучение модели

**Дообучение (fine-tuning)** — дополнительное обучение модели эмбеддингов на парах
«текст обращения → правильная запись классификатора». Пары берутся из проверенных
оператором карточек и из загруженных исторических данных.

1. Запуск: кнопка в бэк-офисе (`POST /api/finetune`) или скрипт
   `python src/training/auto_finetune.py [--force] [--source verified|historical|combined]`.
2. Создаётся строка `jobs` с типом `finetune`. Одновременно может существовать
   только одна активная задача дообучения: это гарантирует уникальный индекс
   `one_active_finetune`, второй запрос получает 409.
3. Сообщение уходит в очередь `training`. Его берёт воркер дообучения и вызывает
   `training/finetune_model.py::run_training`:
   - проверяет порог `FINETUNE_THRESHOLD` (если не `force`); не набрано — статус
     `skipped`;
   - строит пары, делит их 80/20 на обучение и проверку, дообучает **активную**
     версию модели (3 эпохи, функция потерь `MultipleNegativesRankingLoss`);
   - считает recall@5 до и после. **recall@5** — доля проверочных обращений, у
     которых правильный код попал в пятёрку ближайших;
   - сохраняет модель в `models/<семейство>-finetuned-vN/`;
   - считает векторы классификатора новой моделью и **одной транзакцией**
     регистрирует версию N в `ml_model_registry` (неактивной) и пишет её векторы
     в `classifier_vectors`.
4. Правило активации:
   - если дообучали от версии 0 — новая версия **никогда не активируется
     автоматически**. Версия 0 — это прежние прод-векторы с адаптером, и сравнение
     recall@5 с ней нечестное. Такую версию включают вручную в бэк-офисе;
   - иначе версия активируется, **только если recall@5 вырос**;
   - активация (`models.activate`) отправляет `NOTIFY model_changed`.
5. Воркеры классификации переходят на новую версию без перезапуска: перед каждой
   задачей `worker_main.get_agent()` вызывает `ClassifierAgent.reload_model_if_changed()`,
   которая сравнивает активную версию в реестре со своей и при отличии загружает
   новую модель. Поиск всегда идёт по векторам той версии, которой закодирован
   запрос.

> Замечание для разработчика: уведомление `model_changed` в базу отправляется, но
> в текущем коде его никто не слушает. Переключение происходит за счёт проверки
> реестра перед каждой задачей, поэтому срабатывает со следующей задачей.

---

## 2. Пакеты и классы

Корень импорта — папка `src/`. Модули импортируются по имени пакета:
`from core import config`, `from storage import get_storage`.

### 2.1. api — HTTP-интерфейс

Пакет — тонкий веб-слой на FastAPI. Он принимает запросы, создаёт задачи, ждёт
результат и отдаёт его. Модель и `torch` здесь не загружаются никогда.

#### `api/api_server.py`

Главное приложение `app`. При старте (`lifespan`) создаёт хранилище и запускает
ожидатель `JobWaiter`; при остановке гасит его и закрывает пул соединений с БД.
Подключает роутер интеграции RX и раздаёт статику веб-интерфейса из
`src/api/static/`. Любая ошибка PostgreSQL в эндпоинте превращается в ответ 503
«База данных недоступна».

Вспомогательные функции:

- `get_current_user(credentials)` — проверка HTTP Basic по `BACKOFFICE_USER` /
  `BACKOFFICE_PASSWORD`. Сейчас ею защищена только активация версии модели.
- `get_logger_dep()`, `get_examples_path()` — зависимости FastAPI, тесты их подменяют.
- `_classify_via_job(text, req, payload)` — общий путь `/classify` и
  `/classify/file`: `job_api.run_sync_job("classify", "classify_appeal", ...)`.
- `_dispatch_embeddings(vector_ids)` — для каждой новой аннотации ставит задачу
  `embed_annotation` в очередь `classify`. Сбой постановки не ломает верификацию:
  воркер досчитает вектор при следующем старте.
- `_finetune_snapshot(job)` — переводит задачу `finetune` в формат статуса для
  бэк-офиса (`idle`, `queued`, `running`, `completed`, `failed`).

Эндпоинты:

| Метод | Путь | Что делает | Коды ответа |
|---|---|---|---|
| GET | `/` , `/backoffice` | Страницы веб-интерфейса и бэк-офиса. | 200 |
| GET | `/health` | Состояние: число записей классификатора, доступность БД, брокера и наличие хотя бы одного воркера классификации (`agent_ready`). Не падает. | 200 |
| GET | `/api/llm/options` | Список LLM-провайдеров и моделей для выпадающего списка. | 200 |
| POST | `/classify` | Классификация текста из JSON (`appeal_text`, `llm_provider`, `llm_model`, `appeal_id`). Синхронно через `classify_sync`. | 200; 400 пустой текст; 500 ошибка задачи; 503 БД или брокер; 504 таймаут |
| POST | `/classify/file` | То же для файла TXT/PDF (до 5 МБ). Текст длиннее 5000 символов обрезается, в ответе `was_truncated: true`. | 200; 400 нет текста или ошибка чтения; 413 файл велик; 422 скан PDF; 500; 503; 504 |
| GET | `/classifier/search?q=вывоз мусора&top_k=5` | Поиск по классификатору задачей `search_classifier` (ожидание до 60 с). | 200; 500; 503; 504 |
| POST | `/verify` | Решение оператора: `confirm`, `correct` (с `operator_codes` и необязательной `annotation`), `reject`. | 200; 400 нет кодов для `correct`; 503 |
| GET | `/examples` | Демонстрационные обращения из `data/test_appeals.json`. | 200; 503 нет файла |
| GET | `/stats` | Счётчики проверок и прогресс до порога дообучения. | 200 |
| GET | `/api/settings` | Настройки LLM и RX для формы (секреты маскируются). | 200 |
| POST | `/api/settings` | Записать настройки в `.env` и применить в процессе API. | 200; 400 неверное значение; 500 не удалось записать `.env` |
| POST | `/api/settings/test-rx` | Проверка связи с RX. | 200 (`ok` в теле) |
| POST | `/api/settings/test-llm` | Проверка связи с LLM выбранного провайдера (`GET {base_url}/models`). | 200 (`ok` в теле) |
| POST | `/api/upload-historical` | Загрузка файла исторической разметки (xlsx, xls, csv, json, до 50 МБ), проверка кодов. Результат хранится в памяти процесса до подтверждения. | 200; 400 |
| POST | `/api/confirm-historical` | Сохранить проверенные записи в `historical_appeals` (дубли пропускаются). | 200; 400 нет данных |
| GET | `/api/historical-count` | Сколько исторических записей в БД. | 200 |
| POST | `/api/finetune` | Поставить дообучение в очередь `training` (`source`, `force`). Для `historical` и `combined` порог не проверяется. | 200; 409 уже идёт; 503 |
| GET | `/api/finetune/status` | Статус последней задачи дообучения. | 200 |
| GET | `/api/models` | Версии модели из `ml_model_registry`. | 200 |
| POST | `/api/models/{version}/activate` | Активировать версию (ручное включение или откат). Нужна HTTP Basic авторизация. | 200; 401; 404 |
| GET | `/api/backoffice/stats` | Агрегаты для бэк-офиса за 30 дней (по дням, по кодам, по IP). | 200 |

Интерактивная документация FastAPI доступна по адресу `http://localhost:8010/docs`.

#### `api/integration_api.py`

Роутер с префиксом `/integration` для Directum RX. Формат ответа RX
(`ClassifyDocumentResponse`) не менялся при переходе на очереди.

- `_resolve_text(body)` — текст обращения: из `appeal_text` или, если его нет, из
  RX по `document_id`. Ошибки RX превращает в понятные коды (404, 502, 400).
- `_resolve_text_async(body)` — то же, но поход в RX выполняется в отдельном
  потоке, чтобы не блокировать сервер.

| Метод | Путь | Что делает | Коды ответа |
|---|---|---|---|
| POST | `/integration/classify-document` | Синхронная классификация. Тело: `appeal_text` и/или `document_id`. | 200; 400 нет текста / скан / не извлечь текст; 404 документа нет в RX; 502 RX не отдал тело; 500; 503; 504 |
| POST | `/integration/classify-document-async` | Асинхронная классификация, сразу возвращает `job_id`, `status`, `queue_position`, `poll_url`. Можно передать `webhook_url`. | 202 (+ заголовок `Location`); 400 (в т. ч. webhook не разрешён); 404; 429 очередь полна; 502; 503 |
| GET | `/integration/jobs/{job_id}` | Статус задачи классификации; `result` заполнен только у `done`, `error` — только у `error`. | 200; 404 нет задачи или истёк срок хранения; 503 |
| GET | `/integration/jobs?limit=20` | Сводка очереди классификации и `worker_alive` — для диагностики. | 200; 503 |

#### `api/job_api.py`

Общая часть «поставить задачу и дождаться». Не импортирует модель.

- `submit_job(job_type, task_name, queue, payload, *, appeal_text, document_id, ip, webhook_url, max_queued)` —
  удаляет просроченные задачи, создаёт карточку (если передан текст) и задачу
  в одной транзакции, отправляет сообщение брокеру. Возвращает `Submitted(job, appeal_id)`.
  Ошибка БД — 503; сбой брокера — задача помечается `error`, ответ 503.
  `QueueFull` не ловит: решение (429) за вызывающим.
- `wait_job(job_id, timeout, ...)` — ждёт конечного статуса. `done` → возвращает
  `Job`; таймаут → 504; `error` → 500.
- `run_sync_job(...)` — `submit_job` + `wait_job`; очередь по умолчанию
  `classify_sync`, таймаут — `SYNC_WAIT_TIMEOUT_SEC`, который читается на каждый
  запрос (его можно менять из бэк-офиса).
- `worker_status()` — возвращает `(agent_ready, broker)`: слушает ли очереди
  классификации хотя бы один воркер и отвечает ли брокер. Опрашивает Celery
  (`inspect().active_queues()`), результат кэширует на 2 с; одновременные запросы
  ждут один общий опрос.

#### `api/responses.py`

Модели запросов и ответов (pydantic) и функции сборки ответа. Модуль лёгкий:
его импортируют и API, и воркер, чтобы ответ собирался одинаково.

- `ClassifyRequest`, `ClassifyResponse`, `QuestionResult`, `AlternativeItem` —
  формат `/classify`.
- `build_classify_response(result, operator_card, appeal_id, was_truncated)` —
  `ClassificationResult` → `ClassifyResponse`.
- `ClassifyDocumentRequest`, `ClassifyDocumentResponse`, `RxQuestion` — формат RX.
- `build_document_response(result, document_id)` — ответ RX: вопросы с одинаковым
  кодом схлопываются в один, обоснования склеиваются в одну строку не длиннее
  1000 символов.
- `AcceptedResponse`, `JobStatusResponse`, `job_status_payload(job, queue_position)` —
  ответ 202, тело статуса задачи и тело webhook.

### 2.2. queueing — очереди и задачи

Пакет связывает API и воркеры через RabbitMQ и PostgreSQL.

#### `queueing/celery_app.py`

Создаёт приложение Celery `app` и объявляет три quorum-очереди. Важные настройки:

- `task_acks_late=True` — брокер считает сообщение обработанным только после
  завершения задачи. Если воркер упал посреди работы, сообщение вернётся в
  очередь и достанется другому воркеру.
- `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1` — воркер не
  берёт задачи «впрок», потерянная задача возвращается.
- `broker_heartbeat=BROKER_HEARTBEAT_SEC` (по умолчанию 0) — без heartbeat: иначе
  при долгой задаче RabbitMQ разорвал бы соединение и отдал задачу второму воркеру.
- Короткие таймауты публикации: при недоступном брокере API не висит, а отвечает 503.

Маршруты задач:

| Задача | Очередь по умолчанию | Кто ставит |
|---|---|---|
| `classify_appeal` | `classify` (синхронные вызовы явно указывают `classify_sync`) | API |
| `search_classifier` | `classify_sync` | API, `/classifier/search` |
| `embed_annotation` | `classify` | API, `/verify` с аннотацией |
| `finetune` | `training` | API `/api/finetune`, `auto_finetune.py` |

#### `queueing/tasks.py`

Сами задачи. Логика вынесена в обычные функции `run_*(msg, storage, agent)`: их
вызывают и Celery-обёртки, и тесты, и локальный диспетчер.

- `run_classify_appeal(msg, storage, agent)` — читает текст карточки, вызывает
  `agent.classify(...)` с соединением транзакции, собирает ответ формы
  `classify` или `document` (по `payload.response_kind`) и сохраняет его в
  `jobs.result`.
- `run_search_classifier(msg, storage, agent)` — поиск `top_k` кандидатов по
  `payload.q`; результат `{query, results}`.
- `run_embed_annotation(msg, storage, agent)` — считает вектор текста аннотации
  `payload.vector_id` и записывает его в `classifier_vectors`.
- `run_finetune(msg, storage)` — вызывает `training.finetune_model.run_training`
  вне транзакции (долго). Если результат `failed` — задача завершается `error`.

Общий каркас всех задач (`_run` и `_run_claimed`):

1. `_claim` — `jobs.claim` переводит задачу в `running` и увеличивает `attempts`.
   Если задача уже завершена (повторная доставка) — ничего не делаем. Если
   `attempts > JOB_MAX_ATTEMPTS` (по умолчанию 2) — задача завершается ошибкой
   без запуска работы: так падения воркера не зацикливаются.
2. Транзакция: `jobs.lock_owned(job_id, attempts)` блокирует строку, только если
   задача всё ещё `running` с номером **этой** попытки. Затем работа и
   `jobs.finish(..., attempts=...)`. Это **защита владения**: если сообщение
   тем временем отдали другому воркеру, устаревшая попытка откатывает всё и
   ничего не пишет — ни карточку, ни статус, ни webhook.
3. После фиксации — webhook, если он задан и задача завершилась именно сейчас.

Правила повторов:

- Исключение в задаче при `acks_late` **не** приводит к повторной доставке.
  Поэтому сбой инфраструктуры (`psycopg.OperationalError`, `InterfaceError`,
  ошибка соединения с брокером, `ConnectionError`) превращается в `RetryableError`,
  а Celery-обёртка вызывает `self.retry` с паузой `min(60, 5·2^n)` секунд —
  5, 10, 20, 40, 60, 60… — до `INFRA_MAX_RETRIES = 10` раз (около 8 минут).
  Внутри работы повтор разрешён, пока `attempts < JOB_MAX_ATTEMPTS`.
- Любая другая ошибка (например, `JobDataError` — нет текста или вектора, ошибка
  LLM) сразу завершает задачу статусом `error`.
- Дообучение при ошибке не повторяется.
- Если агент не удалось создать (нет активной модели, битая модель) —
  `fail_without_agent` завершает задачу ошибкой; следующая задача попробует снова.
- Остаточный случай: если БД или брокер недоступны дольше всех повторов, строка
  задачи остаётся `queued`/`running` — фоновой «уборки» таких задач пока нет.

Celery-задачи `classify_appeal`, `search_classifier`, `embed_annotation`,
`finetune` — тонкие обёртки над `run_*` через `_retrying`.

#### `queueing/dispatcher.py`

Отправка задач со стороны API.

- `CeleryDispatcher.send(task_name, msg, queue)` — `app.send_task` по строковому
  имени (модуль задач API не импортирует). Сбой брокера → `DispatchError`.
- `LocalDispatcher(storage, agent_factory, max_workers)` — выполняет задачи в
  потоках того же процесса. Используется в тестах и при `STORAGE_BACKEND=memory`.
  Повторов нет; задача после выполнения гарантированно становится конечной.
  Дообучение идёт в отдельном потоке, не занимая слот классификации.
  `shutdown(timeout)` доделывает начатое и останавливает потоки.
- `get_dispatcher()` — выбирает реализацию по `STORAGE_BACKEND` (одна на процесс).

#### `queueing/waiter.py`

`JobWaiter` — ожидание завершения задачи на стороне API.

- `start()` / `stop()` — запуск и остановка фонового потока. Для PostgreSQL поток
  держит отдельное соединение с `LISTEN job_done` и при обрыве переподключается
  (пауза от 1 до 10 с). После переподключения будит всех ожидающих, потому что
  уведомления могли потеряться. Для memory-хранилища подписывается на эмуляцию
  `NOTIFY` (`jobs.add_listener`).
- `wait(job_id, timeout, recheck_every=5.0)` — возвращает задачу в статусе
  `done`/`error` или `None` по таймауту. Регистрируется до первого чтения, чтобы
  не пропустить уведомление. Ошибка БД при первом чтении пробрасывается (503),
  при последующих — только пишется в журнал.

Почему поток, а не асинхронное соединение: асинхронный psycopg не работает на
цикле событий, который uvicorn использует на Windows.

#### `queueing/webhook.py`

- `is_allowed(url)` — только `http`/`https` и хост, точно совпадающий с записью
  `WEBHOOK_ALLOWED_HOSTS` (форматы `host`, `host:port`, `[ipv6]:port`). Пустой
  список — webhook выключен.
- `sign(body)` — подпись `sha256=<HMAC>` по `WEBHOOK_SECRET` для заголовка
  `X-Signature`; без секрета подписи нет.
- `deliver(url, payload)` — POST с тремя попытками (паузы 0,5 и 1 с), без
  перехода по редиректам. Возвращает `True` при ответе 2xx, исключений не бросает.
  Статус задачи от успеха webhook не зависит.

#### `queueing/warmup.py`

Прогрев агента по сигналам Celery. В режиме `prefork` (дочерние процессы) модель
загружается лениво первой задачей: если грузить её в обработчике запуска
дочернего процесса, Celery убьёт процесс по таймауту. После `fork` модуль сбрасывает
унаследованный пул соединений, хранилище и агента (`reset_inherited_state`). В
режимах `solo`/`threads` агент прогревается сразу после старта воркера.

### 2.3. worker — процесс воркера

#### `worker/worker_main.py`

Держит один `ClassifierAgent` на процесс.

- `get_agent()` — при первом вызове создаёт агента и досчитывает векторы
  аннотаций, у которых их ещё нет. При каждом следующем вызове (то есть перед
  каждой задачей) проверяет, не сменилась ли активная модель
  (`reload_model_if_changed`), и не изменился ли `.env`.
- `embed_pending_annotations(agent)` — векторы для аннотаций текущей версии
  модели, у которых `embedding IS NULL`. Возвращает их число.
- `refresh_settings_if_env_changed()` — **горячая перезагрузка `.env`**: если у
  файла изменилось время модификации, читает его и применяет изменённые
  настройки из списка бэк-офиса (`settings_store.FIELDS_BY_KEY`: провайдер и
  ключи LLM, RX, `SYNC_WAIT_TIMEOUT_SEC`) в этом процессе. Отметка времени
  сдвигается только после успешного применения.

### 2.4. classifier — классификация

#### `classifier/classifier_agent.py`

Самый большой модуль (около 2450 строк). Главный класс — `ClassifierAgent`.

**Структуры данных:**

- `AppealQuestion(text, ordinal, evidence)` — один вопрос после разбиения текста.
- `ClassifiedQuestion` — результат по одному вопросу: код, название, уровень,
  полный путь, предмет ведения, уверенность, обоснование, альтернативы и
  служебные причины снижения уверенности (оператору не показываются).
- `ClassificationResult` — результат по обращению: вид и тип обращения по 59-ФЗ,
  признак устного обращения, список вопросов, общая уверенность,
  `needs_verification`, ФИО и e-mail заявителя, краткая суть, провайдер и модель
  LLM, `log_id` (id карточки), время работы.

**Публичные методы `ClassifierAgent`:**

- `__init__(storage=None)` — берёт активную версию из реестра (нет её —
  `RuntimeError` с подсказкой запустить миграцию), готовит клиента LLM по
  `LLM_PROVIDER`, загружает модель эмбеддингов и записи классификатора из БД.
  При включённых флагах дополнительно загружает CrossEncoder-ранжировщик, веса
  LtR, белый список кодов и адаптер эмбеддингов.
- `classify(appeal_text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None)` —
  основной метод. Возвращает `ClassificationResult`. Если передан `appeal_id`,
  результат пишется в эту карточку; если передан `log_conn`, запись идёт в
  транзакции воркера, и её ошибка пробрасывается (задача не завершится без карточки).
- `reload_model_if_changed()` — если в реестре активна другая версия, загружает её
  модель и записи. Возвращает `True`, если перезагрузил.
- `embed_passage(text)` — вектор текста записи или аннотации (с префиксом
  `passage: ` для моделей семейства e5).
- `format_for_operator(result)` — текстовая карточка результата для оператора.

**Стадии конвейера** (`_classify`):

1. **Разбиение на вопросы.** `split_appeal_questions(text)` делит обращение на
   отдельные вопросы по маркерам и абзацам (не больше 7).
2. **Необязательные шаги LLM перед поиском** (включаются флагами в `.env`):
   переформулировка запроса (`ENABLE_QUERY_EXPANSION`), несколько вариантов
   запроса (`ENABLE_MULTI_QUERY_EXPAND`), выбор разделов классификатора L2 и тем L3
   (`ENABLE_SECTION_ROUTING`, `ENABLE_L3_ROUTING`).
3. **Поиск кандидатов для каждого вопроса** (`_retrieve_for_segment`):
   - плотный поиск `_search_candidates`: запрос кодируется моделью (`query: ...`),
     затем `storage.classifier.search` ищет ближайшие векторы активной версии в
     pgvector по косинусной близости;
   - лексический поиск по словам `_search_lexical_candidates`;
   - объединение пулов и переранжирование: эвристика по совпадению слов и
     иерархии кодов (`_rerank_candidates`), при включённых флагах — CrossEncoder и
     LtR; результат — `TOP_K_CANDIDATES` кандидатов (10).
4. **Выбор кодов LLM** (`_classify_with_llm`). Один вызов LLM на всё обращение:
   текст и кандидаты по каждому вопросу. Модель возвращает строгий JSON: вид и
   тип обращения, код для каждого вопроса, уверенность, обоснование. Временные
   сбои (таймаут, лимит запросов, 5xx, битый JSON) повторяются до
   `LLM_MAX_RETRIES` раз.
5. **Запасной проход** (`ENABLE_FULL_CLASSIFIER_FALLBACK`). Если LLM сказала, что
   подходящих кандидатов нет, или максимальная близость ниже
   `FULL_FALLBACK_SIM_THRESHOLD`, LLM получает весь список конечных записей
   классификатора (`_classify_with_full_classifier`).
6. **Проверка и обогащение.** Выбранный код должен быть среди кандидатов своего
   вопроса; уверенность калибруется (`_calibrate_question_confidence`).
   При включённых флагах добавляются коды «повторное обращение»
   (`ENABLE_REPEAT_DETECTION`) и «отзыв обращения» (`ENABLE_WITHDRAWAL_DETECTION`).
7. **Итог.** Общая уверенность — среднее по вопросам; если она ниже
   `MIN_CONFIDENCE` (0,65), `needs_verification = True`. ФИО заявителя
   (`extract_extra_fields` + `fio_extractor`) и e-mail (`email_extractor`)
   проверяются по тексту.
8. **Запись.** `AppealsLogger.log(...)` сохраняет результат в карточку обращения.
   Каждый запрос к LLM и поиску пишется в `llm_request_log` (`_log_request`,
   никогда не бросает исключений).

**Группы закрытых помощников** (перечислять каждый не будем):

- клиенты LLM: `_resolve_llm`, `_get_groq_client`, `_get_gemini_client`,
  `_get_ollama_client`, `_openai_endpoint`, `_ario_call` (OpenAI-совместимый
  вызов для `ario` и `custom`);
- поиск и ранжирование: `_embed_query`, `_search_candidates`,
  `_search_lexical_candidates`, `_merge_candidate_pools`, `_rerank_candidates`,
  `_lexical_score`, `_ltr_score`, `_ensure_l1_diversity`;
- маршрутизация и расширение запроса: `_route_to_sections`, `_route_to_l3_themes`,
  `_expand_query`, `_multi_query_expand`;
- запасной проход: `_full_leaf_listing`, `_full_classifier_call`.

Функции уровня модуля: `split_appeal_questions`, `extract_extra_fields`,
`detect_repeat_markers`, `detect_withdrawal_markers`.

#### Остальные модули пакета

- `classifier/hierarchy.py` — работа с кодами вида `XXXX.XXXX.XXXX.XXXX`:
  разбор на части, родитель, префикс уровня, «густота» ветки среди кандидатов,
  буст при найденном родителе, доминирующие разделы L1.
- `classifier/reranker.py` — `CrossEncoderReranker`: модель, которая оценивает
  пару «запрос — кандидат» и пересортировывает лучших. Загружается лениво; если
  не загрузилась, кандидаты возвращаются как есть.
- `classifier/section_router.py` — подсказки и разбор ответа LLM для выбора
  разделов L2 и тем L3, фильтры кандидатов по выбранным префиксам.
- `classifier/llm_schemas.py` — JSON-схемы для строгого формата ответа vLLM.
- `classifier/llm_check.py` — `check_connection`, `check_active`: проверка связи
  с LLM через `GET {base_url}/models` для кнопки «Проверить связь».
- `classifier/fio_extractor.py` — `extract_applicant_fio(text)`, `normalize_fio`:
  поиск ФИО именно заявителя, а не подписантов приложений.
- `classifier/email_extractor.py` — `extract_applicant_email(text, llm_hint)`:
  адрес берётся из текста регулярным выражением, подсказка LLM помогает выбрать,
  чей он.

### 2.5. storage — слой хранения

Пакет прячет базу данных за набором «хранилищ» (stores) с одинаковым интерфейсом.
Есть две реализации: PostgreSQL для работы и in-memory для тестов. Выбор — по
`STORAGE_BACKEND` (`postgres` или `memory`).

#### `storage/models.py` — данные и контейнер

- `ClassifierEntry` — запись классификатора (код, название, уровень, родитель,
  полный путь, число детей, `search_text`, порядок).
- `ModelVersion` — строка реестра моделей: номер, базовая модель, путь, признак
  активности, recall@5 до и после, число пар, время, отчёт.
- `AppealResult`, `AppealQuestionRow` — результат классификации для записи в карточку.
- `Job` — строка таблицы `jobs`; свойство `elapsed_sec` — сколько задача
  выполняется или выполнялась.
- Константы статусов (`STATUS_QUEUED` и др.), допустимые типы задач
  (`classify`, `search`, `finetune`, `embed_annotation`).
- Исключения: `QueueFull` (очередь полна → 429), `ActiveJobExists` (дообучение
  уже идёт → 409), `ModelVersionExists` (номер версии занят).
- `Storage` — контейнер со свойствами `classifier`, `models`, `appeals`, `jobs`,
  `historical`, `meta` и методом `transaction()`.

**`transaction()`** — контекстный менеджер для атомарной группы записей. В
PostgreSQL он отдаёт соединение; его передают в методы хранилищ параметром
`conn=`, и все записи фиксируются вместе (или откатываются вместе при ошибке).
Без `conn` каждый метод открывает свою короткую транзакцию. В memory-реализации
`transaction()` ничего не делает и отдаёт `None`.

#### `storage/base.py` — протоколы

Протокол — описание набора методов, которые обязана иметь реализация. Основные:

| Протокол | Методы | Назначение |
|---|---|---|
| `ClassifierStore` | `upsert_entries`, `load_entries`, `count_entries`, `add_vectors`, `search`, `pending_annotations`, `vector_text`, `set_embedding`, `add_annotation`, `annotations_for`, `delete_annotations`, `annotations_by_code`, `annotated_codes`, `vectors_for_version` | Записи классификатора, их векторы и аннотации. `search(query, version, top_k, include_annotations)` возвращает пары `(код, близость)`. |
| `ModelStore` | `register`, `register_new`, `active`, `get`, `list`, `activate`, `next_version` | Реестр версий модели. `activate` делает версию единственной активной и шлёт `NOTIFY model_changed`. `register_new` — только новая версия, под блокировкой. |
| `AppealStore` | `create`, `save_result`, `get_text`, `set_verification`, `as_log_entries`, `stats`, `backoffice_stats`, `log_llm_request`, `ip_addresses`, `llm_log_count`, `set_request_meta` | Карточки обращений, вопросы, проверки оператора, журнал запросов к LLM. |
| `JobStore` | `create`, `get`, `claim`, `lock_owned`, `finish`, `position`, `list`, `stats`, `latest`, `has_active`, `cleanup_expired`, `import_job` | Задачи. См. ниже. |
| `HistoricalStore` | `add`, `count`, `all` | Исторические размеченные обращения. |
| `MetaStore` | `get`, `set` | Служебные отметки (таблица `schema_meta`). |

`DuplicateError` — запись с таким ключом уже есть; обе реализации бросают его одинаково.

Методы `JobStore`, на которых держится очередь:

- `create(job_type, payload, ..., max_queued, queue_class)` — новая задача `queued`.
  С `max_queued` счёт и вставка идут под advisory-блокировкой (блокировка
  PostgreSQL по произвольному ключу), чтобы два параллельных запроса не
  превысили предел. Бросает `QueueFull` или `ActiveJobExists`.
- `claim(job_id)` — `running`, `attempts + 1`, `started_at`; `None`, если задача
  уже завершена.
- `lock_owned(job_id, attempts, conn)` — `SELECT ... FOR UPDATE` строки задачи,
  только если она `running` с этим номером попытки.
- `finish(job_id, result, error, conn, attempts)` — `done` или `error`, запись
  результата и `NOTIFY job_done` в той же транзакции. С `attempts` срабатывает,
  только если задачей всё ещё владеет эта попытка. Возвращает, изменилась ли строка.
- `position(job_id)` — место в очереди среди ждущих задач того же типа и очереди.
- `cleanup_expired(ttl_hours)` — удаляет завершённые задачи старше срока.

#### Реализации

- `storage/pg.py` — `PgStorage` и хранилища `PgClassifierStore`, `PgModelStore`,
  `PgAppealStore`, `PgJobStore`, `PgHistoricalStore`, `PgMetaStore`. Каждый
  метод принимает необязательный `conn`. Поиск — SQL с оператором pgvector
  `<=>` (косинусное расстояние) по `classifier_vectors` нужной версии.
- `storage/memory.py` — `MemoryStorage` и аналогичные классы на словарях с
  блокировками. `MemoryJobStore.add_listener` эмулирует `NOTIFY job_done`.
- `storage/db.py` — пул соединений psycopg (`get_pool`, создаётся лениво,
  регистрирует тип `vector`), `transaction()`, `apply_schema(conn)` (выполняет
  `schema.sql`), `close_pool()`.
- `storage/factory.py` — `get_storage()` (одно хранилище на процесс) и
  `reset_storage()`.
- `storage/schema.sql` — канонический DDL; его байт-в-байт копия лежит в
  `deploy/db/init/002_schema.sql`. Подробно — в [DB_scheme.md](DB_scheme.md).
- `storage/appeal_common.py` — общие для обеих реализаций помощники карточек:
  нормализация UUID и IP, формат старого лога, гистограммы и статистика бэк-офиса.

#### `storage/appeals_logger.py`

`AppealsLogger(storage)` — прежний публичный интерфейс журнала классификаций,
теперь поверх `AppealStore`.

- `log(appeal_text, candidates, agent_questions, overall_confidence, appeal_id, result, conn)` —
  записывает результат в карточку (создаёт её, если `appeal_id` не передан).
  Возвращает id карточки.
- `confirm(log_id, comment)` — статус `confirmed`: положительная пара для обучения.
- `correct(log_id, operator_codes, comment)` — статус `corrected` с правильными
  кодами; комментарий сохраняется как аннотация каждого кода.
- `correct_with_annotations(...)` — то же, плюс возвращает id вставленных строк
  аннотаций (их векторы посчитает задача `embed_annotation`).
- `reject(log_id, comment)` — статус `rejected`: запись не идёт в обучение.
- `read_all()`, `read_verified()`, `stats()` — чтение в формате старого лога и счётчики.
- `get_logger()` — общий экземпляр на процесс.

#### `storage/annotations_storage.py`

Аннотации — пояснения оператора к записи классификатора. Хранятся строками
`classifier_vectors` с `source_type='annotation'` и привязаны к активной версии
модели. Функции: `add_annotation`, `save_annotation`, `get_annotations`,
`delete_annotation`, `list_annotated_codes`, `build_search_text` (собирает текст
записи для векторизации с учётом полей старого формата). В поиске аннотации
участвуют, только если `ENABLE_ANNOTATION_VECTORS=true`.

### 2.6. training — дообучение и векторы

- `training/finetune_model.py`
  - `run_training(storage, source="combined", force=False)` — полный цикл
    дообучения (см. [раздел 1.3](#13-дообучение-модели)). Исключений не
    бросает, возвращает словарь `{status: completed|failed|skipped, message,
    model_path, version, recall5_before, recall5_after, activated, log_tail}`.
    Если версия не зарегистрирована, созданная папка модели удаляется.
  - `build_training_examples(entries, texts)`, `build_historical_examples(records, texts)` —
    пары для обучения из карточек и исторических данных. В режиме `combined`
    исторические пары берутся с весом 3. Меньше 10 пар — ошибка.
  - `evaluate_recall(...)` — recall@k на проверочной выборке.
  - `sentence_transformers_train(...)` — само обучение.
  - `model_dir_name(base, version)` — имя папки `<семейство>-finetuned-vN`.
- `training/build_vectordb.py`
  - `compute_vectors(storage, model_path)` — векторы всех записей классификатора и
    аннотаций (долго, вне транзакции).
  - `build_database(storage, model_path, version, conn=None)` — пишет их в
    `classifier_vectors` для версии. Версию 0 этот модуль не трогает: её переносит
    миграция без пересчёта.
- `training/auto_finetune.py` — `main(force, source)`: проверяет порог по БД и ставит
  задачу `finetune` в очередь `training`. Возвращает id задачи или `None`.
- `training/historical_loader.py` — `parse_file(path)` (xlsx, xls, csv, json),
  `validate_codes(records)` (коды сверяются с `classifier_flat.json`),
  `save_to_historical_db(records, source_file, storage)`.
- `training/auto_import_historical.py` — обходит `data/historical/`, загружает
  новые файлы, переносит обработанные в `processed/`, ошибочные — в `errors/`.
  Режим `--watch` проверяет папку периодически.

### 2.7. integrations — внешние системы

- `integrations/rx_client.py` — клиент OData Directum RX.
  `get_document_text(document_id)` возвращает `(текст, имя файла)`, бросает
  `DocumentNotFound` или `BodyFetchError`. `check_connection(url, user, password)` —
  проверка связи для бэк-офиса.
- `integrations/text_extractor.py` — `extract_text(content, filename)` для TXT
  (UTF-8 или CP1251) и текстовых PDF. Возвращает `(текст, был_ли_обрезан)`.
  Скан без текста — `ScanNotSupportedError`. `validate_file_size(size)` — не больше 5 МБ.
- `integrations/proxy_policy.py` — `uses_env_proxy(url, flag)`: идти ли через
  прокси из переменных окружения. Внутренние адреса (RX, модель заказчика) идут
  напрямую, внешние — через прокси. Флаги `RX_VIA_PROXY` и `LLM_VIA_PROXY`
  позволяют задать поведение явно.

### 2.8. core — настройки

- `core/config.py` — **единственный источник настроек**. Читает `.env` и
  переменные окружения, задаёт значения по умолчанию. Все модули берут константы
  отсюда. Полный список с комментариями — в `.env.example`.
- `core/service_urls.py` — `database_url(env)`, `rabbitmq_url(env)`: если
  `DATABASE_URL` или `RABBITMQ_URL` не заданы, собирают их из `POSTGRES_*` и
  `RABBITMQ_DEFAULT_*` так же, как это делает docker compose.
- `core/env_bootstrap.py` — читает `.env` до импорта библиотек Hugging Face,
  чтобы офлайн-флаги (`HF_HUB_OFFLINE`) дошли до них вовремя. Должен
  импортироваться раньше `torch` и `sentence_transformers`.
- `core/settings_store.py` — настройки бэк-офиса:
  - `read_settings()` — значения для формы, секреты маскируются;
  - `save_settings(values)` — проверить, сделать резервную копию `.env`,
    записать, применить в процессе. Неверное значение — `SettingsError`;
  - `apply_runtime(updates, agent)` — применить значения в работающем процессе:
    `os.environ`, `config` и все модули проекта, которые импортировали это имя
    через `from core.config import X`; сбрасывает кэш клиентов LLM у агента.

### 2.9. cli — консольные утилиты

Запускаются из корня проекта, работают напрямую с БД и агентом (брокер не нужен):

- `cli/test_agent.py` — прогон встроенных тестовых обращений.
- `cli/operator_cli.py` — режим оператора: ввести текст, увидеть результат,
  подтвердить, исправить или отклонить; при достижении порога предлагает дообучение.
- `cli/classify_manual.py` — ручная классификация введённого текста.

### 2.10. scripts/db — схема и миграция

- `scripts/db/init_db.py` — применяет схему (можно запускать повторно).
- `scripts/db/migrate_from_files.py` — разовый перенос файловых данных в БД
  (классификатор, векторы версии 0 из `data/vector_db_adapted_v3`, лог обращений,
  историческая разметка, модели). Повторный запуск ничего не дублирует. Код
  возврата 1, если сверка не сошлась.
- `scripts/db/verify_search_parity.py` — сверяет поиск по файлам (numpy) с поиском
  в pgvector: те же коды в том же порядке, разница близости меньше 1e-5.

---

## 3. Правила устройства, которые нельзя нарушать

**API не импортирует модель.** Процесс API не должен загружать
`classifier.classifier_agent`, `sentence_transformers` и `torch`. Поэтому
`job_api`, `dispatcher` и `responses` не импортируют агента, а `worker_main` и
`queueing.tasks` подключаются только лениво внутри функций. Правило проверяют
тесты `tests/test_api_thin.py::test_api_does_not_import_heavy_modules` и
`tests/test_src_layout.py::test_api_process_does_not_import_model_stack`. Если
нужно что-то общее для API и воркера, кладите это в лёгкий модуль вроде
`api/responses.py`.

**`acks_late` и защита владения.** Сообщение подтверждается брокеру только после
завершения задачи, поэтому при падении воркера задача будет доставлена снова.
Чтобы повторная доставка не записала результат дважды, каждая запись идёт в
транзакции, которая начинается с `jobs.lock_owned(job_id, attempts)`, а
`jobs.finish` условен по номеру попытки. Новый тип задачи стройте на `_run` или
`_run_claimed` из `queueing/tasks.py`, а не в обход них.

**LISTEN/NOTIFY.** Любой конечный исход задачи проходит через `jobs.finish`,
который в той же транзакции делает `NOTIFY job_done`. Если завершить задачу
мимо `finish`, API не проснётся и будет ждать до таймаута (страхует только
перечитывание раз в 5 с). Смена активной модели — только через
`models.activate`.

**Горячая перезагрузка `.env`.** Бэк-офис пишет настройки в `.env`. Процесс API
применяет их сразу, воркеры — перед следующей задачей (по времени изменения
файла). Значит, API и все воркеры обязаны читать **один и тот же** файл `.env`.
Горячо применяются только настройки из формы бэк-офиса; остальные требуют
перезапуска.

**Конфигурация в одном месте.** Новые настройки добавляйте в `core/config.py` и
описывайте в `.env.example`. Не читайте `os.getenv` в других модулях: горячее
применение (`settings_store.apply_runtime`) знает только про `config` и имена,
импортированные из него.

**`src` — корень импорта.** Все процессы запускаются с `src` в `PYTHONPATH`
(в образе Docker — `PYTHONPATH=/app/src`), модули импортируются как
`from core import config`, `from queueing.celery_app import app`. Скрипты,
которые запускаются файлом (`python src/training/finetune_model.py`), сами
подставляют `src` в начало `sys.path`. Модуль не должен импортироваться под двумя
разными именами — это тоже проверяет `tests/test_src_layout.py`. Пакеты
`__init__.py` ничего не импортируют, чтобы импорт одного модуля не тянул соседей.

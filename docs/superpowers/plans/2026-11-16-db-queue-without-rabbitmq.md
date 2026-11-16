# Очередь задач в PostgreSQL вместо RabbitMQ — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать RabbitMQ и Celery: задачи ставятся строкой в таблицу `jobs`, воркеры-процессы забирают их оттуда (`SKIP LOCKED` + `NOTIFY job_queued` + аренда), результат пишется туда же; режимы запуска all / api / worker / docker / migrate.

**Architecture:** `jobs` получает поля `queue, available_at, locked_until, worker_id, delivered_at`. Хранилище (memory и pg) получает методы очереди. Новый модуль `worker/run.py` — цикл воркера (LISTEN + контрольный опрос, захват, продление аренды, повтор с паузой). `queueing/tasks.py` теряет Celery-обёртки, логика `run_*` остаётся. API ставит задачу записью + NOTIFY, `/health` видит воркеров через `pg_stat_activity`.

**Tech Stack:** Python 3.11/3.13, FastAPI, psycopg 3, PostgreSQL 16 + pgvector, pytest; Windows `.cmd` + Linux `.sh`; docker compose v2.

**Spec:** `docs/superpowers/specs/2026-11-16-db-queue-without-rabbitmq-design.md` (утверждён пользователем). Ветка `Database` — коммитить в неё, **не пушить**.

## Global Constraints

- Не меняется для RX: пути, модели запросов/ответов, `202` + 32-hex `job_id`, `429` при полной асинхронной очереди (`JOB_MAX_QUEUED`), `404` неизвестной задачи, sync `504`/`503` (503 — только недоступная БД).
- Одна таблица очереди — `jobs`; вид очереди — поле `queue` ∈ `{'classify_sync','classify','training'}`. Маршруты: sync classify → `classify_sync`; async classify → `classify`; search → `classify_sync`; embed_annotation → `classify`; finetune → `training`.
- NOTIFY-каналы: `job_queued` (payload = имя очереди), `job_done` (payload = id задачи), `model_changed` (payload = версия; никто из процессов проекта не слушает).
- Новые ключи `.env` и значения по умолчанию: `QUEUE_POLL_SEC=5`, `JOB_LEASE_SEC=60` (продление каждые `JOB_LEASE_SEC/3`), `JOB_DELIVERED_GRACE_SEC=600`. Удаляются: `RABBITMQ_URL`, `RABBITMQ_PORT`, `RABBITMQ_MGMT_PORT`, `RABBITMQ_BIND`, `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `BROKER_HEARTBEAT_SEC`, `WORKER_PROC_ALIVE_TIMEOUT_SEC`, `CLASSIFY_CONCURRENCY`. Остаются `CLASSIFY_WORKERS=2`, `JOB_MAX_ATTEMPTS=2`, `JOB_TTL_HOURS=24`, `JOB_MAX_QUEUED`, `SYNC_WAIT_TIMEOUT_SEC`.
- Повтор при сбое БД: пауза `min(60, 5 * 2**n)` с, пока `attempts < JOB_MAX_ATTEMPTS`; `attempts > JOB_MAX_ATTEMPTS` при захвате → ошибка без запуска работы.
- Дообучение: истёкшая аренда → ошибка «воркер дообучения пропал, запустите дообучение заново», без перезапуска; захват `training` берёт только `queued`; версию регистрирует только владелец задачи. Строки `finetune` удаляются только по `JOB_TTL_HOURS`.
- Удаление: `delivered_at` ставится при отдаче конечного результата (sync-ответ, `GET /integration/jobs/{id}` с конечным статусом, успешный webhook); строки с `delivered_at` старше `JOB_DELIVERED_GRACE_SEC` удаляются; конечные без `delivered_at` — старше `JOB_TTL_HOURS`; `embed_annotation` удаляется сразу при успешном завершении.
- `/health`: поля `status, agent_ready, classifier_entries, db` (поле `broker` удаляется). `agent_ready` = есть подключение с `application_name`, начинающимся на `classifier-worker:` и содержащим очередь `classify`.
- Воркер — процесс на одну задачу, `application_name = 'classifier-worker:<очереди через запятую>'`, `worker_id = '<hostname>:<pid>'`.
- API-процесс никогда не импортирует `classifier.classifier_agent`, `sentence_transformers`, `torch` (тест есть).
- `src/storage/schema.sql` и `deploy/db/init/002_schema.sql` побайтно одинаковы; схема идемпотентна для существующих баз (`ADD COLUMN IF NOT EXISTS`, без потери данных).
- `.cmd/.bat`: исполняемые строки только ASCII, CRLF (.gitattributes). `.sh`: LF, `set -euo pipefail`. `setup.ps1` ASCII.
- ПДн не в Git; `stop.*` никогда не делает `docker compose down -v`. Не трогать неотслеживаемые `AGENTS.md`, `humanLang.md`, `new_scheme.md`, `schema.md` и `.env` (кроме задачи 8, где контроллер правит его вручную).
- Известные падения набора тестов (не регрессии): `test_api_endpoints.py::test_get_current_user_accepts_correct_credentials`, три в `test_candidate_rerank.py`.

## Review Focus

1. Два воркера (в т.ч. на разных машинах) одновременно ждут одну очередь → каждая задача выполнена ровно один раз (тест на PostgreSQL: 4 потока-воркера × 40 задач). — Задача 1.
2. Воркер убит посреди задачи → по истечении аренды задачу забирает другой; при `attempts > JOB_MAX_ATTEMPTS` — ошибка, не бесконечный цикл. Для `training` — ошибка без перезапуска. — Задачи 1, 2.
3. Сигнал `job_queued` потерян / задача отложена `available_at` → её подбирает контрольный опрос не позже `QUEUE_POLL_SEC` (тест с отключённым LISTEN). — Задача 2.
4. RX опрашивает готовую задачу: результат доступен до `delivered_at + JOB_DELIVERED_GRACE_SEC`, потом `404`; повторный опрос в течение окна — тот же результат. — Задачи 1, 3.
5. Режим `api` без воркеров: `/health` → `agent_ready:false`, задача стоит `queued`, sync-вызов отвечает `504`; появился воркер — задача выполнена. — Задачи 3, 6.

---

### Task 1: Схема очереди и методы хранилища

**Files:**
- Modify: `src/storage/schema.sql`, `deploy/db/init/002_schema.sql` (копия), `src/storage/models.py` (`Job`), `src/storage/base.py` (`JobStore`), `src/storage/memory.py`, `src/storage/pg.py`
- Test: `tests/storage/test_job_store.py` (существующий, параметризован memory/pg), новый `tests/storage/test_job_queue.py`

**Interfaces:**
- Consumes: существующие `JobStore.create/claim/lock_owned/finish/cleanup_expired`.
- Produces (оба бэкенда):
  - `Job` + поля `queue: str = "classify"`, `available_at: Optional[datetime] = None`, `locked_until: Optional[datetime] = None`, `worker_id: Optional[str] = None`, `delivered_at: Optional[datetime] = None`.
  - `create(..., queue_class)` пишет колонку `queue`; pg в той же транзакции делает `pg_notify('job_queued', queue)`.
  - `claim_next(queues: Sequence[str], worker_id: str, lease_sec: float) -> Optional[Job]` — один атомарный захват (см. spec «Воркер», шаг 2); `attempts+1`, `locked_until = now()+lease`; сначала `classify_sync`; истёкшую аренду перехватывает для всех очередей, кроме `training`.
  - `fail_lost_training() -> list[Job]` — `running` задачи `training` с `locked_until < now()` → `error` с текстом из Global Constraints, `NOTIFY job_done`; возвращает их (для webhook).
  - `extend_lease(job_id: str, attempts: int, lease_sec: float) -> bool` — только при совпадении `attempts` и `status='running'`.
  - `release_for_retry(job_id: str, attempts: int, delay_sec: float, error: str) -> bool` — `queued`, `available_at = now()+delay`, `locked_until=NULL`, `error` сохраняется как последняя причина.
  - `mark_delivered(job_id: str) -> None` — только для конечных статусов, только если `delivered_at IS NULL`.
  - `delete(job_id: str) -> None`.
  - `cleanup_expired(ttl_hours: float, delivered_grace_sec: float) -> int` — правила удаления из Global Constraints (finetune — только TTL).
  - `live_workers(queue: str) -> list[str]` — pg: `application_name` из `pg_stat_activity` (`LIKE 'classifier-worker:%'` и очередь в списке); memory: реестр `register_worker(name)/unregister_worker(name)`.
  - `finish(...)` сбрасывает `locked_until`.

- [ ] **Step 1: Failing tests** в `tests/storage/test_job_queue.py` (параметр `storage` memory/pg, кроме помеченных `db`):
  - `test_create_sets_queue_and_available_at` — `create(..., queue_class="classify_sync")` → `job.queue == "classify_sync"`, `available_at` не None.
  - `test_claim_next_prefers_sync_then_fifo` — 2 в `classify`, 1 в `classify_sync` → первым sync, затем по `created_at`.
  - `test_claim_next_respects_available_at` — `release_for_retry(delay_sec=60)` → `claim_next` → None.
  - `test_expired_lease_is_reclaimed_with_attempts_plus_one` (lease 0.05 c, sleep) → `attempts == 2`.
  - `test_training_expired_lease_fails_not_reclaimed` — `claim_next(["training"])` после истечения → None; `fail_lost_training()` → 1 задача `error`, текст содержит «запустите дообучение заново».
  - `test_extend_lease_only_for_owner` — чужой `attempts` → False.
  - `test_cleanup_rules` — delivered старше grace удалена; delivered свежая — нет; конечная без delivered старше TTL удалена; finetune с delivered — не удалена до TTL.
  - `test_mark_delivered_ignores_non_terminal`.
  - `@pytest.mark.db test_concurrent_claim_exactly_once` — 4 потока × свой `PgStorage` × 40 задач: каждый id взят ровно один раз.
  - `@pytest.mark.db test_create_notifies_job_queued` — LISTEN на отдельном соединении получает payload `classify`.
  - `@pytest.mark.db test_live_workers_from_pg_stat_activity` — соединение с `application_name='classifier-worker:classify_sync,classify'` → `live_workers("classify")` содержит его.
  - `@pytest.mark.db test_schema_upgrade_existing_jobs` — старая форма `jobs` (без новых колонок) + строка с `payload.queue='training'` → после применения схемы `queue='training'`.
- [ ] **Step 2: Run** `PYTHONUTF8=1 TEST_DATABASE_URL=… venv/Scripts/python.exe -m pytest tests/storage/test_job_queue.py -q -p no:cacheprovider` → FAIL (нет методов/колонок).
- [ ] **Step 3: Implement** схему (`ALTER TABLE jobs ADD COLUMN IF NOT EXISTS …`, backfill `queue` из `payload->>'queue'`, CHECK, индекс `jobs_queue_ready ON jobs(queue, available_at) WHERE status='queued'`), модель, протокол, оба бэкенда. Обе копии схемы одинаковы.
- [ ] **Step 4: Run** новый файл + `tests/storage/` целиком → PASS.
- [ ] **Step 5: Commit** `feat(storage): job queue columns and queue methods (claim_next, lease, retry, delivery)`.

### Task 2: Воркер без Celery

**Files:**
- Create: `src/worker/run.py` (цикл, точка входа `python -m worker.run --queues …`)
- Modify: `src/queueing/tasks.py` (убрать Celery-обёртки; `run_*(job, storage, agent)` принимают уже захваченную задачу), `src/worker/worker_main.py` (без Celery-сигналов), `src/training/finetune_model.py` (`run_training(..., still_owner: Optional[Callable[[], bool]] = None)` — проверка перед регистрацией версии)
- Delete: `src/queueing/celery_app.py`, `src/queueing/warmup.py`, `tests/queueing/test_celery_config.py`, `tests/queueing/test_warmup.py`, `tests/test_agent_import_under_celery.py`
- Test: `tests/queueing/test_tasks.py` (переписать под новые сигнатуры), новый `tests/worker/test_run.py`

**Interfaces:**
- Consumes: Task 1 методы очереди; `worker_main.get_agent()`; `queueing.webhook.deliver`.
- Produces:
  - `worker.run.Worker(storage, queues: list[str], worker_id: str, agent_factory, *, poll_sec, lease_sec, listen_dsn: Optional[str])` с методами `run_once() -> bool` (взял ли задачу) и `run_forever(stop: threading.Event)`.
  - `main(argv) -> int`: `--queues` (обязателен), открывает LISTEN-соединение с `application_name='classifier-worker:<queues>'`, прогревает агента (для очередей классификации), ловит SIGINT/SIGTERM.
  - Диспетчеризация по `job.job_type`: `classify → run_classify_appeal`, `search → run_search_classifier`, `embed_annotation → run_embed_annotation` (после успеха `delete`), `finetune → run_finetune`.
  - Сбой инфраструктуры → `release_for_retry(delay=min(60, 5*2**(attempts-1)))` при `attempts < JOB_MAX_ATTEMPTS`, иначе `finish(error)`; webhook при `finish` → при успехе `mark_delivered`.
  - Поток продления аренды на время задачи (`extend_lease` каждые `lease_sec/3`).
  - Перед каждым захватом: `fail_lost_training()` (если слушаем `training`) + webhook для возвращённых; `cleanup_expired(...)` не чаще раза в минуту.
  - Агент не создаётся (нет модели) → задачу не берём, пишем ошибку в журнал, ждём `poll_sec` (не валим задачи).

- [ ] **Step 1: Failing tests** `tests/worker/test_run.py` на memory-хранилище с фальшивым агентом:
  - `test_run_once_processes_classify_and_writes_result`.
  - `test_sync_queue_first`.
  - `test_infra_error_releases_for_retry_with_backoff` (`psycopg.OperationalError` из агента → `queued`, `available_at` ≈ now+5).
  - `test_attempts_cap_finishes_with_error_without_work`.
  - `test_embed_annotation_row_deleted_after_success`.
  - `test_webhook_success_marks_delivered` (monkeypatch `webhook.deliver`).
  - `test_lease_extended_during_long_task` (lease 0.3 c, задача 1 c → не перехвачена вторым воркером).
  - `test_poll_picks_up_without_notify` (`listen_dsn=None`, `poll_sec=0.1`, задача создана после старта → выполнена).
  - `test_no_agent_does_not_claim`.
  - `test_finetune_not_registered_when_not_owner` (`still_owner` → False ⇒ `run_training` не регистрирует; проверка в `tests/test_finetune_registry.py`).
  - `test_worker_module_does_not_import_celery` (subprocess: `import worker.run` → нет `celery`, `kombu` в `sys.modules`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `worker/run.py`, правки `tasks.py`, `worker_main.py`, `finetune_model.py`; удалить Celery-модули и их тесты.
- [ ] **Step 4: Run** `tests/worker tests/queueing tests/test_finetune_registry.py` → PASS.
- [ ] **Step 5: Commit** `feat(worker): PostgreSQL queue worker replaces Celery`.

### Task 3: API без брокера

**Files:**
- Modify: `src/api/job_api.py` (постановка без отправки в брокер; `worker_status()` → `live_workers`; `wait_job` → `mark_delivered`), `src/queueing/dispatcher.py` (оставить только `LocalDispatcher` для `STORAGE_BACKEND=memory`; `CeleryDispatcher`, `DispatchError` удалить), `src/api/api_server.py` (`/health` без `broker`; `/api/finetune/status` — fallback на `ml_model_registry.report`), `src/api/integration_api.py` (`GET /integration/jobs/{id}` конечный статус → `mark_delivered`; `worker_alive` из `live_workers`)
- Test: `tests/test_api_thin.py`, `tests/test_integration_async.py`, `tests/queueing/test_dispatcher_waiter.py` — обновить; новые тесты ниже

**Interfaces:**
- Consumes: Task 1 (`create` с NOTIFY, `mark_delivered`, `live_workers`, `cleanup_expired(ttl, grace)`).
- Produces: `job_api.submit_job(job_type, queue, payload, …)` (без `task_name`); `job_api.worker_status() -> bool` (agent_ready); в memory-режиме `submit_job` передаёт задачу `LocalDispatcher.send(job)`.

- [ ] **Step 1: Failing tests**:
  - `test_health_has_no_broker_and_agent_ready_from_workers` (memory: зарегистрированный воркер → true; без него → false).
  - `test_sync_classify_marks_delivered`.
  - `test_integration_job_poll_marks_delivered_and_repeat_poll_same_result`.
  - `test_poll_after_grace_cleanup_returns_404`.
  - `test_finetune_status_falls_back_to_registry_report` (строки нет, в реестре версия с `report`).
  - `test_api_process_does_not_import_celery_or_model` (subprocess `import api.api_server` → нет `celery`, `kombu`, `classifier.classifier_agent`, `torch`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** весь `tests/` (без e2e) → только 4 известных падения.
- [ ] **Step 5: Commit** `feat(api): enqueue via jobs table, health without broker, delivery tracking`.

### Task 4: Модели по имени папки и MODELS_DIR

**Files:**
- Modify: `src/training/finetune_model.py` (регистрирует `model_path = <имя папки>`), `src/classifier/classifier_agent.py` и/или `src/core/config.py` (функция `resolve_model_path(path: str) -> str`: имя HF-модели как есть; имя папки или абсолютный путь → `MODELS_DIR / последний компонент`, если существует)
- Test: `tests/test_finetune_registry.py`, новый `tests/test_model_path_resolution.py`

**Interfaces:**
- Produces: `core.config.resolve_model_path(path: str) -> str`; `ClassifierAgent` загружает модель через неё; отсутствующая папка → понятная `RuntimeError` («нет папки модели vN в MODELS_DIR=…»), которую Task 2 трактует как «агент не создан».

- [ ] **Step 1: Failing tests**: `test_hf_name_unchanged`, `test_folder_name_resolved_under_models_dir`, `test_legacy_absolute_path_resolved_by_last_component`, `test_missing_folder_clear_error`, `test_finetune_registers_folder_name`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(models): registry stores model folder name, resolved under MODELS_DIR`.

### Task 5: Конфигурация, зависимости, Docker

**Files:**
- Modify: `src/core/config.py`, `.env.example`, `requirements.txt` (без `celery`), `docker-compose.yml` (без `rabbitmq`; `worker-classify`: `python -m worker.run --queues classify_sync,classify`, `deploy.replicas: ${CLASSIFY_WORKERS:-2}`; `worker-training`: `--queues training`, профиль `training`; профиль/флаг для запуска без воркеров), `Dockerfile` (комментарии/команды без Celery), `src/core/service_urls.py`, `scripts/setup_infra_env.py` (только порт PostgreSQL)
- Delete: `deploy/rabbitmq/`
- Test: `tests/test_deploy_layout.py`, `tests/test_setup_infra_env.py`, `tests/test_service_urls.py`

- [ ] **Step 1: Failing tests**: compose без сервиса `rabbitmq` и без `RABBITMQ` где-либо; воркеры запускают `worker.run`; `.env.example` содержит три новых ключа и ни одного удалённого; `setup_infra_env.py` больше не пишет `RABBITMQ_*`; `requirements.txt` без `celery`; `grep -ri "rabbit\|celery\|kombu" src scripts docker-compose.yml Dockerfile requirements.txt .env.example` пуст (тест).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** тесты + `docker compose config -q` и `docker compose --profile training config -q` → 0.
- [ ] **Step 5: Commit** `chore: drop RabbitMQ/Celery from config, compose, requirements`.

### Task 6: Режимы запуска и остановки, установщики, меню

**Files:**
- Modify: `start.cmd`, `start.sh`, `stop.cmd`, `stop.sh`, `launch.bat`, `launch.sh`, `install.bat`, `install.sh`, `install_offline.bat`, `install_offline.sh`, `scripts/setup_infra.cmd`, `scripts/install_common.sh`
- Test: `tests/test_start_scripts_guard.py`, `tests/test_install_scripts.py`, `tests/test_launch_bat.py`, `tests/test_deploy_layout.py`

**Interfaces:**
- Consumes: `python -m worker.run --queues …` (Task 2), `/health` без `broker` (Task 3).
- Produces: режимы `all` (синоним `local`), `api`, `worker`, `docker [--no-workers]`, `migrate`, флаг `--training`; `stop local|all|api|worker|docker`; меню launch: [1] всё, отдельные пункты «без воркеров» и «только воркеры», остальные пункты сохраняются, одинаково в `.bat` и `.sh`; установщик: вопрос/ключ `--worker-only` (спрашивает `DATABASE_URL`, без Docker).

- [ ] **Step 1: Failing tests** (строковые и bash-функции, как в существующих тестах):
  - `start.*` знают режимы `all|local|api|worker|docker|migrate`; `local` == `all`.
  - Режим `api` не запускает `worker.run`; ожидание здоровья требует только `"db":true`.
  - Режим `worker` не вызывает `docker`, не запускает миграцию и API, требует `DATABASE_URL`.
  - Нигде нет `rabbitmq`, `RABBITMQ`, `celery`.
  - `stop.cmd` находит процессы по `worker.run` и `api.api_server:app`.
  - Меню `.bat` и `.sh` совпадают по пунктам.
  - `install.* --worker-only` не вызывает `setup_infra`.
  - `.cmd/.bat` ASCII, `.sh` `set -euo pipefail`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** тесты скриптов → PASS; `bash -n` для `.sh`.
- [ ] **Step 5: Commit** `feat(scripts): start modes all/api/worker/docker/migrate without RabbitMQ`.

### Task 7: Живая проверка (контроллер)

Без коммитов кода, кроме исправлений найденного. Контроллер:
- [ ] Убирает из своего `.env` строки RabbitMQ (машина пользователя, вне Git), останавливает и удаляет контейнер `class-og-final-rabbitmq-1` (том PostgreSQL не трогать).
- [ ] Полный набор тестов на временном тестовом PostgreSQL (5433) → только 4 известных падения; контейнер затем удалить.
- [ ] `launch.bat` [1] (режим `all`) → `/health` `db:true, agent_ready:true`; поиск; синхронная классификация; асинхронная + опрос (результат, затем `delivered_at`); `stop.cmd local`.
- [ ] Режим `api` → `agent_ready:false`, async-задача стоит `queued`; затем `start.cmd worker` в отдельном окне (`DATABASE_URL` на 5434) → задача выполнена; `stop.cmd worker`, `stop.cmd api`.
- [ ] Убийство воркера посреди задачи → задача выполнена другим после аренды.
- [ ] `docker compose config -q`.

### Task 8: Инструкции

**Files:**
- Modify: `instructions/User_manual.md`, `instructions/Setup_instruction.md`, `instructions/file_scheme.md`, `instructions/arch.md`, `instructions/DB_scheme.md`, `instructions/suggestions_for_improvement.md`, `README.md`, `CLAUDE.md`, `docs/DEPLOY.md` (места про RabbitMQ)

- [ ] **Step 1:** убрать все упоминания RabbitMQ/Celery/брокера (`grep -ri "rabbit\|celery\|брокер\|broker"` по этим файлам → только уместные исторические упоминания «раньше использовался RabbitMQ (ветка Database_Rabbit)»).
- [ ] **Step 2:** описать очередь в БД человеческим языком: Setup — режимы all/api/worker/docker/migrate, машина «только воркеры» (доступ к базе, пароль, `MODELS_DIR` и общая папка моделей, перенос настроек бэк-офиса), новые ключи `.env`; arch — путь задачи через `jobs`, цикл воркера, аренда, удаление; DB_scheme — новые поля `jobs`, канал `job_queued`, удаление строк, запросы к очереди; User_manual — меню launch; file_scheme — новые/удалённые файлы.
- [ ] **Step 3:** проверка ссылок (скрипт как в прошлый раз) и SQL-запросов DB_scheme на живой базе (read-only).
- [ ] **Step 4: Commit** `docs: instructions for the PostgreSQL queue without RabbitMQ`.

# Design: PostgreSQL + pgvector, RabbitMQ, isolated workers

Date: 2026-11-11
Branch: `Database_Rabbit`
Source documents: «ТЗ прототипа классификатора», «Описание таблиц классификатора»
(provided by the user in the session that produced this design).

## 1. Intent and why

The prototype keeps all state in files (`appeals_log.jsonl`, `request_log.jsonl`,
`historical_verified.jsonl`, `classifier_annotations.json`, `data/jobs/*.json`,
`embeddings.npy` + `metadata.json`) and does all work inside one FastAPI process
(one job-worker thread, fine-tuning in a background thread). Under concurrent writes
the files can be corrupted, `_update_verification` rewrites the whole log, fine-tuning
can OOM the API serving Directum RX, and unfinished jobs are lost on restart.

Goal: move to a service layout fit for production use in government bodies —
**reliability** (ACID storage, at-least-once delivery, no lost jobs) and **speed**
(parallel workers, no heavy work in the web process) — **without losing any existing
functionality and without changing classification accuracy**.

Target layers (named by the user):

1. **API layer** — thin, stateless FastAPI. Validates, stores the appeal, enqueues,
   waits/reports. Never loads the embedding model or calls the LLM for classification.
2. **Database layer** — PostgreSQL 16 + pgvector. Single source of truth.
3. **Queue manager layer** — RabbitMQ (quorum queues).
4. **Worker layer** — Celery workers: `classify` workers (N, scalable, multi-host)
   and one `training` worker (isolated, ideally on a GPU host).

## 2. Goals, non-goals, anti-goals

Goals
- All file-backed state listed above moves to PostgreSQL (tables from the user's
  spec plus the additions in §6).
- Dense candidate search runs as SQL against pgvector.
- All classification (sync and async endpoints) goes through RabbitMQ → Celery worker.
- Workers write results to the DB themselves; the API learns about completion
  immediately via PostgreSQL `LISTEN/NOTIFY`.
- Optional webhook: if the caller passes `webhook_url`, the worker POSTs the result
  there after commit (requirement from the ТЗ, kept as an extra notification).
- Fine-tuning runs only in the training worker; the model registry lives in
  `ml_model_registry`; classify workers switch models without restart (blue-green
  by model version).
- Operator annotations become vectors in `classifier_vectors` (no DB rebuild).
- Deliverables: `docker-compose.yml`, DB init SQL, file→DB migration script,
  launch scripts `start.cmd` / `start.sh` (+ `stop`), updated docs.

Non-goals (this iteration)
- Building/pushing Docker images or running containers as part of the work
  (the user said containers need not be created; compose file + scripts only).
- Prometheus/Grafana, MinIO/S3 artifact store, Nginx/HAProxy load balancer,
  OCR for scanned PDFs, raising the 5000-char limit.
- Moving backoffice settings from `.env` into the DB.
- Kubernetes, multi-host secret distribution.
- **Multi-host deployment (decided by the user): this iteration is built and
  verified on one machine.** Multi-host is documented only: workers on other hosts
  point `DATABASE_URL`/`RABBITMQ_URL` at the shared services, mount `models/` from a
  shared network folder (NFS/SMB) and carry their own `.env`. No model transfer via
  DB/broker.

Anti-goals (failure even if it "works")
- Any change in retrieval results vs. the current numpy search for the same model
  and DB contents (top-50 order must match; see §9 acceptance).
- Any change to the Directum RX contract: paths, request/response shapes, status
  codes (`202` + `job_id` 32-hex, `429` on full queue, `404` on unknown job).
- Appeal texts / PII ending up in Git (DB data dir, dumps, migration logs).
- API process loading `SentenceTransformer` or touching CPU-heavy work.

## 3. Constraints

- Python 3.11 (prod) / 3.13; Windows dev machines and Linux stands; scripts run from
  repo root (`sys.path.insert(0, 'src')`).
- Prod vectors are `data/vector_db_adapted_v3` (2108 × 768, float32). They must be
  imported **as-is**, not re-embedded.
- Celery on Windows does not support the prefork pool → Windows workers use
  `--pool=solo` (one process = one task; parallelism = several worker processes).
- `setup.ps1` stays ASCII-only; `.cmd` scripts avoid Cyrillic in executable lines.
- Offline stands: no new runtime downloads beyond pip wheels and Docker images.

## 4. Approaches considered

### 4.1 Sync endpoints (`/classify`, `/classify/file`, `/integration/classify-document`)
- **A (chosen): thin API.** Sync calls also enqueue and wait for the result from the
  DB with a timeout. API never loads the model; scales cheaply. `/classifier/search`
  also goes through a worker.
- B: API keeps a full agent for sync calls; only async goes via the queue. Simpler,
  but API stays heavy — contradicts the ТЗ.

### 4.2 How the worker returns the result
- **Chosen: worker writes the DB itself**, in one transaction (appeal card,
  questions, verification row, job status). Webhook only as an optional extra
  notification after commit.
- Rejected: webhook as the primary path (ТЗ proposal) — an extra hop; if the API is
  down at that moment the result must be resent or is lost.

### 4.3 How the API learns that a result is ready
- **Chosen: PostgreSQL `LISTEN/NOTIFY`.** Worker runs `NOTIFY job_done, '<job_id>'`
  inside the result transaction; delivered only on commit, so the API is never told
  about a result that isn't there. A safety re-check of the DB every 5 s covers a
  notification missed during an API restart.
- Rejected: fanout event in RabbitMQ (two-step write+publish, can lose the event);
  Celery result backend (result duplicated outside our tables, polling-based).

## 5. Architecture

```
Directum RX / UI ──HTTP──► API (FastAPI, N replicas, no model)
                            │  1. INSERT appeal_classifications (text, ip, document_id)
                            │  2. INSERT jobs (status=queued)
                            │  3. celery send_task → RabbitMQ  {job_id, appeal_id, document_id}
                            │  4. sync: await JobWaiter(job_id)  ◄── LISTEN job_done
                            ▼
                     RabbitMQ (quorum queues: classify, training)
                            │
             ┌──────────────┴───────────────┐
             ▼                              ▼
  classify worker(s) (Celery)        training worker (Celery, concurrency 1)
  - ClassifierAgent loaded once      - reads verified + historical from DB
  - jobs.status=running              - fine-tunes, evaluates recall@5
  - agent.classify(appeal_id=…)      - writes vectors for new model_version
  - one tx: results + jobs=done      - registers model, flips is_active
    + NOTIFY job_done                - NOTIFY model_changed
  - optional webhook POST
             │                              │
             └──────────────┬───────────────┘
                            ▼
                PostgreSQL 16 + pgvector
```

### 5.1 Code layout (new / changed)

```
src/storage/            NEW — persistence layer
  db.py                 connection pool (psycopg 3 + psycopg_pool), pgvector registration
  schema.sql            canonical DDL (also copied to deploy/db/init/)
  classifier_repo.py    entries, vectors, dense search SQL, annotation vectors
  appeals_repo.py       appeal card / questions / verifications, stats queries
  jobs_repo.py          jobs table, counts, TTL cleanup, NOTIFY
  historical_repo.py    historical_appeals
  models_repo.py        ml_model_registry
  memory.py             in-memory implementations of the same interfaces (tests)
  factory.py            get_storage(): SQL if DATABASE_URL set and STORAGE_BACKEND=postgres, else memory
src/queueing/           NEW
  celery_app.py         Celery app, broker = RABBITMQ_URL, quorum queues, acks_late
  tasks.py              classify_appeal, search_classifier, embed_annotation, finetune
  dispatcher.py         API-side: submit(job) → send_task; LocalDispatcher for tests
  waiter.py             API-side: async LISTEN job_done + per-job asyncio.Event
src/worker_main.py      NEW — worker bootstrap (load agent once, model-change watch, .env watch)
src/api_server.py       CHANGED — no ClassifierAgent; uses storage + dispatcher + waiter
src/integration_api.py  CHANGED — JobQueue → jobs repo + dispatcher
src/appeals_logger.py   CHANGED — same public API, backed by appeals_repo
src/annotations_storage.py CHANGED — backed by classifier_repo (+ missing add_annotation)
src/historical_loader.py   CHANGED — save_to_historical_db()
src/classifier_agent.py CHANGED — metadata from DB, dense search via SQL,
                                   classify(…, appeal_id=None), _log_request → DB
src/finetune_model.py   CHANGED — reads DB; registers in ml_model_registry; builds vectors
src/build_vectordb.py   CHANGED — fills classifier_vectors for a model_version
src/job_queue.py        REMOVED (replaced by jobs repo + Celery)
scripts/db/init_db.py           apply schema (idempotent) outside Docker
scripts/db/migrate_from_files.py  one-shot, idempotent file → DB import
scripts/db/verify_search_parity.py  numpy vs pgvector top-50 comparison
deploy/db/init/001_extensions.sql, 002_schema.sql  (postgres entrypoint)
docker-compose.yml      REWRITTEN — postgres, rabbitmq, migrate, api, worker-classify, worker-training
start.cmd / start.sh / stop.cmd / stop.sh   NEW launch scripts
```

`ClassifierAgent` remains usable directly from CLI tools (`operator_cli.py`,
`test_agent.py`, `classify_manual.py`) — they need a reachable DB but no broker.

## 6. Database schema

All tables from the user's spec, with these **additions (builder's/my calls)**:

| Addition | Why |
|---|---|
| `jobs` table | ТЗ requires job status in DB; spec has no table for it |
| `appeal_classifications.candidates JSONB` | current log stores top-K candidates; keep it |
| `appeal_classifications.status` not added — job state lives in `jobs` | keep card = result |
| `appeal_classifications.llm_provider, llm_model TEXT` | which model produced the result (cheap, useful for audit) |
| `classifier_vectors.model_version INT` | blue-green: vectors per embedding model version |
| `llm_request_log` table | `_log_request` currently writes LLM-call events into `request_log.jsonl` |
| `ml_model_registry.report JSONB` | keeps `training_report.json` content |

DDL (abridged; full text in `src/storage/schema.sql`):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS classifier_entries (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, level SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 5),
  parent_code TEXT REFERENCES classifier_entries(code) DEFERRABLE INITIALLY DEFERRED,
  full_path TEXT NOT NULL, children_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS ml_model_registry (
  version INT PRIMARY KEY, base_model_name TEXT NOT NULL, model_path TEXT NOT NULL,
  recall5_before REAL, recall5_after REAL, is_active BOOLEAN NOT NULL DEFAULT false,
  training_samples_count INT, trained_at TIMESTAMPTZ, report JSONB);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_model ON ml_model_registry(is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS classifier_vectors (
  id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL REFERENCES classifier_entries(code),
  source_type TEXT NOT NULL CHECK (source_type IN ('base','annotation')),
  search_text TEXT NOT NULL, embedding vector(768),          -- NULL until embedded
  model_version INT NOT NULL REFERENCES ml_model_registry(version),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS cv_hnsw ON classifier_vectors USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS cv_code ON classifier_vectors(code, model_version);

CREATE TABLE IF NOT EXISTS appeal_classifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id BIGINT,
  appeal_text TEXT NOT NULL, summary TEXT, applicant_fio TEXT, applicant_email TEXT,
  vid_obrascheniya TEXT, tip_obrascheniya TEXT, is_ustnoe BOOLEAN,
  overall_confidence REAL, needs_verification BOOLEAN, full_fallback_used BOOLEAN,
  candidates JSONB, llm_provider TEXT, llm_model TEXT,
  ip_address INET, elapsed_seconds REAL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS appeal_questions (
  id BIGSERIAL PRIMARY KEY,
  appeal_id UUID NOT NULL REFERENCES appeal_classifications(id) ON DELETE CASCADE,
  ordinal SMALLINT NOT NULL, question_text TEXT, selected_code TEXT, predmet_vedeniya TEXT,
  confidence REAL, reasoning TEXT, alternative_codes JSONB, verification_reasons JSONB,
  UNIQUE (appeal_id, ordinal));

CREATE TABLE IF NOT EXISTS appeal_verifications (
  appeal_id UUID PRIMARY KEY REFERENCES appeal_classifications(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','corrected','rejected')),
  operator_codes JSONB, comment TEXT, verified_at TIMESTAMPTZ, verified_by TEXT);

CREATE TABLE IF NOT EXISTS historical_appeals (
  id BIGSERIAL PRIMARY KEY, source_file TEXT, appeal_text TEXT NOT NULL,
  assigned_code TEXT NOT NULL, specialist TEXT, registration_date TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS hist_dedup ON historical_appeals
  (coalesce(source_file,''), md5(appeal_text), assigned_code, coalesce(registration_date,''));

CREATE TABLE IF NOT EXISTS jobs (
  id CHAR(32) PRIMARY KEY,                       -- uuid4().hex, RX contract
  job_type TEXT NOT NULL CHECK (job_type IN ('classify','search','finetune','embed_annotation')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','done','error')),
  appeal_id UUID REFERENCES appeal_classifications(id) ON DELETE SET NULL,
  document_id BIGINT, webhook_url TEXT, payload JSONB, result JSONB, error TEXT,
  attempts INT NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(job_type, status, created_at);

CREATE TABLE IF NOT EXISTS llm_request_log (
  id BIGSERIAL PRIMARY KEY, appeal_id UUID, success BOOLEAN, attempt INT,
  elapsed_seconds REAL, confidence REAL, candidates_count INT, error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
```

A row in `appeal_verifications` with `status='pending'` is created in the same
transaction as the classification result (mirrors the current log behaviour).

`date` from historical files is kept as text (`registration_date TEXT`) because the
source column is free-form; normalising it is out of scope.

## 7. Key behaviours

### 7.1 Classification request (sync and async share the path)
1. API validates, resolves text (inline text, file extraction via `text_extractor`,
   or RX OData `document_id` — these stay in the API so the current immediate
   `400/404/413/422` errors are unchanged; my call).
2. One transaction: `INSERT appeal_classifications (appeal_text, document_id,
   ip_address)`; check `count(queued classify jobs) < JOB_MAX_QUEUED` else `429`;
   `INSERT jobs(status='queued', payload={llm_provider, llm_model, endpoint, …})`.
3. After commit: `send_task('classify_appeal', {job_id, appeal_id, document_id},
   queue='classify')`. If publishing fails → job marked `error`, API returns `503`.
4. Async endpoint returns `202 {job_id, status, queue_position, poll_url}`
   (`queue_position` = rank among queued classify jobs by `created_at`).
   Sync endpoints `await waiter.wait(job_id, SYNC_WAIT_TIMEOUT_SEC)`
   (`.env` setting, default 1800 s — see §10.1), then read the result from
   the DB and build exactly the same response models as today. On timeout → `504`
   with `job_id` in the detail so the caller can poll `/integration/jobs/{id}`.
5. `elapsed_seconds` written by the worker = wall time of `agent.classify`; the
   API-side request duration (old `request_log`) is not stored separately (my call:
   the spec puts `elapsed_seconds` on the card).

### 7.2 Worker task `classify_appeal`
- Celery settings: `task_acks_late=True`, `task_reject_on_worker_lost=True`,
  `worker_prefetch_multiplier=1`, queues declared `x-queue-type=quorum`,
  `task_ignore_result=True`, `broker_connection_retry_on_startup=True`.
- Idempotency: `UPDATE jobs SET status='running', started_at=now(),
  attempts=attempts+1 WHERE id=$1 AND status IN ('queued','running') RETURNING …`;
  if the job is already `done`/`error` → ack and return (redelivery after crash).
- Runs `agent.classify(text, llm_provider, llm_model, appeal_id=…)`. The agent's
  logger writes card fields, questions (delete+insert by `appeal_id` to be
  re-run-safe), pending verification, `jobs.result` (the endpoint-specific response
  dict produced by a shared builder function), `jobs.status='done'`, and
  `NOTIFY job_done` — one transaction.
- On exception: if `attempts < JOB_MAX_ATTEMPTS` (default 1 extra retry for
  infrastructure errors only — DB/broker), re-raise for redelivery; LLM errors are
  already retried inside the agent → mark `error` with the message, `NOTIFY`.
- After commit, if `webhook_url` → POST result JSON (timeout 10 s, 3 attempts,
  failure is logged, never changes job status). Proxy policy for webhooks reuses
  `proxy_policy.py` (internal hosts bypass proxy).

### 7.3 Dense search in SQL
```sql
SET LOCAL hnsw.ef_search = 400;        -- only matters when the planner uses HNSW
SELECT code, max(1 - (embedding <=> $1)) AS similarity
FROM classifier_vectors
WHERE model_version = $2 AND embedding IS NOT NULL
  AND (source_type = 'base' OR $3)      -- $3 = ENABLE_ANNOTATION_VECTORS
GROUP BY code ORDER BY similarity DESC LIMIT $4;
```
The `GROUP BY` forces an exact scan (2108 rows → a few ms), which guarantees parity
with numpy; the HNSW index exists for growth (my call, agreed in session).
The worker keeps classifier entries (code, name, level, parent, full_path,
base search_text) in memory, loaded from the DB at start, for the existing lexical
search, L2/L3 routing, whitelist and `code_index` lookups — these iterate the whole
classifier and are not worth moving to SQL.

### 7.4 Annotations
- `/verify action=correct` with a comment: API inserts `classifier_vectors`
  (`source_type='annotation'`, `embedding NULL`, `model_version = active`) and
  enqueues `embed_annotation`; the worker embeds it with the `passage:` prefix rule.
  This fixes today's silent loss (`add_annotation` doesn't exist).
- Search uses annotation vectors only when `ENABLE_ANNOTATION_VECTORS=true`
  (default `false`, so accuracy doesn't move without measurement).
- `annotations_storage` public functions keep working (backoffice / fine-tuning
  use `build_search_text`, `list_annotated_codes`, `get_annotations`).
- `/verify` response field `annotation_count` = number of annotation rows for the
  first operator code.

### 7.5 Model registry and fine-tuning
- Migration seeds `ml_model_registry` version 0 = current `EMBEDDING_MODEL`
  (`is_active=true`) with the imported prod vectors; existing
  `models/e5-finetuned-v*/training_report.json` are registered inactive.
- `POST /api/finetune` → `jobs(job_type='finetune')` → queue `training` → `409` if
  one is already queued/running. `GET /api/finetune/status` maps the latest
  finetune job to the existing response shape (`status`, `message`, `model_path`,
  `log_tail` …; `idle` when none).
- Training worker: reads verified appeals + historical from DB, trains (same
  algorithm as now), evaluates recall@5, saves to `models/e5-finetuned-vN/`,
  embeds all `base` + `annotation` rows of the active version under version N,
  then in one transaction sets N active (old inactive) and `NOTIFY model_changed`.
  **Decided by the user:** a new version is activated automatically only if
  recall@5 improved; otherwise it is registered inactive (both for API- and
  CLI-triggered runs). Backoffice gets a model-versions list with an
  "Activate version N" action (`POST /api/models/{version}/activate`, Basic Auth)
  for manual activation and rollback; `GET /api/models` lists the registry.
- Classify workers check the active version before each task (one indexed query)
  and reload the embedder when it differs; no restart needed.
- Old version vectors are kept (rollback = flip `is_active`).

### 7.6 Settings hot-apply
Backoffice still writes `.env` (non-goal to move). Workers compare `.env` mtime
before each task and, if changed, call the existing `settings_store.apply_runtime`
+ `refresh_agent_llm` in the worker process. Works when API and workers share the
file (same host or the same bind mount in compose). Multi-host: documented
limitation.

### 7.7 `/health`
`{"status","agent_ready","classifier_entries"}` kept; `agent_ready` = at least one
classify worker answered a Celery `ping` within 1 s (cached 10 s);
`classifier_entries` = `count(*)` from `classifier_entries`. Adds `db`, `broker`
booleans. `/api/backoffice/stats` and `/stats` are reimplemented as SQL aggregates
with the same response keys; `ip_stats` from `appeal_classifications.ip_address`.

### 7.8 Job TTL
On each submit (as today) the API deletes `jobs` rows with `status in (done,error)`
older than `JOB_TTL_HOURS`. Appeal cards are never deleted by TTL.

## 8. Migration and deployment

### 8.1 `scripts/db/migrate_from_files.py` (idempotent; safe to re-run)
1. `classifier_entries` ← `data/classifier_flat.json` (upsert).
2. `ml_model_registry` v0 ← `EMBEDDING_MODEL` from config; `classifier_vectors`
   (`base`, v0) ← `VECTOR_DB_DIR/embeddings.npy` + `metadata.json` (row i ↔
   vector i, `search_text` from metadata). Skipped if v0 vectors exist.
   Fails loudly if metadata codes don't match classifier entries.
3. Annotations ← `classifier_annotations.json`: one `annotation` row per
   `correct_name` / `category` / `note` field, `embedding NULL` → embedded by the
   next worker start (`embed_pending_annotations` on boot).
4. `appeals_log.jsonl` → cards (id = log id, `created_at` = timestamp),
   questions (ordinal = index), verifications. `ON CONFLICT DO NOTHING`.
5. `request_log.jsonl`: IP records with a matching `log_id` → set
   `ip_address`, `elapsed_seconds` on the card; LLM-event records → `llm_request_log`.
6. `historical_verified.jsonl` → `historical_appeals` (dedup index).
7. `models/e5-finetuned-v*/training_report.json` → registry (inactive).
8. Prints a reconciliation table (file count vs. DB count per source).
Source files are left untouched; nothing is deleted.

### 8.2 docker-compose.yml
Services: `postgres` (`pgvector/pgvector:pg16`, volume `pgdata`, init SQL mounted
into `/docker-entrypoint-initdb.d`, healthcheck), `rabbitmq`
(`rabbitmq:3.13-management`, healthcheck), `migrate` (app image, runs
`init_db.py && migrate_from_files.py`, `restart: "no"`), `api`
(`uvicorn`, depends on migrate completed), `worker-classify`
(`celery … -Q classify -c ${CLASSIFY_CONCURRENCY:-2}`, scalable with
`--scale`), `worker-training` (`-Q training -c 1`, optional `profiles: [training]`,
GPU block commented). All app services reuse the existing `Dockerfile`
(`build: .`) and the `.env`, `models/`, HF cache mounts. `DATABASE_URL` and
`RABBITMQ_URL` default to the compose service names.

### 8.3 Launch scripts
`start.sh` / `start.cmd` modes:
- `docker` (default): `docker compose up -d` (+ `--profile training` if asked),
  then wait for `/health`.
- `local`: start only `postgres` + `rabbitmq` via compose, run init + migrate from
  the venv, then start API and workers from the venv (Linux: background processes
  with pid files in `run/`; Windows: separate `start` windows, workers with
  `--pool=solo`, `CLASSIFY_WORKERS` processes).
- `migrate`: run init + migration only.
`stop.sh` / `stop.cmd`: stop local processes / `docker compose down`
(never `-v` — the DB volume holds PII and must survive).
Existing `launch.*` / `install.*` get the new deps and a hint pointing to `start.*`.

### 8.4 New configuration (`.env.example`)
`STORAGE_BACKEND=postgres`, `DATABASE_URL`, `DB_POOL_MAX`, `RABBITMQ_URL`,
`CLASSIFY_CONCURRENCY`, `CLASSIFY_WORKERS` (Windows local), `SYNC_WAIT_TIMEOUT_SEC`,
`JOB_MAX_ATTEMPTS`, `ENABLE_ANNOTATION_VECTORS=false`, `POSTGRES_*`, `RABBITMQ_*`.
New deps: `psycopg[binary]>=3.1`, `psycopg_pool`, `pgvector`, `celery>=5.4`.

## 9. Testing and acceptance

- Unit tests run **without** Postgres/RabbitMQ: `STORAGE_BACKEND=memory` +
  `LocalDispatcher` (runs the task in-process and fires the waiter). Existing tests
  for API, integration (sync/async), job queue semantics, historical loader are
  ported to the new layer; `test_job_queue.py` is rewritten against `jobs_repo`.
- DB tests (`tests/db/`, marked `@pytest.mark.db`, skipped when `DATABASE_URL`
  unreachable): schema apply, repos, NOTIFY wake-up, migration idempotency on
  fixture files.
- Acceptance:
  1. `verify_search_parity.py`: for the 56 `ii25_test` queries + 20 random appeals,
     pgvector top-50 codes and order == numpy top-50 (similarity diff < 1e-5).
  2. Migration reconciliation: counts match for every source.
  3. Full pytest suite: no new failures vs. the pre-change baseline (known
     failures listed in AGENTS.md).
  4. RX contract: `tests/test_integration_*.py` pass unchanged in assertions.
  5. Manual (documented, needs Docker): `start.sh docker` → `/health` ok →
     async + sync classification → kill a worker mid-task → job completes on
     another worker.

## 10. Decisions after the builder check (my calls unless marked)

1. **Sync calls get their own queue.** Sync endpoints publish to `classify_sync`,
   async to `classify`; every classify worker consumes `classify_sync,classify`
   (sync listed first). Sync jobs are **not** counted toward `JOB_MAX_QUEUED`, so
   sync endpoints never return `429`. New unavoidable codes: `503` when the broker
   or DB is unreachable, `504` when `SYNC_WAIT_TIMEOUT_SEC` expires
   (**user decision:** a `.env` setting, documented in `.env.example`, default
   1800 s, editable in the backoffice settings form and applied without restart) — the job keeps running and its id is in the error detail. The anti-goal
   "no change to status codes" is narrowed to: existing codes keep their meaning;
   `504` is the only new code on sync endpoints.
2. **Vector spaces.** Version 0 = imported prod vectors (adapter blend `v3_blend`,
   recorded in a new `ml_model_registry.adapter JSONB` column from
   `adapter_v3.json`). Annotation rows under v0 are embedded with the plain model
   (same space as queries, `ENABLE_EMBEDDING_ADAPTER=false`) — acceptable because
   they are behind `ENABLE_ANNOTATION_VECTORS=false`. New versions are built exactly
   like today's `build_vectordb.py`: base text via `build_search_text` (annotations
   folded in, `correct_name` replaces name), plain fine-tuned model, no adapter;
   plus separate annotation rows. Fine-tuning starts from the **active** model's
   `model_path` (today: `EMBEDDING_MODEL`).
3. **Webhook contract.** Optional `webhook_url` field on
   `/integration/classify-document-async` only. Body = `JobStatusResponse` JSON
   (same as `GET /integration/jobs/{id}`), sent on `done` and `error`. Allowed only
   if the URL host is in `WEBHOOK_ALLOWED_HOSTS` (comma list; empty = webhooks
   disabled, request with `webhook_url` → `400`) — SSRF guard. If `WEBHOOK_SECRET`
   is set, header `X-Signature: sha256=<HMAC of body>`.
4. **Cutover.** Migration also imports `data/jobs/*.json` (done/error jobs keep
   their ids; queued/running → `error` "service migrated, resubmit") so RX polling
   doesn't hit `404`. `src/auto_finetune.py` is ported to enqueue a `finetune` job
   (threshold check against the DB); it no longer edits `.env` or runs
   `build_vectordb.py`.
5. **Classifier roots.** `parent_code '0000.0000.0000.0000'` (L1) is stored as NULL.

## 11. Left to the builder

- Exact module boundaries inside `src/storage/` and `src/queueing/`.
- Connection pool sizes, Celery naming, log formats.
- Whether `ClassifyResponse` building is shared via a new `responses.py` module.
- Windows-specific details of `start.cmd` (window titles, pid tracking).

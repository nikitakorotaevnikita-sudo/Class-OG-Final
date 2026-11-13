# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI agent that classifies Russian citizen appeals (обращения граждан) according to Federal Law 59-FZ and the Общероссийский классификатор вопросов обращений граждан (v4, 2108 entries). Intended for integration with Directum RX 25.3.

## Commands

All commands run from the **project root**, not from `src/`.

```bash
# One-shot install: venv, deps, .env dialog, then free host ports -> .env
# (scripts/setup_infra_env.py), docker compose up postgres rabbitmq, start.* migrate.
# No Docker running -> install still finishes, run start.* migrate later.
install.bat                     # Linux: ./install.sh   (offline: install_offline.*)
launch.bat server               # = start.cmd local;     Linux: ./launch.sh server

# Whole stack in Docker (postgres+pgvector, rabbitmq, migrate, api, classify workers)
./start.sh                      # Windows: start.cmd      (+ --training for the fine-tuning worker)
./stop.sh                       # Windows: stop.cmd       (docker compose down, never -v)

# Local: postgres + rabbitmq in Docker, API and Celery workers from venv
./start.sh local [--port N]     # Windows: start.cmd local (one window per process)
./stop.sh local                 # Windows: stop.cmd local

# Schema + one-shot, idempotent import of file storage into PostgreSQL
python scripts/db/init_db.py
python scripts/db/migrate_from_files.py         # exit 1 on reconciliation mismatch
python scripts/db/verify_search_parity.py --queries data/ii25_test.jsonl --extra 20

# Tests (db-marked tests use TEST_DATABASE_URL, skip if PostgreSQL is unreachable)
TEST_DATABASE_URL=postgresql://classifier:classifier@localhost:5433/classifier \
    python -m pytest tests/ -q --ignore=tests/e2e
python -m pytest tests/test_search.py::test_flat_entries_count

# Run 4 built-in classification test appeals (needs a reachable DB, no broker)
python src/cli/test_agent.py

# Interactive operator mode (verification + fine-tuning trigger)
python src/cli/operator_cli.py

# Queue a fine-tuning job (threshold applies to every source unless --force)
python src/training/auto_finetune.py [--force]

# Processes by hand (PYTHONPATH=src: modules are imported top-level)
uvicorn api.api_server:app --app-dir src --host 0.0.0.0 --port 8010
PYTHONPATH=src celery -A queueing.celery_app worker -Q classify_sync,classify -c 2 --loglevel=INFO
PYTHONPATH=src celery -A queueing.celery_app worker -Q training -c 1      # Windows: --pool=solo
```

**Python version: 3.11 (prod) or 3.13.** Prod runs 3.11; 3.13.2 is verified — full test
suite gives the same result as 3.11 (126 passed, the same 5 pre-existing failures).
3.12 is not tested. 3.14 is still incompatible with `sentence-transformers`.

On 3.13 `numpy` must be 2.x (no cp313 wheels exist for numpy 1.26) — `requirements.txt`
picks the major automatically via environment markers, so 3.11 keeps the verified 1.x.

Verify compatibility without touching the host interpreter:

```bash
docker build -f Dockerfile.py313check -t og-py313check .
docker run --rm -v "$PWD/tests:/app/tests:ro" og-py313check
```

The prod image takes the interpreter from a build arg (`3.11` by default):

```bash
docker build --build-arg PYTHON_VERSION=3.13.2 -t classifier-py313 .
```

## Architecture

### Process layout

```
Directum RX / UI ─HTTP─► API (FastAPI, no model; never imports classifier_agent/torch)
                           │ appeal card + jobs row in PostgreSQL → send_task → RabbitMQ
                           │ sync endpoints wait on LISTEN job_done (SYNC_WAIT_TIMEOUT_SEC → 504)
                           ▼
                RabbitMQ quorum queues: classify_sync, classify, training
                           │
          ┌────────────────┴────────────────┐
          ▼                                 ▼
 classify worker (Celery)            training worker (Celery, concurrency 1)
 ClassifierAgent loaded once,        fine-tunes, builds vectors for the new
 results + jobs=done + NOTIFY        model version, registers it, NOTIFY
 job_done, optional webhook          model_changed
          └────────────────┬────────────────┘
                           ▼
                PostgreSQL 16 + pgvector
```

Compose services: `postgres`, `rabbitmq`, `migrate` (one-shot `init_db.py && migrate_from_files.py`),
`api`, `worker-classify`, `worker-training` (profile `training`). Workers hot-reload `.env`
(the back office writes it), so API and workers must share one `.env` file.

### Two-stage classification pipeline (inside the classify worker)

```
Appeal text
    │
    ▼
[Stage 1] Dense search in SQL (pgvector)
    - multilingual-e5-base (active model version) encodes the query, 768-dim
    - cosine top-K over classifier_vectors of the active model_version
      (parity with the former numpy search is a hard requirement)
    │
    ▼
[Stage 2] LLM reasoning (OpenAI-compatible endpoint)
    - Provider from LLM_PROVIDER: ario (Qwen, основной), custom (модель
      Заказчика через vLLM), groq, gemini, ollama
    - The model receives appeal text + candidates per question
    - Identifies: appeal type (59-FZ), all questions in appeal, best code per question
    - Returns strict JSON with confidence scores and reasoning
    │
    ▼
ClassificationResult → appeal card / questions in PostgreSQL
```

### Key files

`src/` is the import root; no module lives directly in it. Packages by role (every
`__init__.py` is empty, so importing one module never pulls its neighbours):

| Package | Modules |
|---|---|
| `src/api/` | `api_server.py`, `integration_api.py`, `job_api.py`, `responses.py`, `static/` (UI) |
| `src/classifier/` | `classifier_agent.py`, `hierarchy.py`, `reranker.py`, `section_router.py`, `llm_schemas.py`, `llm_check.py`, `fio_extractor.py`, `email_extractor.py` |
| `src/queueing/` | `celery_app.py`, `dispatcher.py`, `tasks.py`, `waiter.py`, `warmup.py`, `webhook.py` |
| `src/worker/` | `worker_main.py` |
| `src/storage/` | DB layer + `appeals_logger.py`, `annotations_storage.py` |
| `src/training/` | `finetune_model.py`, `auto_finetune.py`, `build_vectordb.py`, `historical_loader.py`, `auto_import_historical.py` |
| `src/integrations/` | `rx_client.py`, `text_extractor.py`, `proxy_policy.py` |
| `src/core/` | `config.py`, `env_bootstrap.py`, `settings_store.py`, `service_urls.py` |
| `src/cli/` | `operator_cli.py`, `classify_manual.py`, `test_agent.py` |

Imports are package-qualified (`from core import config`, `from classifier.classifier_agent import ClassifierAgent`);
`tests/test_src_layout.py` enforces the layout.

- **`src/core/config.py`** — all constants from `.env`. Single source of truth for paths, model names, `DATABASE_URL`, `RABBITMQ_URL` (defaults built by `src/core/service_urls.py` from `POSTGRES_*` / `RABBITMQ_*` + ports).
- **`scripts/setup_infra_env.py`** — installer helper: picks free host ports for PostgreSQL/RabbitMQ, writes them and the URLs to `.env` (keeps BOM/line endings, never prints passwords). Used by `scripts/setup_infra.cmd` and `setup_infra` in `scripts/install_common.sh`.
- **`src/storage/`** — persistence layer: `db.py` (psycopg pool, pgvector), `schema.sql` (canonical DDL, byte-identical copy in `deploy/db/init/002_schema.sql`), repos for classifier/vectors, appeals, jobs, historical, models; `memory.py` in-memory twins for tests; `get_storage()` picks by `STORAGE_BACKEND`.
- **`src/queueing/`** — `celery_app.py` (broker, quorum queues, acks_late), `tasks.py` (classify, search, annotation embedding, finetune), `dispatcher.py` + `waiter.py` (API side: send_task, LISTEN job_done), `webhook.py`.
- **`src/worker/worker_main.py`** — worker bootstrap: one `ClassifierAgent` per process, model-change and `.env` hot reload.
- **`src/classifier/classifier_agent.py`** — `ClassifierAgent.classify(text, appeal_id=None) → ClassificationResult`; metadata and dense search from the DB.
- **`src/storage/appeals_logger.py`** — same public API as before, backed by the appeals repo (cards, questions, verifications `pending/confirmed/corrected/rejected`).
- **`src/api/api_server.py`** / **`src/api/integration_api.py`** — thin FastAPI over storage + dispatcher + waiter. `/health` reports DB, broker and worker readiness. RX contract unchanged.
- **`src/training/finetune_model.py`** — reads verified + historical data from the DB, fine-tunes with `MultipleNegativesRankingLoss`, saves `models/e5-finetuned-vN/`, builds vectors for version N, registers it in `ml_model_registry` (activates only if recall@5 improved).
- **`src/training/build_vectordb.py`** — fills `classifier_vectors` for a model version (called by fine-tuning).
- **`scripts/db/`** — `init_db.py`, `migrate_from_files.py`, `verify_search_parity.py`.
- **`docker-compose.yml`, `start.*`, `stop.*`, `deploy/db/init/`** — deployment.

### Vector storage

Vectors live in PostgreSQL table `classifier_vectors` (pgvector, dimension fixed at 768), one set
per `model_version`; the active version comes from `ml_model_registry`. Version 0 is the former
prod file DB `VECTOR_DB_DIR` (`data/vector_db_adapted_v3`: `embeddings.npy` + `metadata.json`),
imported as-is by `migrate_from_files.py` — never re-embedded. A new model version gets its own
vectors from fine-tuning; no manual rebuild of files is needed.

### Classifier data structure

`data/classifier_flat.json` — 2108 entries, 4-level hierarchy with codes like `XXXX.XXXX.XXXX.XXXX`. Level 4 entries are the classification targets. `search_text` field is what gets embedded (combines name + path context). Imported into `classifier_entries`.

### Fine-tuning loop

Operator verifications (DB) accumulate training signal:
- `confirmed` → positive pair: `(appeal, agent_code_name)`
- `corrected` → positive: `(appeal, operator_code_name)` + negative: `(appeal, agent_code_name)`

Trigger threshold: `FINETUNE_THRESHOLD` (default 50) — applies to every source (verified,
historical, combined) unless `force`. Jobs go to the `training` queue; the training worker
registers the new version and classify workers switch to it without restart (`NOTIFY model_changed`).

## Configuration (`.env`)

```
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
EMBEDDING_MODEL=intfloat/multilingual-e5-base   # or path to fine-tuned model
TOP_K_CANDIDATES=10
MIN_CONFIDENCE=0.65      # below this → needs_verification=True
FINETUNE_THRESHOLD=50    # verified entries needed to trigger fine-tuning
STORAGE_BACKEND=postgres # memory in tests
POSTGRES_PORT=           # host ports; empty = 5432/5672/15672; the installer picks free
RABBITMQ_PORT=           #   ones (fallbacks 5434.., 5673.., 15673..) and writes them here
RABBITMQ_MGMT_PORT=
DATABASE_URL=            # empty -> built from POSTGRES_USER/PASSWORD/DB + POSTGRES_PORT
RABBITMQ_URL=            #   (src/core/service_urls.py, credentials URL-encoded)
CLASSIFY_CONCURRENCY=2   # per worker; CLASSIFY_WORKERS=2 processes for start.cmd local
SYNC_WAIT_TIMEOUT_SEC=1800
WEBHOOK_ALLOWED_HOSTS=   # exact hostnames, comma-separated; empty = webhooks off
```

In compose, `DATABASE_URL`/`RABBITMQ_URL` are overridden to the service hostnames
(`postgres`, `rabbitmq`). Full list with comments: `.env.example`.

## Important constraints

- Scripts import from `src/` using `sys.path.insert(0, 'src')` — always run from project root.
- `numpy` major is chosen by environment marker: `<2.0` below Python 3.12 (the version prod
  is verified on), `>=2.1` from 3.12 up (numpy 1.26 has no cp313 wheels). Project code uses
  no aliases removed in numpy 2.0, so the bump needs no source changes.
- Every process needs `src` on `PYTHONPATH` (`src` is the import root: `from core import config`,
  `queueing.celery_app`, `api.api_server:app`); the image sets `PYTHONPATH=/app/src`.
- `data/vector_db/` and `models/` are excluded from Git (see `.gitignore`). The former
  production file DB `data/vector_db_adapted_v3` **is** in Git: it is the source of model
  version 0 for `migrate_from_files.py` (`VECTOR_DB_DIR` points at it).
- Personal data (appeal texts, 59-FZ) never goes to Git: the `pgdata` volume, `run/`
  (local process logs), migration output, the legacy `data/appeals_log.jsonl` /
  `data/request_log.jsonl` (git-ignored; data now lives in PostgreSQL). `stop.*` never runs `docker compose down -v`.
- The API process must never import `classifier_agent`, `sentence_transformers` or `torch`.
- `.cmd` files: executable lines ASCII-only, CRLF. `.sh` files: LF, `set -euo pipefail`.
- `setup.ps1` is ASCII-only — Cyrillic strings cause PowerShell parse errors on Windows with CP1251 encoding.

## GitHub

- Repository: https://github.com/nikitakorotaevnikita-sudo/Class-OG-Final
- Main branch: `main` — основной проект, наиболее готовый к пилотированию;
  стенд тянет обновления отсюда
- `research/quality-followups-68` — эксперименты; полезное переносится в `main`
- `prod-pilot` — «на всякий случай», иногда принимает изменения со стенда
- Branch model and merge rules: `README.md` on `main`

## Session Start Protocol

At the **beginning of each session**, Claude Code MUST:

1. **Read Hub instructions** — `C:/Users/Korotaev_NO/Desktop/Obsidian vault/AI Agent Hub/instructions/core.md`
2. **Read Hub session-start** — `C:/Users/Korotaev_NO/Desktop/Obsidian vault/AI Agent Hub/instructions/session-start.md`
3. **Read project Obsidian notes** — check for updates in:
   - `C:/Users/Korotaev_NO/Desktop/Obsidian vault/Прототипы/ИИ Агенты/agent_appeals/`
   - Look for files modified today and recent planning files
4. **Read relevant tickets** — check `Тикеты/TICKET-*.md` for current state
5. **Give session summary** — before starting work, output:
   ```
   ## Session Summary
   - Last session: [date]
   - What was done: [1-2 sentences]
   - What's next: [next step]
   - Open issues: [any blockers]
   ```

## Hub Instructions

This project follows the AI Agent Hub standards:
- **Language:** Russian for user communication, English for technical docs
- **Stack:** Python 3.11 / FastAPI, Vanilla JS/HTML/CSS, pytest, Docker
- **Principle:** Agent proposes, human approves. юридически значимые действия — через human-in-the-loop.
- **Docs:** General rules in Hub, local info only in project README


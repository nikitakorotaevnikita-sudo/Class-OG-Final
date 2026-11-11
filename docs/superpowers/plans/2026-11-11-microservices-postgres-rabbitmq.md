# PostgreSQL + pgvector, RabbitMQ, isolated workers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all file-backed state with PostgreSQL+pgvector, move classification and fine-tuning out of the FastAPI process into Celery workers fed by RabbitMQ, keep every existing endpoint and the Directum RX contract.

**Architecture:** Thin stateless API (`src/api_server.py`, `src/integration_api.py`) writes the appeal + a `jobs` row, publishes `{job_id, appeal_id, document_id}` to RabbitMQ and (for sync endpoints) awaits a PostgreSQL `NOTIFY job_done`. Celery `classify` workers load `ClassifierAgent` once, write results in one transaction and `NOTIFY`. A `training` worker fine-tunes, writes vectors for a new `model_version`, and activates it only if recall@5 improved.

**Tech Stack:** Python 3.11/3.13, FastAPI, psycopg 3 + psycopg_pool, pgvector (Python + PG16 extension), Celery 5.4+, RabbitMQ 3.13 (quorum queues), Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-11-11-microservices-postgres-rabbitmq-design.md` — read it before any task; section numbers (§) below refer to it.

## Global Constraints

- Run everything from repo root; interpreter `venv\Scripts\python.exe` (Windows) / `venv/bin/python`.
- New deps (exact floors): `psycopg[binary]>=3.1`, `psycopg_pool>=3.2`, `pgvector>=0.2.5`, `celery>=5.4`.
- The API process must never import `classifier_agent`, `sentence_transformers` or `torch` (test-enforced in Task 9).
- RX contract unchanged: paths, request/response models, `202` + 32-hex `job_id`, `429` on full async queue, `404` unknown job. Only new code on sync endpoints: `504` (and `503` when DB/broker unreachable).
- Retrieval parity: pgvector top-50 == numpy top-50 for the same vectors (|Δsim| < 1e-5).
- Vector dimension fixed at 768; prod vectors imported as-is from `VECTOR_DB_DIR`, never re-embedded.
- New `.env` keys and defaults: `STORAGE_BACKEND=postgres` (`memory` in tests), `DATABASE_URL=postgresql://classifier:classifier@localhost:5432/classifier`, `DB_POOL_MAX=10`, `RABBITMQ_URL=amqp://classifier:classifier@localhost:5672//`, `CLASSIFY_CONCURRENCY=2`, `CLASSIFY_WORKERS=2`, `SYNC_WAIT_TIMEOUT_SEC=1800`, `JOB_MAX_ATTEMPTS=2`, `ENABLE_ANNOTATION_VECTORS=false`, `WEBHOOK_ALLOWED_HOSTS=` (empty = disabled), `WEBHOOK_SECRET=`, `POSTGRES_USER/PASSWORD/DB=classifier`, `RABBITMQ_DEFAULT_USER/PASS=classifier`.
- Queue names: `classify_sync`, `classify`, `training`; all declared `x-queue-type: quorum`. Celery: `task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`, `task_ignore_result=True`, `broker_connection_retry_on_startup=True`.
- NOTIFY channels: `job_done` (payload = job id), `model_changed` (payload = version).
- `setup.ps1` stays ASCII-only; executable lines of `.cmd` files ASCII-only.
- Never commit PII: `pgdata` volume, `run/`, migration logs stay out of Git. `stop.*` never runs `docker compose down -v`.
- Known pre-existing test failures (AGENTS.md) are not regressions; Task 0 records the exact baseline.

## Review Focus

1. **Worker crash mid-task / message redelivery** — the job must finish once, with no duplicate `appeal_questions` rows and no second webhook for an already-`done` job. Test: Task 7 `test_redelivered_done_job_is_skipped`, `test_rerun_replaces_questions`.
2. **NOTIFY missed (API restarted, listener connection dropped)** — a sync caller must still get its result. Test: Task 8 `test_waiter_falls_back_to_db_recheck`.
3. **Re-running the migration** on an already-migrated DB — counts must not change. Test: Task 3 `test_migration_is_idempotent`.
4. **Webhook URL pointing at an internal/unknown host** — must be refused with 400 before enqueue. Test: Task 10 `test_webhook_host_not_allowed_returns_400`.
5. **Classify worker started before any model is active / with NULL-embedding annotation rows** — search must ignore NULL embeddings and the worker must fail fast with a clear message if no active model. Test: Task 2a `test_search_ignores_null_embeddings`, Task 4 `test_agent_requires_active_model`.

---

## File Structure

```
src/storage/__init__.py          get_storage() factory export
src/storage/schema.sql           canonical DDL (§6 + §10.2 adapter column)
src/storage/db.py                pool, pgvector registration, transaction helper
src/storage/models.py            dataclasses shared by both backends
src/storage/base.py              Protocols: ClassifierStore, AppealStore, JobStore, HistoricalStore, ModelStore
src/storage/memory.py            in-memory implementation of all Protocols
src/storage/pg.py                PostgreSQL implementation of all Protocols
src/storage/factory.py           get_storage() -> Storage (SQL or memory by STORAGE_BACKEND)
src/responses.py                 pydantic response models + builders (moved from api_server / integration_api)
src/queueing/celery_app.py       Celery app + queue declarations
src/queueing/tasks.py            classify_appeal, search_classifier, embed_annotation, finetune
src/queueing/dispatcher.py       CeleryDispatcher, LocalDispatcher
src/queueing/waiter.py           JobWaiter (LISTEN job_done)
src/queueing/webhook.py          URL allowlist check, HMAC signing, POST with retries
src/worker_main.py               worker bootstrap: agent singleton, model-change + .env watch
scripts/db/init_db.py            apply schema.sql (idempotent)
scripts/db/migrate_from_files.py file → DB import (§8.1, §10.4)
scripts/db/verify_search_parity.py numpy vs pgvector comparison
deploy/db/init/001_extensions.sql
deploy/db/init/002_schema.sql    byte-identical copy of src/storage/schema.sql (test-enforced)
docker-compose.yml, start.cmd, start.sh, stop.cmd, stop.sh
tests/storage/conftest.py        `storage` fixture parametrized [memory, pg]; pg skipped if DB unreachable
tests/storage/test_*.py, tests/queueing/test_*.py, tests/scripts/test_migrate.py
```
Removed: `src/job_queue.py`, `tests/test_job_queue.py` (replaced by Task 2c tests).

---

### Task 0: Baseline and dependencies

**Files:** Modify `requirements.txt`; Create `docs/superpowers/plans/baseline-2026-11-11.txt`

- [ ] **Step 1:** Run `venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/e2e -p no:cacheprovider > docs/superpowers/plans/baseline-2026-11-11.txt 2>&1`; record the failing test ids at the top of the file.
- [ ] **Step 2:** Add the four deps from Global Constraints to `requirements.txt`; `venv\Scripts\python.exe -m pip install -r requirements.txt`. Expected: `python -c "import celery, psycopg, psycopg_pool, pgvector"` exits 0.
- [ ] **Step 3:** Commit `chore(deps): add psycopg, pgvector, celery; record test baseline`.

### Task 1: Schema, config, DB bootstrap

**Files:** Create `src/storage/schema.sql`, `src/storage/db.py`, `deploy/db/init/001_extensions.sql`, `deploy/db/init/002_schema.sql`, `scripts/db/init_db.py`, `tests/storage/conftest.py`, `tests/storage/test_schema.py`; Modify `src/config.py`, `.env.example`, `pytest.ini` or `pyproject` marker registration (create `pytest.ini` if absent with `markers = db: needs PostgreSQL`).

**Interfaces — Produces:**
- `config.STORAGE_BACKEND, DATABASE_URL, DB_POOL_MAX, RABBITMQ_URL, CLASSIFY_CONCURRENCY, SYNC_WAIT_TIMEOUT_SEC, JOB_MAX_ATTEMPTS, ENABLE_ANNOTATION_VECTORS, WEBHOOK_ALLOWED_HOSTS: list[str], WEBHOOK_SECRET`
- `db.get_pool() -> psycopg_pool.ConnectionPool` (lazy singleton, registers pgvector on each connection via `configure=`), `db.transaction() -> ContextManager[psycopg.Connection]`, `db.apply_schema(conn) -> None`, `db.close_pool()`.
- Fixture `pg_conn` (tests/storage/conftest.py): connects to `TEST_DATABASE_URL` (default = `DATABASE_URL`), drops/creates schema `test_<random>`, sets `search_path`, applies schema; `pytest.skip` if unreachable.

- [ ] **Step 1: failing tests** in `tests/storage/test_schema.py`:
  - `test_deploy_schema_matches_canonical`: bytes of `deploy/db/init/002_schema.sql` == `src/storage/schema.sql`.
  - `test_schema_declares_all_tables`: for each of `classifier_entries, classifier_vectors, appeal_classifications, appeal_questions, appeal_verifications, historical_appeals, ml_model_registry, jobs, llm_request_log` assert `CREATE TABLE IF NOT EXISTS <name>` in schema text.
  - `@pytest.mark.db test_apply_schema_twice_is_idempotent(pg_conn)`: `apply_schema` twice, then `SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema()` == 9.
  - `test_config_defaults`: with env cleared via monkeypatch + `importlib.reload(config)`: `SYNC_WAIT_TIMEOUT_SEC == 1800`, `ENABLE_ANNOTATION_VECTORS is False`, `WEBHOOK_ALLOWED_HOSTS == []`.
- [ ] **Step 2:** Run `pytest tests/storage/test_schema.py -v` → FAIL (files missing).
- [ ] **Step 3:** Write `schema.sql` from spec §6 DDL plus `ml_model_registry.adapter JSONB` (§10.2); `CREATE EXTENSION` lines live only in `001_extensions.sql` and at the top of `schema.sql` guarded by `IF NOT EXISTS`. `classifier_entries.parent_code` nullable (§10.5). Add config keys and `.env.example` section with comments in Russian (style of existing file). `init_db.py`: `apply_schema` in one transaction, prints `schema applied`.
- [ ] **Step 4:** `pytest tests/storage/test_schema.py -v` → PASS (db test SKIPPED without DB).
- [ ] **Step 5:** Commit `feat(storage): PostgreSQL schema, pool and config`.

### Task 2a: ClassifierStore + ModelStore

**Files:** Create `src/storage/models.py`, `src/storage/base.py`, `src/storage/memory.py`, `src/storage/pg.py`, `src/storage/factory.py`, `src/storage/__init__.py`, `tests/storage/test_classifier_store.py`. (Later 2b–2d append to `models.py/base.py/memory.py/pg.py`.)

**Interfaces — Produces:**
```python
@dataclass class ClassifierEntry: code: str; name: str; level: int; parent_code: str|None; full_path: str; children_count: int; search_text: str  # base row text
@dataclass class ModelVersion: version: int; base_model_name: str; model_path: str; is_active: bool; recall5_before: float|None=None; recall5_after: float|None=None; training_samples_count: int|None=None; trained_at: datetime|None=None; report: dict|None=None; adapter: dict|None=None
class ClassifierStore(Protocol):
    def upsert_entries(self, entries: list[ClassifierEntry]) -> int
    def load_entries(self) -> list[ClassifierEntry]          # ordered by code; search_text from base row of active version
    def count_entries(self) -> int
    def add_vectors(self, version: int, rows: list[tuple[str, str, str, np.ndarray|None]]) -> int   # (code, source_type, search_text, embedding)
    def search(self, query: np.ndarray, version: int, top_k: int, include_annotations: bool) -> list[tuple[str, float]]   # SQL §7.3
    def pending_annotations(self, version: int) -> list[tuple[int, str]]     # (vector id, search_text) with NULL embedding
    def set_embedding(self, vector_id: int, embedding: np.ndarray) -> None
    def add_annotation(self, code: str, text: str, version: int) -> int     # returns vector id, embedding NULL
    def annotations_for(self, code: str) -> list[str]
    def annotated_codes(self) -> list[str]
    def vectors_for_version(self, version: int) -> list[tuple[str, str, str]]  # (code, source_type, search_text)
class ModelStore(Protocol):
    def register(self, mv: ModelVersion) -> None
    def active(self) -> ModelVersion | None
    def get(self, version: int) -> ModelVersion | None
    def list(self) -> list[ModelVersion]
    def activate(self, version: int) -> None   # one tx: all inactive, target active, NOTIFY model_changed
    def next_version(self) -> int
@dataclass class Storage: classifier: ClassifierStore; models: ModelStore; appeals: AppealStore; jobs: JobStore; historical: HistoricalStore
def get_storage() -> Storage   # cached; memory when config.STORAGE_BACKEND == "memory"
```
`tests/storage/conftest.py` gains fixture `storage` parametrized `["memory", "pg"]` (pg uses `pg_conn`; `PgStorage` accepts a connection factory so tests can bind it to the test schema).

- [ ] **Step 1: failing tests** (all run for both backends):
  - `test_upsert_and_load_entries_roundtrip`: 3 entries (L1 with `parent_code=None`) → `load_entries()` codes sorted, `count_entries()==3`, second upsert of same entries keeps 3.
  - `test_search_orders_by_cosine_and_groups_by_code`: version 0, unit vectors e1, e2, and annotation for code A = (e1+e2)/√2; query e2 with `include_annotations=True` → `[("B",1.0),("A",≈0.7071)]`; with `False` → A's similarity 0.0.
  - `test_search_ignores_null_embeddings`: annotation with NULL embedding never appears and doesn't raise.
  - `test_search_filters_by_version`: vectors in v0 and v1 → search v1 returns only v1 codes.
  - `test_activate_switches_single_active`: register v0 active, v1 inactive; `activate(1)` → `active().version==1`, `get(0).is_active is False`.
  - `test_add_annotation_then_pending_then_set_embedding`.
- [ ] **Step 2:** `pytest tests/storage/test_classifier_store.py -v` → FAIL.
- [ ] **Step 3:** Implement both backends. Memory search = numpy dot over stored rows, max per code. PG search uses the §7.3 SQL inside a transaction with `SET LOCAL hnsw.ef_search = 400`.
- [ ] **Step 4:** → PASS (pg cases SKIPPED without DB).
- [ ] **Step 5:** Commit `feat(storage): classifier vectors and model registry stores`.

### Task 2b: AppealStore

**Files:** Modify `src/storage/{models,base,memory,pg}.py`; Create `tests/storage/test_appeal_store.py`.

**Interfaces — Produces:**
```python
@dataclass class AppealQuestionRow: ordinal: int; question_text: str; selected_code: str; confidence: float; alternative_codes: list[str]; predmet_vedeniya: str=""; reasoning: str=""; verification_reasons: list[str]=field(default_factory=list)
@dataclass class AppealResult: summary: str|None; applicant_fio: str|None; applicant_email: str|None; vid_obrascheniya: str; tip_obrascheniya: str; is_ustnoe: bool; overall_confidence: float; needs_verification: bool; full_fallback_used: bool; candidates: list[dict]; llm_provider: str; llm_model: str; elapsed_seconds: float|None; questions: list[AppealQuestionRow]
class AppealStore(Protocol):
    def create(self, appeal_text: str, document_id: int|None=None, ip_address: str|None=None, appeal_id: str|None=None, created_at: datetime|None=None) -> str   # uuid str; also creates pending verification
    def save_result(self, appeal_id: str, result: AppealResult, conn=None) -> None   # replaces questions (delete+insert); conn lets Task 7 share the job tx
    def get_text(self, appeal_id: str) -> str | None
    def set_verification(self, appeal_id: str, status: str, operator_codes: list[str]|None, comment: str|None, verified_by: str|None=None, verified_at: datetime|None=None) -> bool
    def as_log_entries(self, statuses: tuple[str,...]|None=None) -> list[dict]   # same dict shape as old appeals_log.jsonl lines (id, timestamp, appeal_text, candidates, agent_questions, overall_confidence, verification{status,verified_at,operator_codes,comment})
    def stats(self) -> dict          # keys: total, verified, pending, confirmed, corrected, rejected
    def backoffice_stats(self, days: int = 30) -> dict   # keys identical to /api/backoffice/stats response minus top_codes names (code,count only)
    def log_llm_request(self, appeal_id: str|None, success: bool, attempt: int, elapsed: float|None, confidence: float|None, error: str|None, candidates_count: int) -> None
    def set_request_meta(self, appeal_id: str, ip_address: str|None, elapsed_seconds: float|None) -> None
```
- [ ] **Step 1: failing tests:** `test_create_makes_pending_verification`, `test_save_result_twice_replaces_questions` (2 questions then 1 → exactly 1 row), `test_set_verification_unknown_id_returns_false`, `test_as_log_entries_shape_matches_legacy` (assert key set equals legacy keys listed above), `test_stats_counts`, `test_backoffice_stats_histogram_and_daily_usage` (port expectations from `tests/test_api_endpoints.py::test_backoffice_stats_histogram_binning` and `..._daily_usage_count`), `test_ip_stats_from_cards`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(storage): appeal cards, questions, verifications`.

### Task 2c: JobStore

**Files:** Modify `src/storage/{models,base,memory,pg}.py`; Create `tests/storage/test_job_store.py`; Delete `tests/test_job_queue.py`, `src/job_queue.py` (after Task 10 removes its import — do the delete in Task 10).

**Interfaces — Produces:**
```python
STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR = "queued","running","done","error"   # in storage/models.py
@dataclass class Job: id: str; job_type: str; status: str; appeal_id: str|None; document_id: int|None; webhook_url: str|None; payload: dict; result: dict|None; error: str|None; attempts: int; created_at: datetime; started_at: datetime|None; finished_at: datetime|None
    elapsed_sec -> float (property, same rule as old job_queue.Job)
class QueueFull(RuntimeError)
class JobStore(Protocol):
    def create(self, job_type: str, payload: dict, appeal_id: str|None=None, document_id: int|None=None, webhook_url: str|None=None, max_queued: int|None=None, queue_class: str="classify") -> Job  # raises QueueFull when max_queued given and count(queued, job_type, payload->>'queue'=queue_class) >= max_queued; id = uuid4().hex
    def get(self, job_id: str) -> Job | None       # None for ids not matching ^[0-9a-f]{32}$
    def claim(self, job_id: str) -> Job | None     # queued|running → running, attempts+1; None if done/error/missing
    def finish(self, job_id: str, result: dict|None, error: str|None, conn=None) -> None   # sets done/error + finished_at + NOTIFY job_done
    def position(self, job_id: str) -> int | None
    def list(self, limit: int = 50, job_type: str|None=None) -> list[Job]
    def stats(self) -> dict      # queued, running, done, error
    def latest(self, job_type: str) -> Job | None
    def has_active(self, job_type: str) -> bool
    def cleanup_expired(self, ttl_hours: float) -> int
    def import_job(self, job: Job) -> None        # migration only; ON CONFLICT DO NOTHING
```
Memory backend: `finish` calls registered listeners (`add_listener(callable)`) to emulate NOTIFY.
- [ ] **Step 1: failing tests:** port semantics from the old `tests/test_job_queue.py` (queue full, position order, TTL cleanup, bad id → None) plus `test_claim_done_job_returns_none`, `test_finish_notifies` (memory: listener called; pg: a second connection `LISTEN job_done` receives the id after commit — `@pytest.mark.db`), `test_sync_queue_not_counted_in_limit` (queue_class `classify_sync` jobs don't trigger QueueFull for `classify`).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(storage): jobs table with NOTIFY`.

### Task 2d: HistoricalStore

**Files:** Modify `src/storage/{models,base,memory,pg}.py`, `src/historical_loader.py`, `tests/test_historical_loader.py`; Create `tests/storage/test_historical_store.py`.

**Interfaces — Produces:** `HistoricalStore.add(records: list[dict], source_file: str) -> int` (inserted count, duplicates skipped by dedup key §6), `count() -> int`, `all() -> list[dict]` (keys `appeal_text, assigned_code, specialist, registration_date, source_file`). `historical_loader.save_to_historical_db(records, source_file) -> int` replaces `save_to_historical_jsonl` (remove the old function and `HISTORICAL_FILE`).
- [ ] **Step 1:** tests `test_add_dedups_same_record`, `test_count_and_all`; update `tests/test_historical_loader.py` save test to use memory storage.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(storage): historical appeals store`.

### Task 3: Migration from files

**Files:** Create `scripts/db/migrate_from_files.py`, `tests/scripts/test_migrate.py`, `tests/fixtures/migration/` (tiny: 3-entry `classifier_flat.json`, matching `metadata.json` + `embeddings.npy` (3×768), 2-line `appeals_log.jsonl` (one corrected), 3-line `request_log.jsonl` (IP record with log_id, IP record without, LLM event), 1-line `historical_verified.jsonl`, annotations `{code:{note:"x"}}`, `jobs/<hex>.json` done + one running, `models/e5-finetuned-v1/training_report.json`).

**Interfaces — Produces:** `migrate(storage: Storage, data_dir: Path, vector_db_dir: Path, models_dir: Path, embedding_model: str) -> dict[str, tuple[int, int]]` (source → (file count, db count)); CLI `python scripts/db/migrate_from_files.py [--data-dir data] [--vector-db-dir <VECTOR_DB_DIR>] [--models-dir models]` prints the reconciliation table, exit 1 on mismatch.

Rules (§8.1 + §10): L1 `parent_code '0000.0000.0000.0000'` → NULL; v0 registered active with `adapter` = contents of `VECTOR_DB_DIR/adapter_v3.json` if present; v0 base vectors skipped if v0 already has vectors; metadata/entries code-set mismatch → `SystemExit` with the differing codes; annotations → one row per field with text `f"{field}: {value}"` (`correct_name` → value only), NULL embedding; jobs queued/running → `error` "Сервис переведён на новую очередь, отправьте обращение заново".
- [ ] **Step 1: failing tests** (memory storage; plus one pg-marked copy of the idempotency test): `test_migration_counts` (entries 3, vectors 3+1 annotation, appeals 2, questions per log, verifications statuses preserved, llm_request_log 1, ip set on matching card, historical 1, jobs 2 with running→error, registry v0 active + v1 inactive with recall from report), `test_migration_is_idempotent` (run twice, same reconciliation), `test_mismatched_metadata_aborts`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(migration): import file storage into PostgreSQL`.

### Task 4: ClassifierAgent on the DB

**Files:** Modify `src/classifier_agent.py` (`__init__` 591-627, `_search_candidates` 1095-1106, `_log_request` 1812-1833, `classify` signature 1944 and logging block 2290-2320), `src/appeals_logger.py`, `src/annotations_storage.py`, `src/build_vectordb.py`; Create `scripts/db/verify_search_parity.py`, `tests/test_agent_storage.py`; Modify tests that poke `agent.metadata`/`agent.embeddings` only if they break.

**Interfaces:**
- Consumes: `get_storage()`, `ClassifierStore.search/load_entries`, `ModelStore.active`, `AppealStore.*`.
- Produces:
  - `ClassifierAgent.__init__(self, storage: Storage | None = None)`: `self.model_version` = active version (raise `RuntimeError("Нет активной модели в ml_model_registry — запустите scripts/db/migrate_from_files.py")` if none); embedder loads `active.model_path` (v0 path = `EMBEDDING_MODEL`); `self.metadata: list[dict]` built from `load_entries()` with the same keys as old metadata.json; `self.code_index` as before; `self.embeddings` removed.
  - `ClassifierAgent.reload_model_if_changed(self) -> bool`.
  - `ClassifierAgent.classify(self, appeal_text, llm_provider=None, llm_model=None, appeal_id: str | None = None, log_conn=None) -> ClassificationResult` (`log_conn` is passed to `AppealsLogger.log(conn=)`); `result.elapsed_seconds: float` new field on `ClassificationResult`.
  - `ClassifierAgent.embed_passage(self, text: str) -> np.ndarray` (applies `passage:` prefix rule from `build_vectordb.py`).
  - `AppealsLogger(storage: Storage | None = None)` keeps `log(...)`, `confirm`, `correct`, `reject`, `read_all`, `read_verified`, `stats`; `log` gains kwargs `appeal_id=None, result: AppealResult | None = None, conn=None` and returns the appeal id (creates the card if `appeal_id` is None). `correct()` stores the comment via `add_annotation` (fixes missing function).
  - `annotations_storage.add_annotation(code, text, case_id=None, operator_code="") -> int`, `get_annotations(code) -> list[str]`, `list_annotated_codes()`, `build_search_text(code, base_name, base_full_path)` (legacy dict-style `correct_name/category/note` parsed from annotation texts with those prefixes), `save_annotation`/`delete_annotation` kept, `backup_annotations` removed.
  - `build_vectordb.build_database(storage, model_path: str, version: int) -> int` writes base rows (folded `build_search_text`) + annotation rows for `version`.
- [ ] **Step 1: failing tests** (memory storage, fake embedder `lambda texts, **kw: unit vectors by hash` injected via monkeypatching `SentenceTransformer` in `classifier_agent`):
  - `test_agent_requires_active_model`: empty registry → RuntimeError with "Нет активной модели".
  - `test_search_candidates_uses_store`: 3 entries/vectors → `_search_candidates("q")` returns codes in store order with `similarity` float.
  - `test_classify_logs_to_given_appeal_id`: monkeypatch `_classify_with_llm` to a canned dict; `classify(text, appeal_id=id)` → `result.log_id == id`, `storage.appeals.as_log_entries()` has 1 entry.
  - `test_reload_model_if_changed`: activate v1 → returns True, `model_version==1`.
  - `test_correct_with_comment_creates_annotation`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. Search keeps the `candidate_from_metadata` shape; annotation inclusion = `config.ENABLE_ANNOTATION_VECTORS`.
- [ ] **Step 4:** PASS; then full `pytest tests/ -q --ignore=tests/e2e` → no new failures vs baseline.
- [ ] **Step 5:** `verify_search_parity.py --queries data/ii25_test.jsonl --extra 20`: loads numpy files + DB, compares top-50 per query, prints `parity OK (76/76)` or the first diff and exits 1. (Runs in Task 13 against a live DB.)
- [ ] **Step 6:** Commit `feat(agent): candidates and logs from PostgreSQL`.

### Task 5: Shared response builders

**Files:** Create `src/responses.py`, `tests/test_responses.py`; Modify `src/api_server.py` (remove models 128-240, import from `responses`), `src/integration_api.py` (remove models 25-150).

**Interfaces — Produces:** in `responses.py` (imports only pydantic/stdlib): `ClassifyRequest, AlternativeItem, QuestionResult, ClassifyResponse, ClassifyDocumentRequest (+ webhook_url: Optional[str] = None), RxQuestion, ClassifyDocumentResponse, AcceptedResponse, JobStatusResponse, REASONING_MAX_LEN`, `build_classify_response(result, operator_card: str, appeal_id=None, was_truncated=False) -> ClassifyResponse`, `build_document_response(result, document_id) -> ClassifyDocumentResponse` (includes `_unique_by_code`, `_build_reasoning`, `_clip_reasoning`).
- [ ] **Step 1:** tests move the existing dedup/reasoning assertions from `tests/test_integration_dedup_codes.py` to call `responses.build_document_response` directly (keep old tests passing too) + `test_responses_module_has_no_heavy_imports` (subprocess: `import responses; assert 'torch' not in sys.modules and 'classifier_agent' not in sys.modules`).
- [ ] **Step 2–4:** FAIL → move code → PASS (whole suite no new failures).
- [ ] **Step 5:** Commit `refactor: response models into src/responses.py`.

### Task 6: Celery app, worker bootstrap, webhook

**Files:** Create `src/queueing/__init__.py`, `src/queueing/celery_app.py`, `src/queueing/webhook.py`, `src/worker_main.py`, `tests/queueing/test_celery_config.py`, `tests/queueing/test_webhook.py`.

**Interfaces — Produces:**
- `celery_app.app` (Celery, broker `config.RABBITMQ_URL`, settings from Global Constraints, `task_queues` = 3 quorum `kombu.Queue`s, `task_routes` per task name).
- `worker_main.get_agent() -> ClassifierAgent` (process singleton; before returning calls `reload_model_if_changed()` and `refresh_settings_if_env_changed()`), `worker_main.refresh_settings_if_env_changed() -> bool` (compares `.env` mtime; on change parses with `dotenv_values`, calls `settings_store.apply_runtime(updates, agent)` + `refresh_agent_llm`), Celery `worker_process_init` signal → `get_agent()` + embed pending annotations.
- `webhook.is_allowed(url: str) -> bool` (host in `config.WEBHOOK_ALLOWED_HOSTS`, scheme http/https), `webhook.sign(body: bytes) -> str | None`, `webhook.deliver(url: str, payload: dict) -> bool` (httpx, timeout 10 s, 3 attempts, `trust_env` via `proxy_policy.uses_env_proxy(url, "auto")`, header `X-Signature` when secret set; never raises).
- [ ] **Step 1:** tests: `test_queues_are_quorum` (`x-queue-type == "quorum"` on all 3), `test_acks_late_and_prefetch_one`, `test_routes` (`classify_appeal` default queue `classify`), `test_is_allowed_rejects_unknown_host`, `test_is_allowed_empty_list_disables`, `test_sign_hmac_sha256` (known vector), `test_deliver_retries_then_false` (httpx MockTransport 500×3), `test_env_change_triggers_refresh` (tmp .env, touch → True once, then False).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(queue): Celery app with quorum queues, webhook delivery`.

### Task 7: Worker tasks

**Files:** Create `src/queueing/tasks.py`, `tests/queueing/test_tasks.py`.

**Interfaces:**
- Consumes: `get_agent()`, `Storage`, `responses.build_*`, `webhook.deliver`.
- Produces (Celery tasks, all take the ТЗ message dict `msg = {"job_id", "appeal_id", "document_id"}`; plain functions `run_<name>(msg, storage, agent)` hold the logic and are what tests and `LocalDispatcher` call):
  - `classify_appeal(msg)`: `jobs.claim` → None ⇒ return. Reads text + `payload` (`llm_provider`, `llm_model`, `response_kind` ∈ `"classify"|"document"`, `was_truncated`, `client_appeal_id`). Opens `db.transaction()` as `conn` (memory backend: a null context), calls `agent.classify(text, provider, model, appeal_id=msg["appeal_id"], log_conn=conn)` — the agent's logger writes the card on `conn` — then `jobs.finish(job_id, result=<response dict>, error=None, conn=conn)`; card + job status + NOTIFY commit together. On exception: infrastructure errors (`psycopg.OperationalError`, `kombu` errors) with `job.attempts < JOB_MAX_ATTEMPTS` → re-raise (redelivery); otherwise `finish(error=f"{type(e).__name__}: {e}")`. After finish, if `job.webhook_url` → `deliver(url, JobStatusResponse dict)`.
  - `search_classifier(msg)`: payload `{q, top_k}` → `finish(result={"query": q, "results": [...]})`.
  - `embed_annotation(msg)`: payload `{vector_id}` → `set_embedding(embed_passage(text))`, finish.
  - `finetune(msg)`: delegates to `finetune_model.run_training(storage, source, force)` (Task 11) and finishes with the status dict.
- [ ] **Step 1: failing tests** (memory storage + fake agent with `classify(text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None)`): `test_classify_success_writes_result_and_notifies`, `test_classify_llm_error_marks_error`, `test_redelivered_done_job_is_skipped` (agent not called), `test_rerun_replaces_questions`, `test_infra_error_reraised_until_max_attempts`, `test_webhook_sent_after_finish` (deliver monkeypatched, called once with status `done`), `test_document_response_kind_uses_build_document_response`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(worker): classification, search, annotation, finetune tasks`.

### Task 8: Dispatcher and waiter (API side)

**Files:** Create `src/queueing/dispatcher.py`, `src/queueing/waiter.py`, `tests/queueing/test_dispatcher_waiter.py`.

**Interfaces — Produces:**
- `Dispatcher` Protocol: `send(task_name: str, msg: dict, queue: str) -> None`.
- `CeleryDispatcher.send` → `celery_app.app.send_task(task_name, args=[msg], queue=queue)`; raises `DispatchError` on broker failure.
- `LocalDispatcher(storage, agent_factory)` runs `tasks.run_<name>` synchronously in a thread (so `await waiter.wait` behaves like production).
- `JobWaiter(storage)`: `async start()`/`async stop()` (pg: one `psycopg.AsyncConnection` with `LISTEN job_done`, reconnect loop; memory: `jobs.add_listener`), `async wait(job_id: str, timeout: float, recheck_every: float = 5.0) -> Job | None` (returns finished Job or None on timeout; re-reads the DB every `recheck_every`).
- `get_dispatcher()`, `get_waiter()` module singletons chosen by `STORAGE_BACKEND` (`memory` → Local).
- [ ] **Step 1:** tests: `test_wait_returns_on_notify` (finish from another thread after 0.1 s; wait returns < 1 s), `test_wait_timeout_returns_none`, `test_waiter_falls_back_to_db_recheck` (finish job without notifying listeners; `recheck_every=0.1` → returns), `test_celery_dispatcher_wraps_broker_error` (monkeypatch `send_task` to raise `kombu.exceptions.OperationalError` → `DispatchError`).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(api): dispatcher and LISTEN/NOTIFY waiter`.

### Task 9: Thin API server

**Files:** Modify `src/api_server.py`, `src/settings_store.py` (add `SYNC_WAIT_TIMEOUT_SEC` field to a new group `"queue"` labelled "Очередь", kind `text`, validated as positive int), `src/static/backoffice.html` (+ JS) for model versions list/activate, `tests/test_api_endpoints.py`; Create `tests/test_api_thin.py`.

**Interfaces — Consumes:** Tasks 2–8. **Behaviour per endpoint (same response models):**
- startup: `get_storage()`, `await get_waiter().start()`; no agent. Shutdown: stop waiter, close pool.
- `POST /classify`, `POST /classify/file`: create appeal (ip from request) → `jobs.create("classify", payload, queue_class="classify_sync")` → `dispatcher.send("classify_appeal", msg, "classify_sync")` (DispatchError → job error + 503) → `wait(SYNC_WAIT_TIMEOUT_SEC)` → None ⇒ 504 `"Классификация не завершилась за N с, задача <id> продолжает выполняться: GET /integration/jobs/<id>"`; job error ⇒ 500 `"Ошибка классификации: <error>"`; done ⇒ `ClassifyResponse(**job.result)`. After done: `appeals.set_request_meta(appeal_id, ip, elapsed)`.
- `GET /classifier/search`: job `search` via `classify_sync`, wait 60 s.
- `POST /verify`: as today via `AppealsLogger(storage)`; on `correct` with annotation → `classifier.add_annotation` + dispatch `embed_annotation` on `classify`; `annotation_count = len(annotations_for(code))`.
- `/stats`, `/api/backoffice/stats`: from `AppealStore.stats/backoffice_stats`; top-code names from `ClassifierStore.load_entries()` (cached).
- `/health`: `{"status","agent_ready","classifier_entries","db","broker"}` — `agent_ready` from `celery_app.app.control.ping(timeout=1)` cached 10 s (memory backend: True).
- historical upload/confirm/count → `HistoricalStore`.
- `POST /api/finetune`: 409 if `jobs.has_active("finetune")`; create + send to `training`; returns status snapshot. `GET /api/finetune/status`: maps `jobs.latest("finetune")` to the old keys (`job_id,status(idle|queued|running|completed|failed),source,force,started_at,finished_at,message,model_path,returncode,log_tail`).
- New: `GET /api/models` (list), `POST /api/models/{version}/activate` (Basic Auth; 404 unknown).
- Remove: `agent` global, `_finetune_*`, `_reload_agent_with_model`, `_REQUEST_LOG`, `log_request` file writes.
- [ ] **Step 1: failing tests** in `tests/test_api_thin.py` (`STORAGE_BACKEND=memory`, LocalDispatcher with fake agent): `test_api_does_not_import_heavy_modules` (subprocess import of `api_server` → no `torch`/`classifier_agent`/`sentence_transformers` in `sys.modules`), `test_classify_sync_roundtrip`, `test_classify_sync_timeout_returns_504` (`SYNC_WAIT_TIMEOUT_SEC=0.2`, slow fake agent), `test_classify_dispatch_failure_returns_503`, `test_health_shape`, `test_finetune_conflict_409`, `test_models_activate`. Port `tests/test_api_endpoints.py` fixtures (`isolated_logger`, `backoffice_logger`) to memory storage keeping assertions; `test_log_request_*` become `test_request_meta_*` on cards.
- [ ] **Step 2–4:** FAIL → implement → PASS; full suite no new failures.
- [ ] **Step 5:** Commit `feat(api): thin API over queue and PostgreSQL`.

### Task 10: Integration API on the queue

**Files:** Modify `src/integration_api.py`, `tests/test_integration_api.py`, `tests/test_integration_async.py`, `tests/test_integration_text_input.py`, `tests/test_integration_scan_pdf.py`, `tests/test_integration_dedup_codes.py`; Delete `src/job_queue.py`, `tests/test_job_queue.py`.

**Behaviour:** `_resolve_text` unchanged (stays in API). Sync `/classify-document`: like `/classify` with `payload.response_kind="document"`, queue `classify_sync`. Async: `webhook_url` given and not `webhook.is_allowed` → 400 `"webhook_url не разрешён: хост не входит в WEBHOOK_ALLOWED_HOSTS"`; `jobs.create(..., max_queued=JOB_MAX_QUEUED, queue_class="classify")` → `QueueFull` → 429 (same detail prefix `"Очередь заполнена: "`); cleanup_expired(JOB_TTL_HOURS) on submit; send to `classify`; 202 body/headers as today. `GET /jobs/{id}` and `GET /jobs` from JobStore with identical response shapes.
- [ ] **Step 1:** rewrite fixtures: `_FakeAgent.classify` accepts the new kwargs; `isolated_queue` → fresh memory storage + LocalDispatcher. Keep every existing assertion. Add `test_webhook_host_not_allowed_returns_400`, `test_webhook_delivered_on_done` (allowlisted host, deliver mocked).
- [ ] **Step 2–4:** FAIL → implement → PASS; full suite no new failures.
- [ ] **Step 5:** Commit `feat(integration): RX endpoints over RabbitMQ jobs`.

### Task 11: Training worker, registry, scripts

**Files:** Modify `src/finetune_model.py`, `src/auto_finetune.py`, `src/auto_import_historical.py`, `src/operator_cli.py` (uses `AppealsLogger(get_storage())` — check only), `tests/` (new `tests/test_finetune_registry.py`).

**Interfaces — Produces:** `finetune_model.run_training(storage: Storage, source: str = "combined", force: bool = False, train_fn=None) -> dict` with keys `status ("completed"|"failed"|"skipped")`, `message`, `model_path`, `version`, `recall5_before`, `recall5_after`, `activated: bool`, `log_tail`. Steps: data from `appeals.as_log_entries(("confirmed","corrected"))` + `historical.all()`; base model = active `model_path`; save to `models/e5-finetuned-v{N}` with N = `models.next_version()`; `build_vectordb.build_database(storage, path, N)`; register (report JSON, adapter None); `activate(N)` only if `recall5_after > recall5_before`. `train_fn` injectable for tests (default = existing SentenceTransformers fit). CLI `main()` calls `run_training`. `auto_finetune.py`: threshold check via `appeals.stats()["verified"]`, then `jobs.create("finetune")` + `CeleryDispatcher.send("finetune", msg, "training")`. `auto_import_historical.py` → `save_to_historical_db`.
- [ ] **Step 1:** tests with fake `train_fn` returning recall pairs: `test_improved_model_is_activated`, `test_not_improved_model_registered_inactive`, `test_new_version_has_vectors_for_all_entries`, `test_training_reads_db_not_files` (no `data/appeals_log.jsonl` access: monkeypatch `open` guard).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(training): isolated fine-tuning with model registry`.

### Task 12: Deployment files and launch scripts

**Files:** Rewrite `docker-compose.yml`; Modify `Dockerfile` (copy `scripts/`, `deploy/`; keep CMD for api), `.gitignore` (`run/`), `.env.example`; Create `start.sh`, `start.cmd`, `stop.sh`, `stop.cmd`; Modify `README.md`, `CLAUDE.md`, `AGENTS.md` (architecture, commands, PII note), `launch.bat`/`launch.sh` (hint to `start.*`); tests `tests/test_deploy_layout.py`.

Compose (§8.2): services `postgres` (`pgvector/pgvector:pg16`, env from `.env`, volume `pgdata:/var/lib/postgresql/data`, `./deploy/db/init:/docker-entrypoint-initdb.d:ro`, healthcheck `pg_isready`), `rabbitmq` (`rabbitmq:3.13-management`, ports 5672/15672, healthcheck `rabbitmq-diagnostics -q ping`), `migrate` (`build: .`, `command: sh -c "python scripts/db/init_db.py && python scripts/db/migrate_from_files.py"`, mounts `./data:/app/data:ro`, `./models:/app/models`, `restart: "no"`), `api` (depends_on migrate `service_completed_successfully`, ports `${HOST_PORT:-8010}:8005`, no model/HF mounts), `worker-classify` (`celery -A src.queueing.celery_app worker -Q classify_sync,classify -c ${CLASSIFY_CONCURRENCY:-2} --loglevel=INFO`, HF cache + models mounts), `worker-training` (`-Q training -c 1`, `profiles: [training]`, commented GPU `deploy.resources` block). App services share `.env` bind mount and get `DATABASE_URL`/`RABBITMQ_URL` overridden to service hostnames.

Scripts (§8.3) — modes `docker` (default) | `local` | `migrate`, flag `--training`; `local` on Windows opens `start "classifier-api"` / `start "classifier-worker-N"` windows with `--pool=solo` × `CLASSIFY_WORKERS`; on Linux background processes, pids in `run/*.pid`, logs in `run/*.log`. Both wait for `GET /health` (60 × 2 s). `stop.*`: `local` kills pid files / window titles; `docker` → `docker compose down` (no `-v`).
- [ ] **Step 1:** tests `test_compose_services` (yaml load: 6 services, `worker-training` has profile `training`, no `down -v` anywhere in stop scripts), `test_compose_mounts_init_sql`, `test_cmd_files_ascii_executable_lines` (lines not starting with `REM`/`::` are ASCII), `test_start_sh_has_modes` (grep `docker)`, `local)`, `migrate)`), `bash -n start.sh stop.sh` via subprocess (skip if no bash).
- [ ] **Step 2–4:** FAIL → write files → PASS. Also `docker compose config -q` → exit 0 (skip note if daemon/CLI unavailable).
- [ ] **Step 5:** Commit `feat(deploy): compose stack and start/stop scripts`.

### Task 13: End-to-end verification

- [ ] **Step 1:** Full suite: `pytest tests/ -q --ignore=tests/e2e` → failures ⊆ baseline (Task 0 file).
- [ ] **Step 2 (needs Docker Desktop running — ask the user to start it):** `docker compose up -d postgres rabbitmq`; `start.cmd migrate` → reconciliation table all-equal; `TEST_DATABASE_URL=… pytest -m db -q` → all pass.
- [ ] **Step 3:** `python scripts/db/verify_search_parity.py --queries data/ii25_test.jsonl --extra 20` → `parity OK`.
- [ ] **Step 4:** `start.cmd local`; `curl /health` → `agent_ready: true`; POST `/integration/classify-document-async` → 202, poll → `done`; POST `/classify` (sync) returns a result; kill one worker window mid-task → job still reaches `done` (other worker picks the redelivered message).
- [ ] **Step 5:** Record outputs in `docs/superpowers/plans/2026-11-11-verification.md`; commit `docs: verification of PostgreSQL/RabbitMQ migration`.

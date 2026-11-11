# Verification: PostgreSQL + pgvector / RabbitMQ / Celery migration (Task 13)

Date: 2026-11-11. Branch `Database_Rabbit`. Host: Windows 10, Python 3.13 venv, Docker Desktop.
Only commands, counts, statuses and timings are recorded here — no appeal content.
Classification requests used only the synthetic appeals from `data/test_appeals.json`.

## Environment

The host already runs its own PostgreSQL 17 (:5432) and RabbitMQ (:5672, :15672). They were not
touched. The compose infrastructure was published on alternate ports (made configurable in commit
`b1ce2cd fix(deploy): configurable RabbitMQ host ports`). `.env` was not edited; every command ran
with process environment variables (python-dotenv does not override them):

```
POSTGRES_PORT=5434 RABBITMQ_PORT=5673 RABBITMQ_MGMT_PORT=15673
DATABASE_URL=postgresql://classifier:classifier@localhost:5434/classifier
RABBITMQ_URL=amqp://classifier:classifier@localhost:5673//
STORAGE_BACKEND=postgres PYTHONPATH=src PYTHONUTF8=1
```

Before each step the effective `config.DATABASE_URL` / `config.RABBITMQ_URL` were printed and
asserted to point at `:5434` / `:5673`. The test DB `classifier-test-pg` (:5433) kept running.
The application image was not built: only the `postgres` and `rabbitmq` compose services ran in
Docker; API and workers ran from the venv.

## Step 1 — full test suite

```
PYTHONUTF8=1 TEST_DATABASE_URL=postgresql://classifier:classifier@localhost:5433/classifier \
  venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e -p no:cacheprovider
```

| Run | Result |
|---|---|
| before the fix below | 4 failed, 786 passed (95.8 s) |
| after the fix (final) | 4 failed, 787 passed (108.6 s) |

The 4 failures are exactly the pre-existing baseline (`test_get_current_user_accepts_correct_credentials`,
3 × `test_candidate_rerank`).

## Step 2 — infrastructure and migration on real data

`docker compose up -d --wait postgres rabbitmq` → both `healthy` (first start incl. image pull 2 min 21 s).
Published: `127.0.0.1:5434->5432`, `127.0.0.1:5673->5672`, `127.0.0.1:15673->15672`.

`start.cmd migrate` (run via `cmd /c start.cmd migrate`; the environment variables pass through to
`cmd.exe` and its child Python processes unchanged).

Run 1 — exit 0, 208 s:

```
Сверка (источник: в файлах / в БД):
  classifier_entries       2108     2108  OK
  vectors_base             2108     2108  OK
  model_registry              1        1  OK
  annotations                 0        0  OK
  appeals                  1613     1613  OK
  questions                2358     2358  OK
  verifications            1613     1613  OK
  ip_records                645      645  OK
  llm_events               3439     3439  OK
  historical                 20       20  OK
  jobs                        0        0  OK
  warnings                    0
```

(Import log: model v0 registered and active, 2108 vectors loaded, appeals created 1613,
IP records applied 645 / without a pair 72.)

Run 2 (idempotency) — exit 0, 59 s: same table, all `OK`; vectors v0 skipped as present,
appeals created 0 / already present 1613, `llm_events 3439 3439 skipped (table not empty) OK`.
Run 3 happened inside `start.cmd local` (Step 4) — same table, all `OK`.

`pytest -m db` against the real DB was deliberately not run (it creates test schemas); the db
tests ran against `classifier-test-pg` (:5433) in Step 1.

## Step 3 — retrieval parity numpy vs pgvector

```
venv/Scripts/python.exe scripts/db/verify_search_parity.py
```

Defaults: `--queries data/ii25_test.jsonl` (56) + `--extra 20` random appeals from the DB,
`VECTOR_DB_DIR=data/vector_db_adapted_v3`, model version 0, top-50, tolerance 1e-5.

Result: `parity OK (76/76)`, exit 0, 74 s.

## Step 4 — API + 2 classify workers, end-to-end

`cmd /c start.cmd local` → exit 0 in 71–80 s; three console windows opened
(`classifier-api`, `classifier-worker-1`, `classifier-worker-2`, Celery pool `solo`).
`GET /health` right after start:

```
{"status":"ok","agent_ready":true,"classifier_entries":2108,"db":true,"broker":true}
```

(`agent_ready` turned true within 2–8 s — it reflects a worker answering ping; the embedding model
loads lazily on the first task of each worker, see the first-job timing below.)
Connections were confirmed on the compose DB (`pg_stat_activity`: 4 client backends) and the
compose broker (`classify` and `classify_sync` quorum queues, 2 consumers each).

### Defect found and fixed

The first async job ended `error` after 2 s with
`AttributeError: 'LoggingProxy' object has no attribute 'reconfigure'`:
`classifier_agent` called `sys.stdout.reconfigure()` at import time; Celery replaces stdout with
`LoggingProxy`, and the agent is imported lazily inside the task, so **every** classification task
failed. Fixed in `55b17eb fix(worker): classifier_agent import under Celery stdout proxy`
(regression test `tests/test_agent_import_under_celery.py`: RED without the fix, GREEN with it).
The local stack was restarted (`stop.cmd local`, `start.cmd local`) and the checks repeated.

### Results after the fix (LLM provider from `.env`: `ario`, reachable)

| Check | Result |
|---|---|
| `GET /integration/jobs/<32 zeros>` | `404` |
| `POST /integration/classify-document-async` (synthetic ex01) | `202`, 32-hex `job_id`, `status=queued`, `queue_position=1`, `Location: /integration/jobs/{id}` |
| poll `GET /integration/jobs/{id}` | `queued` → `running` at t+26.9 s (first task: model load) → `done` at t+43.4 s; 1 question code |
| `POST /classify` sync (synthetic ex02) | `200` in 19.8 s; 1 question code, `needs_verification=true` |
| second async job (synthetic ex03) | `done`, elapsed 18.4 s |

### Crash test (worker killed mid-task)

Task events enabled on both workers (`enable_events`); the job's `task-started` event identified
the worker; that worker's Python process was killed.

```
submit (synthetic ex04): HTTP 202
t+3.2s  task-started on classify1 pid=26076
t+4.3s  status=running
t+4.6s  taskkill /PID 26076 /F -> rc=0
t+4.6s  task-received + task-started on classify2 pid=1416   (redelivered message)
t+21.4s task-succeeded on classify2
t+23.2s status=done, 1 question code
```

DB row of that job: `status=done`, `attempts=2`. Outcome: **redelivery to the other worker**
(`acks_late` + `reject_on_worker_lost`), not the attempts cap.

Jobs in the DB at the end (status / attempts): 1 × error/1 (the pre-fix run), 3 × done/1,
1 × done/2 (crash test). The five synthetic appeals were stored in the compose DB as usual.

## Step 5 — shutdown

`cmd /c stop.cmd local` → closed the `classifier-*` windows that were running, `docker compose stop
postgres rabbitmq` (containers `Exited (0)`, volume `class-og-final_pgdata` kept; no `down -v`).
One window was left open: `classifier-worker-1`, whose Python had been killed in the crash test —
its idle console title is `Администратор: classifier-worker-1` (elevated console), which the
`WINDOWTITLE eq classifier-*` filter does not match. Closed manually by PID.

After shutdown: native PostgreSQL :5432 (PID 5924) and RabbitMQ :5672/:15672 (PID 4616) still
listening; `classifier-test-pg` (:5433) `Up`. No `uvicorn`/`celery` processes left.

## Open observations

- `stop.cmd local` misses a `classifier-*` window whose program has exited when the console is
  elevated (title prefix `Администратор: `). Not fixed here.
- `migrate_from_files.py` prints `couldn't stop thread 'pool-1-worker-N' within 5.0 seconds` at
  exit (connection pool not closed explicitly); cosmetic, adds ~20 s to each run.
- `agent_ready` means "a worker answered ping", not "model loaded": the first task on each worker
  waits for the embedding model (~25 s).

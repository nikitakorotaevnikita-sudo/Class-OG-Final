"""Очередь задач в таблице jobs: захват, аренда, повтор, доставка, чистка (память и PostgreSQL)."""

import threading
import time
from contextlib import contextmanager

import pytest

from storage.models import LOST_TRAINING_ERROR, STATUS_ERROR, STATUS_QUEUED, STATUS_RUNNING

W = "host:1"


def test_create_sets_queue_and_available_at(storage):
    job = storage.jobs.create("classify", {"text": "т"}, queue_class="classify_sync")
    assert job.queue == "classify_sync"
    assert job.available_at is not None
    assert job.locked_until is None and job.worker_id is None and job.delivered_at is None
    got = storage.jobs.get(job.id)
    assert got.queue == "classify_sync" and got.available_at is not None
    assert storage.jobs.create("classify", {}).queue == "classify"


def test_claim_next_prefers_sync_then_fifo(storage):
    a = storage.jobs.create("classify", {}, queue_class="classify")
    time.sleep(0.01)
    b = storage.jobs.create("classify", {}, queue_class="classify")
    time.sleep(0.01)
    s = storage.jobs.create("search", {}, queue_class="classify_sync")
    queues = ["classify_sync", "classify"]
    first = storage.jobs.claim_next(queues, W, 60)
    assert first.id == s.id
    assert first.status == STATUS_RUNNING and first.attempts == 1
    assert first.worker_id == W and first.locked_until is not None
    assert first.started_at is not None
    assert storage.jobs.claim_next(queues, W, 60).id == a.id
    assert storage.jobs.claim_next(queues, W, 60).id == b.id
    assert storage.jobs.claim_next(queues, W, 60) is None


def test_claim_next_only_listed_queues(storage):
    storage.jobs.create("finetune", {}, queue_class="training")
    assert storage.jobs.claim_next(["classify_sync", "classify"], W, 60) is None
    assert storage.jobs.claim_next(["training"], W, 60) is not None


def test_claim_next_respects_available_at(storage):
    job = storage.jobs.create("classify", {})
    claimed = storage.jobs.claim_next(["classify"], W, 60)
    assert storage.jobs.release_for_retry(job.id, claimed.attempts, 60, "БД недоступна")
    got = storage.jobs.get(job.id)
    assert got.status == STATUS_QUEUED and got.locked_until is None
    assert got.error == "БД недоступна"
    assert storage.jobs.claim_next(["classify"], W, 60) is None


def test_release_for_retry_zero_delay_is_reclaimed(storage):
    job = storage.jobs.create("classify", {})
    claimed = storage.jobs.claim_next(["classify"], W, 60)
    assert storage.jobs.release_for_retry(job.id, claimed.attempts + 1, 0, "x") is False
    assert storage.jobs.release_for_retry(job.id, claimed.attempts, 0, "x") is True
    again = storage.jobs.claim_next(["classify"], W, 60)
    assert again.id == job.id and again.attempts == 2


def test_expired_lease_is_reclaimed_with_attempts_plus_one(storage):
    job = storage.jobs.create("classify", {})
    first = storage.jobs.claim_next(["classify"], "w1:1", 0.05)
    assert first.attempts == 1
    assert storage.jobs.claim_next(["classify"], "w2:2", 0.05) is None  # аренда ещё действует
    time.sleep(0.2)
    second = storage.jobs.claim_next(["classify"], "w2:2", 60)
    assert second.id == job.id and second.attempts == 2 and second.worker_id == "w2:2"
    # прежний владелец больше не владеет задачей
    assert storage.jobs.finish(job.id, {}, None, attempts=1) is False
    assert storage.jobs.finish(job.id, {}, None, attempts=2) is True
    assert storage.jobs.get(job.id).locked_until is None


def test_training_expired_lease_fails_not_reclaimed(storage):
    job = storage.jobs.create("finetune", {}, queue_class="training")
    assert storage.jobs.claim_next(["training"], W, 0.05).id == job.id
    time.sleep(0.2)
    assert storage.jobs.claim_next(["training"], W, 60) is None
    lost = storage.jobs.fail_lost_training()
    assert [j.id for j in lost] == [job.id]
    got = storage.jobs.get(job.id)
    assert got.status == STATUS_ERROR
    assert "запустите дообучение заново" in got.error
    assert got.error == LOST_TRAINING_ERROR
    assert got.finished_at is not None and got.locked_until is None
    assert storage.jobs.fail_lost_training() == []


def test_fail_lost_training_ignores_live_lease(storage):
    storage.jobs.create("finetune", {}, queue_class="training")
    storage.jobs.claim_next(["training"], W, 60)
    assert storage.jobs.fail_lost_training() == []


def test_fail_lost_training_notifies_memory_listeners():
    from storage.memory import MemoryStorage

    st = MemoryStorage()
    seen = []
    st.jobs.add_listener(seen.append)
    job = st.jobs.create("finetune", {}, queue_class="training")
    st.jobs.claim_next(["training"], W, 0.01)
    time.sleep(0.05)
    st.jobs.fail_lost_training()
    assert seen == [job.id]


def test_extend_lease_only_for_owner(storage):
    job = storage.jobs.create("classify", {})
    claimed = storage.jobs.claim_next(["classify"], W, 0.05)
    assert storage.jobs.extend_lease(job.id, claimed.attempts + 1, 60) is False
    assert storage.jobs.extend_lease(job.id, claimed.attempts, 60) is True
    time.sleep(0.2)
    assert storage.jobs.claim_next(["classify"], W, 60) is None  # аренда продлена
    storage.jobs.finish(job.id, {}, None)
    assert storage.jobs.extend_lease(job.id, claimed.attempts, 60) is False  # не running


def test_mark_delivered_ignores_non_terminal(storage):
    job = storage.jobs.create("classify", {})
    storage.jobs.mark_delivered(job.id)
    assert storage.jobs.get(job.id).delivered_at is None
    storage.jobs.claim_next(["classify"], W, 60)
    storage.jobs.mark_delivered(job.id)
    assert storage.jobs.get(job.id).delivered_at is None
    storage.jobs.finish(job.id, {}, None)
    storage.jobs.mark_delivered(job.id)
    first = storage.jobs.get(job.id).delivered_at
    assert first is not None
    time.sleep(0.01)
    storage.jobs.mark_delivered(job.id)  # повторно — метка не сдвигается
    assert storage.jobs.get(job.id).delivered_at == first
    storage.jobs.mark_delivered("../../.env")  # мусорный id — без ошибки


def test_delete(storage):
    job = storage.jobs.create("embed_annotation", {})
    storage.jobs.delete(job.id)
    assert storage.jobs.get(job.id) is None
    storage.jobs.delete(job.id)  # повторно — без ошибки
    storage.jobs.delete("nope")


def test_cleanup_rules(storage):
    jobs = storage.jobs

    def done(job_type, queue, delivered):
        job = jobs.create(job_type, {}, queue_class=queue)
        jobs.finish(job.id, {}, None)
        if delivered:
            jobs.mark_delivered(job.id)
        return job.id

    delivered = done("classify", "classify", True)
    undelivered = done("classify", "classify", False)
    finetune = done("finetune", "training", True)
    waiting = jobs.create("classify", {}).id

    assert jobs.cleanup_expired(1, delivered_grace_sec=0.3) == 0  # всё свежее
    time.sleep(0.4)
    assert jobs.cleanup_expired(1, delivered_grace_sec=0.3) == 1
    assert jobs.get(delivered) is None
    assert jobs.get(undelivered) is not None
    assert jobs.get(finetune) is not None  # дообучение — только по TTL
    assert jobs.cleanup_expired(0.3 / 3600, delivered_grace_sec=600) == 2
    assert jobs.get(undelivered) is None and jobs.get(finetune) is None
    assert jobs.get(waiting) is not None


def test_cleanup_default_grace_from_config(storage, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "JOB_DELIVERED_GRACE_SEC", 0.0)
    job = storage.jobs.create("classify", {})
    storage.jobs.finish(job.id, {}, None)
    storage.jobs.mark_delivered(job.id)
    time.sleep(0.01)
    assert storage.jobs.cleanup_expired(24) == 1


def test_finish_clears_lock(storage):
    job = storage.jobs.create("classify", {})
    storage.jobs.claim_next(["classify"], W, 60)
    storage.jobs.finish(job.id, None, "boom")
    assert storage.jobs.get(job.id).locked_until is None


def test_live_workers_memory_registry():
    from storage.memory import MemoryStorage

    st = MemoryStorage()
    assert st.jobs.live_workers("classify") == []
    st.jobs.register_worker("classifier-worker:classify_sync,classify")
    st.jobs.register_worker("classifier-worker:training")
    assert st.jobs.live_workers("classify") == ["classifier-worker:classify_sync,classify"]
    assert st.jobs.live_workers("training") == ["classifier-worker:training"]
    st.jobs.unregister_worker("classifier-worker:training")
    assert st.jobs.live_workers("training") == []


def test_import_job_takes_queue_from_payload(storage):
    from datetime import datetime, timezone

    from storage.models import Job

    now = datetime.now(timezone.utc)
    storage.jobs.import_job(Job(
        id="e" * 32, job_type="finetune", status="done", appeal_id=None, document_id=None,
        webhook_url=None, payload={"queue": "training"}, result={}, error=None, attempts=1,
        created_at=now, started_at=now, finished_at=now))
    assert storage.jobs.get("e" * 32).queue == "training"


# --- только PostgreSQL ---

def _schema_and_url(pg_conn):
    import os

    schema = pg_conn.execute("SELECT current_schema()").fetchone()[0]
    return schema, os.environ["TEST_DATABASE_URL"]


@pytest.mark.db
def test_concurrent_claim_exactly_once(pg_conn):
    import psycopg

    from storage.pg import PgStorage

    schema, url = _schema_and_url(pg_conn)
    main = PgStorage(_connect_factory(pg_conn))
    ids = {main.jobs.create("classify", {}, queue_class="classify").id for _ in range(40)}
    taken: list[str] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(n):
        conn = psycopg.connect(url, autocommit=True, options=f"-c search_path={schema},public")
        try:
            st = PgStorage(_connect_factory(conn))
            while True:
                job = st.jobs.claim_next(["classify_sync", "classify"], f"t:{n}", 60)
                if job is None:
                    return
                with lock:
                    taken.append(job.id)
                st.jobs.finish(job.id, {}, None, attempts=job.attempts)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    assert sorted(taken) == sorted(ids)
    assert len(taken) == len(set(taken)) == 40


def _connect_factory(conn):
    @contextmanager
    def connect():
        with conn.transaction():
            yield conn
    return connect


@pytest.mark.db
def test_create_notifies_job_queued(pg_conn):
    import psycopg

    from storage.pg import PgStorage

    _, url = _schema_and_url(pg_conn)
    listener = psycopg.connect(url, autocommit=True)
    try:
        listener.execute("LISTEN job_queued")
        PgStorage(_connect_factory(pg_conn)).jobs.create("classify", {}, queue_class="classify")
        payloads = [n.payload for n in listener.notifies(timeout=5, stop_after=1)]
        assert payloads == ["classify"]
    finally:
        listener.close()


@pytest.mark.db
def test_live_workers_from_pg_stat_activity(pg_conn):
    import psycopg

    from storage.pg import PgStorage

    _, url = _schema_and_url(pg_conn)
    name = "classifier-worker:classify_sync,classify"
    worker = psycopg.connect(url, autocommit=True, application_name=name)
    try:
        jobs = PgStorage(_connect_factory(pg_conn)).jobs
        assert name in jobs.live_workers("classify")
        assert name in jobs.live_workers("classify_sync")
        assert name not in jobs.live_workers("training")
    finally:
        worker.close()


@pytest.mark.db
def test_schema_upgrade_existing_jobs(pg_conn):
    """Старая форма jobs (без новых колонок): схема добавляет их и берёт queue из payload."""
    from storage import db

    pg_conn.execute("DROP TABLE jobs")
    pg_conn.execute(
        """CREATE TABLE jobs (
             id CHAR(32) PRIMARY KEY,
             job_type TEXT NOT NULL CHECK (job_type IN ('classify','search','finetune','embed_annotation')),
             status TEXT NOT NULL CHECK (status IN ('queued','running','done','error')),
             appeal_id UUID REFERENCES appeal_classifications(id) ON DELETE SET NULL,
             document_id BIGINT, webhook_url TEXT, payload JSONB, result JSONB, error TEXT,
             attempts INT NOT NULL DEFAULT 0,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ)""")
    rows = (("a" * 32, "finetune", '{"queue": "training"}'),
            ("b" * 32, "search", '{"queue": "classify_sync"}'),
            ("c" * 32, "classify", '{}'),
            ("d" * 32, "classify", '{"queue": "bogus"}'))
    for jid, jt, payload in rows:
        pg_conn.execute(
            "INSERT INTO jobs (id, job_type, status, payload) VALUES (%s, %s, 'done', %s)",
            (jid, jt, payload))
    db.apply_schema(pg_conn)
    db.apply_schema(pg_conn)  # повторно — без изменений
    got = dict(pg_conn.execute("SELECT id, queue FROM jobs").fetchall())
    assert got == {"a" * 32: "training", "b" * 32: "classify_sync",
                   "c" * 32: "classify", "d" * 32: "classify"}
    assert pg_conn.execute(
        "SELECT count(*) FROM jobs WHERE available_at IS NULL").fetchone()[0] == 0
    assert pg_conn.execute(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'jobs_queue_ready' "
        "AND schemaname = current_schema()").fetchone()[0] == 1
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        pg_conn.execute(
            "INSERT INTO jobs (id, job_type, status, queue) VALUES (%s, 'classify', 'queued', 'x')",
            ("f" * 32,))

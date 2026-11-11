"""JobStore: память и PostgreSQL (замена файловой очереди job_queue)."""

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from storage.models import (STATUS_DONE, STATUS_ERROR, STATUS_QUEUED, STATUS_RUNNING,
                            Job, QueueFull)

BAD_IDS = ["../../.env", "..\\..\\.env", "0000000000000000000000000000000g",
           "0" * 31, "", "nope", "нет-такого"]


def test_create_returns_queued_job(storage):
    job = storage.jobs.create("classify", {"text": "т"}, document_id=7,
                              webhook_url="https://x/y")
    assert len(job.id) == 32
    assert job.status == STATUS_QUEUED
    assert job.attempts == 0
    assert job.payload["text"] == "т"
    assert job.payload["queue"] == "classify"
    assert job.document_id == 7 and job.webhook_url == "https://x/y"
    assert job.created_at.tzinfo is not None
    got = storage.jobs.get(job.id)
    assert got.id == job.id and got.payload == job.payload


def test_create_with_appeal_id(storage):
    aid = storage.appeals.create("текст")
    job = storage.jobs.create("classify", {}, appeal_id=aid)
    assert storage.jobs.get(job.id).appeal_id == aid


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_bad_id_returns_none(storage, bad_id):
    assert storage.jobs.get(bad_id) is None
    assert storage.jobs.position(bad_id) is None
    assert storage.jobs.claim(bad_id) is None


def test_unknown_valid_id_returns_none(storage):
    assert storage.jobs.get("a" * 32) is None
    assert storage.jobs.claim("a" * 32) is None


def test_queue_full_raises(storage):
    for _ in range(2):
        storage.jobs.create("classify", {}, max_queued=2)
    with pytest.raises(QueueFull):
        storage.jobs.create("classify", {}, max_queued=2)
    assert storage.jobs.stats()["queued"] == 2


def test_running_jobs_not_counted_in_limit(storage):
    first = storage.jobs.create("classify", {}, max_queued=1)
    storage.jobs.claim(first.id)
    storage.jobs.create("classify", {}, max_queued=1)
    with pytest.raises(QueueFull):
        storage.jobs.create("classify", {}, max_queued=1)


def test_sync_queue_not_counted_in_limit(storage):
    for _ in range(3):
        storage.jobs.create("classify", {}, queue_class="classify_sync")
    storage.jobs.create("classify", {}, max_queued=2)
    storage.jobs.create("classify", {}, max_queued=2)
    with pytest.raises(QueueFull):
        storage.jobs.create("classify", {}, max_queued=2)
    # sync-очередь считается отдельно
    with pytest.raises(QueueFull):
        storage.jobs.create("classify", {}, queue_class="classify_sync", max_queued=3)


def test_other_job_type_not_counted_in_limit(storage):
    storage.jobs.create("search", {}, max_queued=1)
    storage.jobs.create("classify", {}, max_queued=1)


def test_position_counts_from_one_in_creation_order(storage):
    a = storage.jobs.create("classify", {})
    b = storage.jobs.create("classify", {})
    c = storage.jobs.create("classify", {})
    other = storage.jobs.create("classify", {}, queue_class="classify_sync")
    assert [storage.jobs.position(j.id) for j in (a, b, c)] == [1, 2, 3]
    assert storage.jobs.position(other.id) == 1
    storage.jobs.claim(a.id)
    assert storage.jobs.position(a.id) is None
    assert storage.jobs.position(b.id) == 1
    assert storage.jobs.position(c.id) == 2


def test_claim_sets_running_and_counts_attempts(storage):
    job = storage.jobs.create("classify", {})
    claimed = storage.jobs.claim(job.id)
    assert claimed.status == STATUS_RUNNING
    assert claimed.attempts == 1
    assert claimed.started_at is not None
    again = storage.jobs.claim(job.id)  # повторная доставка из брокера
    assert again.status == STATUS_RUNNING and again.attempts == 2


def test_claim_done_job_returns_none(storage):
    job = storage.jobs.create("classify", {})
    storage.jobs.claim(job.id)
    storage.jobs.finish(job.id, {"ok": 1}, None)
    assert storage.jobs.claim(job.id) is None
    assert storage.jobs.get(job.id).attempts == 1


def test_finish_done_and_error(storage):
    ok = storage.jobs.create("classify", {})
    bad = storage.jobs.create("classify", {})
    assert storage.jobs.finish(ok.id, {"code": "1"}, None) is True
    assert storage.jobs.finish(bad.id, None, "RuntimeError: боль") is True
    got = storage.jobs.get(ok.id)
    assert got.status == STATUS_DONE and got.result == {"code": "1"}
    assert got.error is None and got.finished_at is not None
    got = storage.jobs.get(bad.id)
    assert got.status == STATUS_ERROR and got.result is None
    assert "боль" in got.error


def test_finish_does_not_overwrite_finished_job(storage):
    job = storage.jobs.create("classify", {})
    assert storage.jobs.finish(job.id, {"v": 1}, None) is True
    assert storage.jobs.finish(job.id, None, "позже") is False
    got = storage.jobs.get(job.id)
    assert got.status == STATUS_DONE and got.result == {"v": 1}
    assert storage.jobs.finish("b" * 32, {}, None) is False


def test_finish_notifies_memory():
    from storage.memory import MemoryStorage

    st = MemoryStorage()
    seen = []
    st.jobs.add_listener(seen.append)
    job = st.jobs.create("classify", {})
    st.jobs.finish(job.id, {}, None)
    assert seen == [job.id]
    st.jobs.finish(job.id, {}, None)  # no-op, без повторного уведомления
    assert seen == [job.id]
    st.jobs.remove_listener(seen.append)
    other = st.jobs.create("classify", {})
    st.jobs.finish(other.id, None, "e")
    assert seen == [job.id]


def test_raising_listener_does_not_break_finish():
    from storage.memory import MemoryStorage

    st = MemoryStorage()
    seen = []

    def bad(_job_id):
        raise RuntimeError("сбой слушателя")

    st.jobs.add_listener(bad)
    st.jobs.add_listener(seen.append)
    job = st.jobs.create("classify", {})
    assert st.jobs.finish(job.id, {"ok": 1}, None) is True
    assert seen == [job.id]
    assert st.jobs.get(job.id).status == STATUS_DONE


@pytest.mark.db
def test_finish_notifies_pg(pg_conn):
    import psycopg

    import config
    from storage.pg import PgStorage

    url = os.getenv("TEST_DATABASE_URL") or config.DATABASE_URL

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    store = PgStorage(connect).jobs
    job = store.create("classify", {})
    with psycopg.connect(url, autocommit=True) as listener:
        listener.execute("LISTEN job_done")
        store.finish(job.id, {"ok": True}, None)
        got = [n.payload for n in listener.notifies(timeout=2, stop_after=1)]
    assert got == [job.id]


def test_list_stats_latest_has_active(storage):
    assert storage.jobs.latest("classify") is None
    assert storage.jobs.has_active("classify") is False
    a = storage.jobs.create("classify", {})
    b = storage.jobs.create("search", {})
    c = storage.jobs.create("classify", {})
    assert storage.jobs.has_active("classify") is True
    assert storage.jobs.latest("classify").id == c.id
    assert [j.id for j in storage.jobs.list()] == [c.id, b.id, a.id]
    assert [j.id for j in storage.jobs.list(job_type="classify")] == [c.id, a.id]
    assert len(storage.jobs.list(limit=1)) == 1
    storage.jobs.claim(a.id)
    storage.jobs.finish(a.id, {}, None)
    storage.jobs.finish(c.id, None, "e")
    assert storage.jobs.has_active("classify") is False
    assert storage.jobs.stats() == {"queued": 1, "running": 0, "done": 1, "error": 1}


def test_cleanup_expired_removes_only_finished(storage):
    old = storage.jobs.create("classify", {})
    storage.jobs.finish(old.id, {}, None)
    waiting = storage.jobs.create("classify", {})
    assert storage.jobs.cleanup_expired(24) == 0
    assert storage.jobs.cleanup_expired(0.0) == 1
    assert storage.jobs.get(old.id) is None
    assert storage.jobs.get(waiting.id) is not None


def test_import_job_is_idempotent(storage):
    now = datetime.now(timezone.utc)
    job = Job(id="c" * 32, job_type="classify", status=STATUS_DONE, appeal_id=None,
              document_id=5, webhook_url=None, payload={"queue": "classify"},
              result={"a": 1}, error=None, attempts=1,
              created_at=now - timedelta(minutes=2), started_at=now - timedelta(minutes=1),
              finished_at=now)
    storage.jobs.import_job(job)
    other = Job(**{**job.__dict__, "result": {"a": 2}, "status": STATUS_ERROR})
    storage.jobs.import_job(other)
    got = storage.jobs.get(job.id)
    assert got.status == STATUS_DONE and got.result == {"a": 1} and got.document_id == 5


def test_elapsed_sec_rule():
    now = datetime.now(timezone.utc)
    done = Job(id="d" * 32, job_type="classify", status=STATUS_DONE, appeal_id=None,
               document_id=None, webhook_url=None, payload={}, result=None, error=None,
               attempts=1, created_at=now - timedelta(seconds=30),
               started_at=now - timedelta(seconds=10), finished_at=now)
    assert done.elapsed_sec == 10.0
    waiting = Job(**{**done.__dict__, "started_at": None, "finished_at": None,
                     "created_at": now - timedelta(seconds=5)})
    assert 4.9 <= waiting.elapsed_sec <= 6.0

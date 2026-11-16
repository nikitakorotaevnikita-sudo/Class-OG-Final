"""Воркер очереди в PostgreSQL (worker.run): цикл на MemoryStorage с фейковым агентом."""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from core import config
from fake_agent import CODE, FakeAgent
from queueing import tasks
from storage.memory import MemoryStorage
from storage.models import LOST_TRAINING_ERROR, ModelVersion
from worker import run as worker_run

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CLASSIFY_QUEUES = ["classify_sync", "classify"]


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def hooks(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks.webhook, "deliver",
                        lambda url, payload: calls.append((url, payload)) or True)
    return calls


def _worker(storage, agent=None, queues=None, factory=None, **kw):
    kw.setdefault("poll_sec", 0.1)
    kw.setdefault("lease_sec", 5)
    kw.setdefault("listen_dsn", None)
    if factory is None:
        factory = lambda: agent  # noqa: E731
    return worker_run.Worker(storage, queues or CLASSIFY_QUEUES, "host:1", factory, **kw)


def _classify_job(storage, queue="classify", webhook_url=None, payload=None):
    aid = storage.appeals.create("текст обращения")
    return storage.jobs.create("classify", payload or {}, appeal_id=aid,
                               webhook_url=webhook_url, queue_class=queue)


def test_run_once_processes_classify_and_writes_result(storage, hooks):
    job = _classify_job(storage, payload={"client_appeal_id": "RX-1"})
    agent = FakeAgent(storage)

    assert _worker(storage, agent).run_once() is True

    done = storage.jobs.get(job.id)
    assert done.status == "done" and done.error is None and done.attempts == 1
    assert done.worker_id == "host:1" and done.locked_until is None
    assert done.result["appeal_id"] == "RX-1"
    assert [q["code"] for q in done.result["questions"]] == [CODE]
    assert len(agent.calls) == 1
    assert _worker(storage, agent).run_once() is False  # очередь пуста


def test_sync_queue_first(storage, hooks):
    slow = _classify_job(storage, queue="classify")
    search = storage.jobs.create("search", {"q": "мусор", "top_k": 2},
                                 queue_class="classify_sync")

    assert _worker(storage, FakeAgent(storage)).run_once() is True

    assert storage.jobs.get(search.id).status == "done"
    assert storage.jobs.get(search.id).result["query"] == "мусор"
    assert storage.jobs.get(slow.id).status == "queued"


def test_worker_takes_only_its_queues(storage, hooks):
    ft = storage.jobs.create("finetune", {}, queue_class="training")
    assert _worker(storage, FakeAgent(storage)).run_once() is False
    assert storage.jobs.get(ft.id).status == "queued"


@pytest.mark.parametrize("exc", [
    psycopg.OperationalError("connection lost"),
    psycopg.InterfaceError("closed"),
    ConnectionError("reset"),
])
def test_infra_error_releases_for_retry_with_backoff(storage, hooks, monkeypatch, exc):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 2)
    job = _classify_job(storage, webhook_url="http://rx.local/hook")
    agent = FakeAgent(storage, error=exc)

    before = datetime.now(timezone.utc)
    assert _worker(storage, agent).run_once() is True

    queued = storage.jobs.get(job.id)
    assert queued.status == "queued" and queued.attempts == 1
    assert queued.locked_until is None
    assert before + timedelta(seconds=4) <= queued.available_at
    assert queued.available_at <= datetime.now(timezone.utc) + timedelta(seconds=6)
    assert type(exc).__name__ in queued.error
    assert hooks == []
    # пауза ещё не прошла — задачу не берём
    assert _worker(storage, agent).run_once() is False


def test_infra_error_on_last_attempt_finishes_with_error(storage, hooks, monkeypatch):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 1)
    job = _classify_job(storage, webhook_url="http://rx.local/hook")

    _worker(storage, FakeAgent(storage, error=psycopg.OperationalError("db down"))).run_once()

    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and failed.error == "OperationalError: db down"
    assert len(hooks) == 1 and hooks[0][1]["status"] == "error"


def test_retry_delay_grows_and_is_capped():
    assert [worker_run.retry_delay(n) for n in (1, 2, 3, 4, 5, 6, 10)] == [
        5, 10, 20, 40, 60, 60, 60]


def test_attempts_cap_finishes_with_error_without_work(storage, hooks, monkeypatch):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 1)
    job = _classify_job(storage, webhook_url="http://rx.local/hook")
    # Первая попытка: воркер взял задачу и умер (аренда уже истекла).
    storage.jobs.claim_next(["classify"], "dead:1", -1)
    agent = FakeAgent(storage)

    assert _worker(storage, agent).run_once() is True

    assert agent.calls == []
    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and failed.attempts == 2
    assert "Превышено число попыток" in failed.error
    assert len(hooks) == 1 and hooks[0][1]["status"] == "error"


def test_embed_annotation_row_deleted_after_success(storage, hooks):
    storage.models.register(ModelVersion(0, "base", "m0", True))
    vid = storage.classifier.add_annotation(CODE, "пояснение", 0)
    job = storage.jobs.create("embed_annotation", {"vector_id": vid})

    assert _worker(storage, FakeAgent(storage)).run_once() is True

    assert storage.jobs.get(job.id) is None
    assert storage.classifier.pending_annotations(0) == []


def test_embed_annotation_error_row_kept(storage, hooks):
    job = storage.jobs.create("embed_annotation", {"vector_id": 999})

    _worker(storage, FakeAgent(storage)).run_once()

    failed = storage.jobs.get(job.id)
    assert failed is not None and failed.status == "error" and "999" in failed.error


@pytest.mark.parametrize("delivered", [True, False])
def test_webhook_success_marks_delivered(storage, monkeypatch, delivered):
    calls = []
    monkeypatch.setattr(tasks.webhook, "deliver",
                        lambda url, payload: calls.append(payload) or delivered)
    job = _classify_job(storage, webhook_url="http://rx.local/hook")

    _worker(storage, FakeAgent(storage)).run_once()

    assert len(calls) == 1 and calls[0]["status"] == "done"
    assert (storage.jobs.get(job.id).delivered_at is not None) is delivered


def test_lease_extended_during_long_task(storage, hooks):
    job = _classify_job(storage)
    slow = FakeAgent(storage, delay=1.0)
    other = FakeAgent(storage)
    first = _worker(storage, slow, lease_sec=0.3)
    second = worker_run.Worker(storage, CLASSIFY_QUEUES, "host:2", lambda: other,
                               poll_sec=0.1, lease_sec=0.3, listen_dsn=None)

    t = threading.Thread(target=first.run_once)
    t.start()
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and t.is_alive():
            assert second.run_once() is False
            time.sleep(0.05)
    finally:
        t.join(5)

    done = storage.jobs.get(job.id)
    assert done.status == "done" and done.attempts == 1 and done.worker_id == "host:1"
    assert other.calls == []


def test_poll_picks_up_without_notify(storage, hooks):
    worker = _worker(storage, FakeAgent(storage), poll_sec=0.1)
    stop = threading.Event()
    t = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    t.start()
    try:
        time.sleep(0.2)
        job = _classify_job(storage)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and storage.jobs.get(job.id).status != "done":
            time.sleep(0.02)
        assert storage.jobs.get(job.id).status == "done"
    finally:
        stop.set()
        t.join(3)
    assert not t.is_alive()


def test_run_forever_registers_worker_in_memory(storage):
    worker = _worker(storage, FakeAgent(storage))
    stop = threading.Event()
    t = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not storage.jobs.live_workers("classify"):
            time.sleep(0.02)
        assert storage.jobs.live_workers("classify") == ["classifier-worker:classify_sync,classify"]
    finally:
        stop.set()
        t.join(3)
    assert storage.jobs.live_workers("classify") == []


def test_no_agent_does_not_claim(storage, hooks):
    job = _classify_job(storage)
    built = []

    def broken():
        built.append(1)
        raise RuntimeError("Нет активной модели в ml_model_registry")

    worker = _worker(storage, factory=broken)
    assert worker.run_once() is False
    assert worker.run_once() is False

    still = storage.jobs.get(job.id)
    assert still.status == "queued" and still.attempts == 0 and still.error is None
    assert len(built) == 2  # неудачный агент не кэшируется — следующая попытка строит заново


def test_training_worker_needs_no_agent(storage, hooks, monkeypatch):
    from training import finetune_model

    job = storage.jobs.create("finetune", {"source": "verified", "force": True},
                              queue_class="training")
    seen = {}

    def fake_training(st, source="combined", force=False, still_owner=None):
        seen.update(source=source, force=force, owner=still_owner())
        return {"status": "completed", "message": "ok"}

    monkeypatch.setattr(finetune_model, "run_training", fake_training)

    def factory():
        raise AssertionError("воркеру дообучения агент не нужен")

    assert _worker(storage, queues=["training"], factory=factory).run_once() is True

    done = storage.jobs.get(job.id)
    assert done.status == "done" and done.result["status"] == "completed"
    assert seen == {"source": "verified", "force": True, "owner": True}


def test_lost_training_failed_with_webhook(storage, hooks):
    job = storage.jobs.create("finetune", {}, queue_class="training",
                              webhook_url="http://rx.local/hook")
    storage.jobs.claim_next(["training"], "dead:1", -1)  # воркер дообучения пропал

    assert _worker(storage, queues=["training"]).run_once() is False

    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and failed.error == LOST_TRAINING_ERROR
    assert len(hooks) == 1 and hooks[0][1]["status"] == "error"


def test_cleanup_runs_between_jobs_at_most_once_a_minute(storage, hooks, monkeypatch):
    monkeypatch.setattr(config, "JOB_DELIVERED_GRACE_SEC", 0)
    calls = []
    real = storage.jobs.cleanup_expired
    monkeypatch.setattr(storage.jobs, "cleanup_expired",
                        lambda *a, **kw: calls.append(1) or real(*a, **kw))
    job = _classify_job(storage)
    worker = _worker(storage, FakeAgent(storage))
    worker.run_once()
    storage.jobs.mark_delivered(job.id)

    worker.run_once()
    assert len(calls) == 1  # вторая чистка — не раньше чем через минуту
    assert storage.jobs.get(job.id) is not None

    worker._next_cleanup = 0.0
    worker.run_once()
    assert len(calls) == 2
    assert storage.jobs.get(job.id) is None


def test_parse_queues_rejects_unknown():
    assert worker_run.parse_queues("classify_sync, classify") == ["classify_sync", "classify"]
    with pytest.raises(ValueError):
        worker_run.parse_queues("classify,bogus")
    with pytest.raises(ValueError):
        worker_run.parse_queues(" , ")


def test_application_name():
    assert worker_run.application_name(["classify_sync", "classify"]) == \
        "classifier-worker:classify_sync,classify"


def test_worker_module_does_not_import_celery():
    code = ("import sys, worker.run; "
            "bad = [m for m in ('celery', 'kombu', 'classifier.classifier_agent', 'torch') "
            "if m in sys.modules]; print('BAD=' + repr(bad)); sys.exit(1 if bad else 0)")
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("how", ["module", "script"])
def test_entry_point_help(how):
    env = {**os.environ, "PYTHONUTF8": "1"}
    if how == "module":
        env["PYTHONPATH"] = str(SRC)
        cmd = [sys.executable, "-m", "worker.run", "--help"]
    else:
        env["PYTHONPATH"] = ""
        cmd = [sys.executable, str(SRC / "worker" / "run.py"), "--help"]
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "--queues" in proc.stdout

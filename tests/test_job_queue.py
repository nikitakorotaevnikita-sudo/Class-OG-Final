"""Очередь фоновых задач: состояния, ошибки, переживание перезапуска."""

import sys
import threading
import time

sys.path.insert(0, "src")

import pytest
from job_queue import (STATUS_DONE, STATUS_ERROR, STATUS_QUEUED, STATUS_RUNNING,
                       JobQueue, QueueFull)


def wait_for(predicate, timeout=5.0, step=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def jobs(tmp_path):
    return JobQueue(jobs_dir=tmp_path / "jobs", ttl_hours=24, max_queued=5)


def test_submit_returns_queued_job_immediately(jobs):
    """Смысл очереди: вызывающая сторона не ждёт выполнения."""
    started = time.time()
    job = jobs.submit(lambda: {"ok": True})
    assert job.status in (STATUS_QUEUED, STATUS_RUNNING)
    assert time.time() - started < 1.0
    assert wait_for(lambda: jobs.get(job.id).status == STATUS_DONE)
    assert jobs.get(job.id).result == {"ok": True}


def test_error_is_recorded_with_type_and_message(jobs):
    job = jobs.submit(lambda: (_ for _ in ()).throw(RuntimeError("модель недоступна")))
    assert wait_for(lambda: jobs.get(job.id).status == STATUS_ERROR)
    stored = jobs.get(job.id)
    assert stored.result is None
    assert "RuntimeError" in stored.error
    assert "модель недоступна" in stored.error


def test_one_failure_does_not_stop_the_worker(jobs):
    """Упавшая задача не должна вешать обработку остальных."""
    bad = jobs.submit(lambda: (_ for _ in ()).throw(ValueError("боль")))
    good = jobs.submit(lambda: {"n": 42})
    assert wait_for(lambda: jobs.get(good.id).status == STATUS_DONE)
    assert jobs.get(bad.id).status == STATUS_ERROR


def test_tasks_run_one_at_a_time(jobs):
    """Пайплайн последовательный — параллельного выполнения быть не должно."""
    concurrent = []
    running = {"n": 0}

    def slow():
        running["n"] += 1
        concurrent.append(running["n"])
        time.sleep(0.15)
        running["n"] -= 1
        return {}

    ids = [jobs.submit(slow).id for _ in range(3)]
    assert wait_for(lambda: all(jobs.get(i).status == STATUS_DONE for i in ids), timeout=10)
    assert max(concurrent) == 1


def test_queue_position_counts_from_one(jobs):
    blocker = jobs.submit(lambda: time.sleep(0.4) or {})
    waiting = [jobs.submit(lambda: {}) for _ in range(2)]
    positions = [jobs.position(j.id) for j in waiting]
    # Первая задача уже выполняется, поэтому у ожидающих позиции 1 и 2.
    assert positions == [1, 2]
    assert wait_for(lambda: jobs.get(waiting[-1].id).status == STATUS_DONE, timeout=10)
    assert jobs.position(blocker.id) is None


def test_queue_full_raises_instead_of_growing(tmp_path):
    """Лимит считается по ждущим задачам, поэтому воркер держим занятым явно.

    Без явного удержания тест зависел бы от того, успел ли воркер забрать первую
    задачу: лимит упирался бы то на третьем, то на четвёртом вызове.
    """
    jobs = JobQueue(jobs_dir=tmp_path / "jobs", max_queued=2)
    gate = threading.Event()

    busy = jobs.submit(lambda: gate.wait(10) or {})
    assert wait_for(lambda: jobs.get(busy.id).status == STATUS_RUNNING)

    jobs.submit(lambda: {})
    jobs.submit(lambda: {})
    with pytest.raises(QueueFull):
        jobs.submit(lambda: {})

    gate.set()


def test_result_survives_restart(tmp_path):
    first = JobQueue(jobs_dir=tmp_path / "jobs")
    job = first.submit(lambda: {"code": "0005.0005.0056.1160"})
    assert wait_for(lambda: first.get(job.id).status == STATUS_DONE)

    # Новый экземпляр — как после перезапуска сервиса.
    second = JobQueue(jobs_dir=tmp_path / "jobs")
    restored = second.get(job.id)
    assert restored is not None
    assert restored.status == STATUS_DONE
    assert restored.result == {"code": "0005.0005.0056.1160"}


def test_unfinished_job_becomes_error_after_restart(tmp_path):
    """Функцию обработки восстановить нечем — честнее сказать об этом сразу."""
    first = JobQueue(jobs_dir=tmp_path / "jobs")
    job = first.submit(lambda: time.sleep(30) or {})
    assert wait_for(lambda: first.get(job.id).status == STATUS_RUNNING)

    second = JobQueue(jobs_dir=tmp_path / "jobs")
    restored = second.get(job.id)
    assert restored.status == STATUS_ERROR
    assert "перезапущен" in restored.error


def test_expired_jobs_are_removed(tmp_path):
    jobs = JobQueue(jobs_dir=tmp_path / "jobs", ttl_hours=0.0)
    old = jobs.submit(lambda: {})
    assert wait_for(lambda: jobs.get(old.id).status == STATUS_DONE)
    # Следующая постановка запускает уборку просроченных.
    jobs.submit(lambda: {})
    assert wait_for(lambda: jobs.get(old.id) is None)
    assert not (tmp_path / "jobs" / f"{old.id}.json").exists()


def test_stats_reports_worker_state(jobs):
    job = jobs.submit(lambda: {})
    assert wait_for(lambda: jobs.get(job.id).status == STATUS_DONE)
    stats = jobs.stats()
    assert stats["done"] == 1
    assert stats["worker_alive"] is True


def test_unknown_job_id_returns_none(jobs):
    assert jobs.get("нет-такого") is None
    assert jobs.position("нет-такого") is None

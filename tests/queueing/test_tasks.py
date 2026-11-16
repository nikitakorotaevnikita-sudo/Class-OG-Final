"""Задачи воркера: run_*(job, storage, agent) над уже захваченной задачей (без модели)."""
from contextlib import contextmanager

import psycopg
import pytest

from core import config
from fake_agent import CODE, CODE2, FakeAgent
from queueing import tasks
from storage.memory import MemoryStorage


def _enqueue(storage, payload=None, webhook_url=None, document_id=None, text="текст обращения"):
    """Задача classify, уже захваченная воркером (как после claim_next)."""
    aid = storage.appeals.create(text, document_id=document_id)
    job = storage.jobs.create("classify", payload or {}, appeal_id=aid,
                              document_id=document_id, webhook_url=webhook_url)
    return storage.jobs.claim(job.id)


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def no_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks.webhook, "deliver", lambda url, payload: calls.append((url, payload)) or True)
    return calls


def _questions(storage, aid):
    [entry] = [e for e in storage.appeals.as_log_entries() if e["id"] == aid]
    return entry["agent_questions"]


def test_classify_success_writes_result_and_notifies(storage, no_webhook):
    notified = []
    storage.jobs.add_listener(notified.append)
    job = _enqueue(storage, {"llm_provider": "groq", "llm_model": "llama",
                             "client_appeal_id": "RX-1", "was_truncated": True})
    agent = FakeAgent(storage)

    assert tasks.run_classify_appeal(job, storage, agent) is True

    done = storage.jobs.get(job.id)
    assert done.status == "done" and done.error is None
    assert done.result["appeal_id"] == "RX-1"
    assert done.result["was_truncated"] is True
    assert done.result["operator_card"] == "карточка"
    assert [q["code"] for q in done.result["questions"]] == [CODE]
    assert notified == [job.id]
    [call] = agent.calls
    assert call["text"] == "текст обращения"
    assert (call["llm_provider"], call["llm_model"]) == ("groq", "llama")
    assert call["appeal_id"] == job.appeal_id
    assert [q["selected_code"] for q in _questions(storage, job.appeal_id)] == [CODE]


def test_classify_llm_error_marks_error(storage, no_webhook):
    notified = []
    storage.jobs.add_listener(notified.append)
    job = _enqueue(storage)

    tasks.run_classify_appeal(job, storage, FakeAgent(storage, error=RuntimeError("LLM недоступна")))

    failed = storage.jobs.get(job.id)
    assert failed.status == "error"
    assert failed.error == "RuntimeError: LLM недоступна"
    assert failed.result is None
    assert notified == [job.id]


def test_classify_missing_text_marks_error(storage, no_webhook):
    created = storage.jobs.create("classify", {},
                                  appeal_id="11111111-2222-3333-4444-555555555555")
    job = storage.jobs.claim(created.id)
    agent = FakeAgent(storage)

    tasks.run_classify_appeal(job, storage, agent)

    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and "11111111-2222-3333-4444-555555555555" in failed.error
    assert agent.calls == []


def test_rerun_replaces_questions(storage, no_webhook):
    job = _enqueue(storage)
    # Первый прогон записал карточку, но воркер упал до finish (задача осталась running).
    FakeAgent(storage, codes=(CODE, CODE2)).classify("текст обращения", appeal_id=job.appeal_id)
    assert len(_questions(storage, job.appeal_id)) == 2
    retry = storage.jobs.claim(job.id)  # аренда истекла, задачу взяли заново

    tasks.run_classify_appeal(retry, storage, FakeAgent(storage, codes=(CODE2,)))

    assert [q["selected_code"] for q in _questions(storage, job.appeal_id)] == [CODE2]
    assert storage.jobs.get(job.id).status == "done"


@pytest.mark.parametrize("exc", [
    psycopg.OperationalError("connection lost"),
    psycopg.InterfaceError("closed"),
    ConnectionError("reset"),
])
def test_infra_error_reraised_until_max_attempts(storage, no_webhook, monkeypatch, exc):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 2)
    job = _enqueue(storage, webhook_url="http://rx.local/hook")
    agent = FakeAgent(storage, error=exc)

    with pytest.raises(tasks.RetryableError):
        tasks.run_classify_appeal(job, storage, agent)
    running = storage.jobs.get(job.id)
    assert running.status == "running" and running.attempts == 1
    assert no_webhook == []

    second = storage.jobs.claim(job.id)  # вторая попытка — последняя
    tasks.run_classify_appeal(second, storage, agent)

    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and failed.attempts == 2
    assert failed.error == f"{type(exc).__name__}: {exc}"
    assert len(no_webhook) == 1 and no_webhook[0][1]["status"] == "error"


def test_infra_errors_do_not_mention_broker():
    assert set(tasks.INFRA_ERRORS) == {psycopg.OperationalError, psycopg.InterfaceError,
                                       ConnectionError}


def test_webhook_sent_after_finish(storage, no_webhook):
    job = _enqueue(storage, {"response_kind": "document"}, webhook_url="http://rx.local/hook",
                   document_id=42)

    tasks.run_classify_appeal(job, storage, FakeAgent(storage))

    [(url, payload)] = no_webhook
    assert url == "http://rx.local/hook"
    assert payload["status"] == "done"
    assert payload["job_id"] == job.id
    assert payload["document_id"] == 42
    assert payload["error"] is None
    assert payload["result"]["document_id"] == 42
    assert payload["result"]["questions"] == [{"code": CODE, "question": "Вывоз мусора"}]
    assert storage.jobs.get(job.id).delivered_at is not None


def test_webhook_failure_leaves_job_undelivered(storage, monkeypatch):
    monkeypatch.setattr(tasks.webhook, "deliver", lambda url, payload: False)
    job = _enqueue(storage, webhook_url="http://rx.local/hook")

    tasks.run_classify_appeal(job, storage, FakeAgent(storage))

    done = storage.jobs.get(job.id)
    assert done.status == "done" and done.delivered_at is None


def test_webhook_not_sent_when_finish_does_not_transition(storage, no_webhook, monkeypatch):
    job = _enqueue(storage, webhook_url="http://rx.local/hook")
    monkeypatch.setattr(storage.jobs, "finish", lambda *a, **kw: False)

    tasks.run_classify_appeal(job, storage, FakeAgent(storage))

    assert no_webhook == []


def test_document_response_kind_uses_build_document_response(storage, no_webhook):
    job = _enqueue(storage, {"response_kind": "document"}, document_id=7)

    tasks.run_classify_appeal(job, storage, FakeAgent(storage, codes=(CODE, CODE)))

    result = storage.jobs.get(job.id).result
    assert set(result) == {"document_id", "applicant_fio", "applicant_email", "summary",
                           "reasoning", "questions"}
    assert result["document_id"] == 7
    assert result["questions"] == [{"code": CODE, "question": "Вывоз мусора"}]  # дубли схлопнуты


def test_search_classifier_finishes_with_results(storage):
    created = storage.jobs.create("search", {"q": "мусор", "top_k": 3},
                                  queue_class="classify_sync")
    job = storage.jobs.claim(created.id)

    tasks.run_search_classifier(job, storage, FakeAgent(storage))

    done = storage.jobs.get(job.id)
    assert done.status == "done"
    assert done.result["query"] == "мусор"
    assert [r["code"] for r in done.result["results"]] == [
        "0000.0000.0000.0000", "0001.0000.0000.0000", "0002.0000.0000.0000"]


def test_embed_annotation_sets_embedding(storage):
    from storage.models import ModelVersion

    storage.models.register(ModelVersion(0, "base", "m0", True))
    vid = storage.classifier.add_annotation(CODE, "пояснение", 0)
    job = storage.jobs.claim(storage.jobs.create("embed_annotation", {"vector_id": vid}).id)

    tasks.run_embed_annotation(job, storage, FakeAgent(storage))

    assert storage.jobs.get(job.id).status == "done"
    assert storage.classifier.pending_annotations(0) == []


def test_embed_annotation_missing_vector_marks_error(storage):
    job = storage.jobs.claim(storage.jobs.create("embed_annotation", {"vector_id": 999}).id)

    tasks.run_embed_annotation(job, storage, FakeAgent(storage))

    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and "999" in failed.error


def test_tasks_module_has_no_celery_wrappers():
    for name in ("classify_appeal", "search_classifier", "embed_annotation", "finetune",
                 "INFRA_MAX_RETRIES"):
        assert not hasattr(tasks, name), name


@pytest.mark.db
def test_card_and_job_status_commit_atomically(pg_conn, monkeypatch, no_webhook):
    """finish падает внутри транзакции → карточка не сохранена, задача не done."""
    from storage.pg import PgStorage

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    storage = PgStorage(connect)
    job = _enqueue(storage)
    real_finish = storage.jobs.finish

    def finish(job_id, result, error, conn=None, attempts=None):
        if error is None:  # успешное завершение в транзакции классификации
            raise RuntimeError("сбой записи статуса")
        return real_finish(job_id, result, error, conn=conn, attempts=attempts)

    monkeypatch.setattr(storage.jobs, "finish", finish)

    tasks.run_classify_appeal(job, storage, FakeAgent(storage))

    n = pg_conn.execute("SELECT count(*) FROM appeal_questions WHERE appeal_id = %s",
                        (job.appeal_id,)).fetchone()[0]
    assert n == 0
    failed = storage.jobs.get(job.id)
    assert failed.status == "error"
    assert failed.error == "RuntimeError: сбой записи статуса"


def test_crash_redeliveries_capped_by_attempts(storage, no_webhook, monkeypatch):
    """Воркер падал дважды (захват без finish) → третий захват: ошибка, агент не вызван."""
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 2)
    job = _enqueue(storage, webhook_url="http://rx.local/hook")
    storage.jobs.claim(job.id)
    third = storage.jobs.claim(job.id)
    agent = FakeAgent(storage)

    assert tasks.run_classify_appeal(third, storage, agent) is True

    assert agent.calls == []
    failed = storage.jobs.get(job.id)
    assert failed.status == "error"
    assert "Превышено число попыток" in failed.error
    assert len(no_webhook) == 1 and no_webhook[0][1]["status"] == "error"


def test_finetune_crash_redeliveries_capped(storage, monkeypatch):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 1)
    created = storage.jobs.create("finetune", {}, queue_class="training")
    storage.jobs.claim(created.id)
    job = storage.jobs.claim(created.id)

    tasks.run_finetune(job, storage)

    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and "Превышено число попыток" in failed.error


def test_finish_error_infra_failure_is_retryable(storage, monkeypatch):
    job = _enqueue(storage)

    def broken_finish(*a, **kw):
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(storage.jobs, "finish", broken_finish)
    with pytest.raises(tasks.RetryableError):
        tasks._finish_error(storage, job.id, "RuntimeError: x")
    # и в полном потоке: ошибка LLM, но записать её некуда → повтор, а не сырой psycopg
    with pytest.raises(tasks.RetryableError):
        tasks.run_classify_appeal(job, storage, FakeAgent(storage, error=RuntimeError("llm")))


# ── finetune: run_training целиком (MemoryStorage, фейковые обучение и модель) ──

@pytest.fixture
def fake_training(monkeypatch, tmp_path):
    from training import build_vectordb
    from training import finetune_model
    from finetune_helpers import FakeSentenceTransformer, FakeTrain

    monkeypatch.setattr(config, "MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(config, "FINETUNE_THRESHOLD", 5)
    monkeypatch.setattr(build_vectordb, "SentenceTransformer", FakeSentenceTransformer)

    def use(train):
        monkeypatch.setattr(finetune_model, "sentence_transformers_train", train)

    return use, FakeTrain


def _finetune_job(storage):
    job = storage.jobs.create("finetune", {"source": "combined", "force": False},
                              queue_class="training")
    return storage.jobs.claim(job.id)


def test_run_finetune_completed_job_done(fake_training, no_webhook):
    from finetune_helpers import seed

    use, FakeTrain = fake_training
    use(FakeTrain(before=0.3, after=0.5))
    storage = seed(MemoryStorage())
    job = _finetune_job(storage)

    tasks.run_finetune(job, storage)

    done = storage.jobs.get(job.id)
    assert done.status == "done" and done.error is None
    # база — v0 (адаптер): первая дообученная версия активируется только вручную
    assert done.result["status"] == "completed" and done.result["activated"] is False
    assert storage.models.get(1) is not None and storage.models.active().version == 0


def test_run_finetune_failed_job_error(fake_training, no_webhook):
    from finetune_helpers import seed

    use, FakeTrain = fake_training
    use(FakeTrain(error=RuntimeError("нет памяти")))
    storage = seed(MemoryStorage())
    job = _finetune_job(storage)

    tasks.run_finetune(job, storage)

    failed = storage.jobs.get(job.id)
    assert failed.status == "error" and "нет памяти" in failed.error
    assert failed.result["status"] == "failed"
    assert [mv.version for mv in storage.models.list()] == [0]


def test_run_finetune_lost_ownership_registers_nothing(fake_training, no_webhook):
    """Пока шло обучение, задачу признали потерянной: версия не регистрируется."""
    from finetune_helpers import seed

    use, FakeTrain = fake_training
    storage = seed(MemoryStorage())
    job = _finetune_job(storage)

    class LosingTrain(FakeTrain):
        def __call__(self, *a, **kw):
            storage.jobs.finish(job.id, result=None, error="воркер дообучения пропал")
            return super().__call__(*a, **kw)

    use(LosingTrain(before=0.3, after=0.5))

    tasks.run_finetune(job, storage)

    assert [mv.version for mv in storage.models.list()] == [0]
    after = storage.jobs.get(job.id)
    assert after.status == "error" and after.error == "воркер дообучения пропал"


# ── Владение задачей: устаревшая попытка не пишет карточку и не завершает задачу ──

def _run_stale(storage, no_webhook):
    """Попытка 1 работает, но задачу уже забрал другой воркер (попытка 2)."""
    job = _enqueue(storage, webhook_url="http://rx.local/hook")
    storage.jobs.claim(job.id)  # аренда истекла, задачу взял другой воркер
    agent = FakeAgent(storage)
    assert tasks.run_classify_appeal(job, storage, agent) is None
    return job, agent


def test_stale_attempt_does_not_overwrite_card_or_finish(storage, no_webhook):
    job, agent = _run_stale(storage, no_webhook)
    assert agent.calls == []
    assert _questions(storage, job.appeal_id) == []
    after = storage.jobs.get(job.id)
    assert after.status == "running" and after.attempts == 2 and after.result is None
    assert no_webhook == []


def test_attempt_losing_ownership_mid_work_does_not_finish(storage, no_webhook):
    """Пока шла классификация, задачу забрал другой воркер: finish не происходит."""
    job = _enqueue(storage, webhook_url="http://rx.local/hook")

    class Redelivered(FakeAgent):
        def classify(self, *a, **kw):
            storage.jobs.claim(job.id)
            return super().classify(*a, **kw)

    tasks.run_classify_appeal(job, storage, Redelivered(storage))
    after = storage.jobs.get(job.id)
    assert after.status == "running" and after.result is None
    assert no_webhook == []


def test_stale_attempt_error_does_not_finish_job(storage, no_webhook):
    job = _enqueue(storage, webhook_url="http://rx.local/hook")

    class Failing(FakeAgent):
        def classify(self, *a, **kw):
            storage.jobs.claim(job.id)  # задачу забрал другой воркер
            raise RuntimeError("LLM упал")

    tasks.run_classify_appeal(job, storage, Failing(storage))
    after = storage.jobs.get(job.id)
    assert after.status == "running" and after.error is None
    assert no_webhook == []


@pytest.mark.db
def test_stale_attempt_does_not_overwrite_card_pg(pg_conn, no_webhook):
    from storage.pg import PgStorage

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    storage = PgStorage(connect)
    job, agent = _run_stale(storage, no_webhook)
    assert agent.calls == []
    n = pg_conn.execute("SELECT count(*) FROM appeal_questions WHERE appeal_id = %s",
                        (job.appeal_id,)).fetchone()[0]
    assert n == 0
    after = storage.jobs.get(job.id)
    assert after.status == "running" and after.attempts == 2
    assert no_webhook == []


@pytest.mark.db
def test_losing_ownership_mid_work_rolls_back_card_pg(pg_conn, no_webhook):
    """Условный finish (attempts) не прошёл → карточка устаревшей попытки откатывается."""
    from storage.pg import PgStorage

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    storage = PgStorage(connect)
    job = _enqueue(storage, webhook_url="http://rx.local/hook")

    class Redelivered(FakeAgent):
        def classify(self, *a, log_conn=None, **kw):
            # та же сессия держит блокировку строки — имитируем захват через UPDATE
            log_conn.execute("UPDATE jobs SET attempts = attempts + 1 WHERE id = %s",
                             (job.id,))
            return super().classify(*a, log_conn=log_conn, **kw)

    tasks.run_classify_appeal(job, storage, Redelivered(storage))
    n = pg_conn.execute("SELECT count(*) FROM appeal_questions WHERE appeal_id = %s",
                        (job.appeal_id,)).fetchone()[0]
    assert n == 0
    after = storage.jobs.get(job.id)
    assert after.status == "running" and after.result is None
    assert no_webhook == []

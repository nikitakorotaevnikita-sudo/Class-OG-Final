"""Задачи воркера: classify_appeal, search_classifier, embed_annotation (без модели)."""
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import psycopg
import pytest

import config
from queueing import tasks
from storage.memory import MemoryStorage
from storage.models import AppealQuestionRow, AppealResult

CODE = "0005.0005.0051.1103"
CODE2 = "0001.0001.0001.0001"


def _question(code=CODE, name="Вывоз мусора"):
    return SimpleNamespace(
        question_text="вопрос", code=code, name=name, level=4, full_path="путь",
        predmet_vedeniya="", confidence=0.9, reasoning="потому что", alternatives=[],
    )


class FakeAgent:
    """Агент без модели: пишет карточку через storage на log_conn, как настоящий."""

    model_version = 0

    def __init__(self, storage, codes=(CODE,), error=None):
        self.storage = storage
        self.codes = list(codes)
        self.error = error
        self.calls = []

    def classify(self, text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None):
        self.calls.append(dict(text=text, llm_provider=llm_provider, llm_model=llm_model,
                               appeal_id=appeal_id, log_conn=log_conn))
        if self.error is not None:
            raise self.error
        questions = [_question(c) for c in self.codes]
        self.storage.appeals.save_result(appeal_id, AppealResult(
            summary="суть", applicant_fio="Иванов И.И.", applicant_email=None,
            vid_obrascheniya="Заявление", tip_obrascheniya="Письменное", is_ustnoe=False,
            overall_confidence=0.9, needs_verification=False, full_fallback_used=False,
            candidates=[], llm_provider=llm_provider or "ario", llm_model=llm_model or "qwen",
            elapsed_seconds=1.0,
            questions=[
                AppealQuestionRow(ordinal=i, question_text=q.question_text,
                                  selected_code=q.code, confidence=q.confidence,
                                  alternative_codes=[])
                for i, q in enumerate(questions, 1)
            ],
        ), conn=log_conn)
        return SimpleNamespace(
            questions=questions, log_id=appeal_id, vid_obrascheniya="Заявление",
            tip_obrascheniya="Письменное", is_ustное=False, overall_confidence=0.9,
            needs_verification=False, llm_provider=llm_provider or "ario",
            llm_model=llm_model or "qwen", full_fallback_used=False,
            applicant_fio="Иванов И.И.", applicant_email=None, summary="суть",
        )

    def format_for_operator(self, result):
        return "карточка"

    def _search_candidates(self, q, top_k=None):
        return [{"code": f"{i:04d}.0000.0000.0000", "similarity": 1 - i / 10} for i in range(10)]

    def embed_passage(self, text):
        v = np.zeros(768, dtype=np.float32)
        v[len(text) % 768] = 1.0
        return v


def _enqueue(storage, payload=None, webhook_url=None, document_id=None, text="текст обращения"):
    aid = storage.appeals.create(text, document_id=document_id)
    job = storage.jobs.create("classify", payload or {}, appeal_id=aid,
                              document_id=document_id, webhook_url=webhook_url)
    return {"job_id": job.id, "appeal_id": aid, "document_id": document_id}


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
    msg = _enqueue(storage, {"llm_provider": "groq", "llm_model": "llama",
                             "client_appeal_id": "RX-1", "was_truncated": True})
    agent = FakeAgent(storage)

    tasks.run_classify_appeal(msg, storage, agent)

    job = storage.jobs.get(msg["job_id"])
    assert job.status == "done" and job.error is None
    assert job.result["appeal_id"] == "RX-1"
    assert job.result["was_truncated"] is True
    assert job.result["operator_card"] == "карточка"
    assert [q["code"] for q in job.result["questions"]] == [CODE]
    assert notified == [msg["job_id"]]
    [call] = agent.calls
    assert call["text"] == "текст обращения"
    assert (call["llm_provider"], call["llm_model"]) == ("groq", "llama")
    assert call["appeal_id"] == msg["appeal_id"]
    assert [q["selected_code"] for q in _questions(storage, msg["appeal_id"])] == [CODE]


def test_classify_llm_error_marks_error(storage, no_webhook):
    notified = []
    storage.jobs.add_listener(notified.append)
    msg = _enqueue(storage)

    tasks.run_classify_appeal(msg, storage, FakeAgent(storage, error=RuntimeError("LLM недоступна")))

    job = storage.jobs.get(msg["job_id"])
    assert job.status == "error"
    assert job.error == "RuntimeError: LLM недоступна"
    assert job.result is None
    assert notified == [msg["job_id"]]


def test_classify_missing_text_marks_error(storage, no_webhook):
    job = storage.jobs.create("classify", {})
    msg = {"job_id": job.id, "appeal_id": "11111111-2222-3333-4444-555555555555",
           "document_id": None}
    agent = FakeAgent(storage)

    tasks.run_classify_appeal(msg, storage, agent)

    job = storage.jobs.get(msg["job_id"])
    assert job.status == "error" and "11111111-2222-3333-4444-555555555555" in job.error
    assert agent.calls == []


def test_redelivered_done_job_is_skipped(storage, no_webhook):
    msg = _enqueue(storage, webhook_url="http://rx.local/hook")
    agent = FakeAgent(storage)
    tasks.run_classify_appeal(msg, storage, agent)
    assert len(agent.calls) == 1
    assert len(no_webhook) == 1

    tasks.run_classify_appeal(msg, storage, agent)  # повторная доставка

    assert len(agent.calls) == 1
    assert len(no_webhook) == 1
    assert storage.jobs.get(msg["job_id"]).status == "done"


def test_rerun_replaces_questions(storage, no_webhook):
    msg = _enqueue(storage)
    # Первый прогон записал карточку, но воркер упал до finish (задача осталась running).
    storage.jobs.claim(msg["job_id"])
    FakeAgent(storage, codes=(CODE, CODE2)).classify(
        "текст обращения", appeal_id=msg["appeal_id"])
    assert len(_questions(storage, msg["appeal_id"])) == 2

    tasks.run_classify_appeal(msg, storage, FakeAgent(storage, codes=(CODE2,)))

    assert [q["selected_code"] for q in _questions(storage, msg["appeal_id"])] == [CODE2]
    assert storage.jobs.get(msg["job_id"]).status == "done"


@pytest.mark.parametrize("exc", [
    psycopg.OperationalError("connection lost"),
    psycopg.InterfaceError("closed"),
    ConnectionError("reset"),
])
def test_infra_error_reraised_until_max_attempts(storage, no_webhook, monkeypatch, exc):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 2)
    msg = _enqueue(storage, webhook_url="http://rx.local/hook")
    agent = FakeAgent(storage, error=exc)

    with pytest.raises(tasks.RetryableError):
        tasks.run_classify_appeal(msg, storage, agent)
    job = storage.jobs.get(msg["job_id"])
    assert job.status == "running" and job.attempts == 1
    assert no_webhook == []

    tasks.run_classify_appeal(msg, storage, agent)  # вторая попытка — последняя

    job = storage.jobs.get(msg["job_id"])
    assert job.status == "error" and job.attempts == 2
    assert job.error == f"{type(exc).__name__}: {exc}"
    assert len(no_webhook) == 1 and no_webhook[0][1]["status"] == "error"


def test_kombu_error_is_retryable(storage, no_webhook, monkeypatch):
    from kombu.exceptions import OperationalError as KombuOperationalError

    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 2)
    msg = _enqueue(storage)
    with pytest.raises(tasks.RetryableError):
        tasks.run_classify_appeal(msg, storage, FakeAgent(storage, error=KombuOperationalError("x")))


def test_webhook_sent_after_finish(storage, no_webhook):
    msg = _enqueue(storage, {"response_kind": "document"}, webhook_url="http://rx.local/hook",
                   document_id=42)

    tasks.run_classify_appeal(msg, storage, FakeAgent(storage))

    [(url, payload)] = no_webhook
    assert url == "http://rx.local/hook"
    assert payload["status"] == "done"
    assert payload["job_id"] == msg["job_id"]
    assert payload["document_id"] == 42
    assert payload["error"] is None
    assert payload["result"]["document_id"] == 42
    assert payload["result"]["questions"] == [{"code": CODE, "question": "Вывоз мусора"}]


def test_webhook_not_sent_when_finish_does_not_transition(storage, no_webhook, monkeypatch):
    msg = _enqueue(storage, webhook_url="http://rx.local/hook")
    monkeypatch.setattr(storage.jobs, "finish", lambda *a, **kw: False)

    tasks.run_classify_appeal(msg, storage, FakeAgent(storage))

    assert no_webhook == []


def test_document_response_kind_uses_build_document_response(storage, no_webhook):
    msg = _enqueue(storage, {"response_kind": "document"}, document_id=7)

    tasks.run_classify_appeal(msg, storage, FakeAgent(storage, codes=(CODE, CODE)))

    result = storage.jobs.get(msg["job_id"]).result
    assert set(result) == {"document_id", "applicant_fio", "applicant_email", "summary",
                           "reasoning", "questions"}
    assert result["document_id"] == 7
    assert result["questions"] == [{"code": CODE, "question": "Вывоз мусора"}]  # дубли схлопнуты


def test_search_classifier_finishes_with_results(storage):
    job = storage.jobs.create("search", {"q": "мусор", "top_k": 3}, queue_class="classify_sync")

    tasks.run_search_classifier({"job_id": job.id, "appeal_id": None, "document_id": None},
                                storage, FakeAgent(storage))

    job = storage.jobs.get(job.id)
    assert job.status == "done"
    assert job.result["query"] == "мусор"
    assert [r["code"] for r in job.result["results"]] == [
        "0000.0000.0000.0000", "0001.0000.0000.0000", "0002.0000.0000.0000"]


def test_embed_annotation_sets_embedding(storage):
    from storage.models import ModelVersion

    storage.models.register(ModelVersion(0, "base", "m0", True))
    vid = storage.classifier.add_annotation(CODE, "пояснение", 0)
    job = storage.jobs.create("embed_annotation", {"vector_id": vid})

    tasks.run_embed_annotation({"job_id": job.id, "appeal_id": None, "document_id": None},
                               storage, FakeAgent(storage))

    assert storage.jobs.get(job.id).status == "done"
    assert storage.classifier.pending_annotations(0) == []


def test_embed_annotation_missing_vector_marks_error(storage):
    job = storage.jobs.create("embed_annotation", {"vector_id": 999})

    tasks.run_embed_annotation({"job_id": job.id, "appeal_id": None, "document_id": None},
                               storage, FakeAgent(storage))

    job = storage.jobs.get(job.id)
    assert job.status == "error" and "999" in job.error


def test_celery_tasks_registered():
    from queueing.celery_app import app

    assert "queueing.tasks" in (app.conf.include or ())
    for name in ("classify_appeal", "search_classifier", "embed_annotation", "finetune"):
        assert name in app.tasks


@pytest.mark.db
def test_card_and_job_status_commit_atomically(pg_conn, monkeypatch, no_webhook):
    """finish падает внутри транзакции → карточка не сохранена, задача не done."""
    from storage.pg import PgStorage

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    storage = PgStorage(connect)
    msg = _enqueue(storage)
    real_finish = storage.jobs.finish

    def finish(job_id, result, error, conn=None):
        if error is None:  # успешное завершение в транзакции классификации
            raise RuntimeError("сбой записи статуса")
        return real_finish(job_id, result, error, conn=conn)

    monkeypatch.setattr(storage.jobs, "finish", finish)

    tasks.run_classify_appeal(msg, storage, FakeAgent(storage))

    n = pg_conn.execute("SELECT count(*) FROM appeal_questions WHERE appeal_id = %s",
                        (msg["appeal_id"],)).fetchone()[0]
    assert n == 0
    job = storage.jobs.get(msg["job_id"])
    assert job.status == "error"
    assert job.error == "RuntimeError: сбой записи статуса"


def test_celery_wrapper_converts_retryable_error_into_retry(monkeypatch):
    from celery.exceptions import Retry

    monkeypatch.setattr(tasks, "_agent", lambda: "agent")
    monkeypatch.setattr(tasks, "get_storage", lambda: "storage")

    def boom(msg, storage, agent):
        assert (storage, agent) == ("storage", "agent")
        raise tasks.RetryableError("db down")

    monkeypatch.setattr(tasks, "run_classify_appeal", boom)
    captured = {}

    def fake_retry(**kw):
        captured.update(kw)
        return Retry("retry")

    monkeypatch.setattr(tasks.classify_appeal, "retry", fake_retry)
    with pytest.raises(Retry):
        tasks.classify_appeal.run({"job_id": "x", "appeal_id": None, "document_id": None})
    assert captured["countdown"] == 5
    assert captured["max_retries"] == tasks.INFRA_MAX_RETRIES == 10


def test_crash_redeliveries_capped_by_attempts(storage, no_webhook, monkeypatch):
    """Воркер падал дважды (claim без finish) → третья доставка: ошибка, агент не вызван."""
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 2)
    msg = _enqueue(storage, webhook_url="http://rx.local/hook")
    storage.jobs.claim(msg["job_id"])
    storage.jobs.claim(msg["job_id"])
    agent = FakeAgent(storage)

    tasks.run_classify_appeal(msg, storage, agent)

    assert agent.calls == []
    job = storage.jobs.get(msg["job_id"])
    assert job.status == "error"
    assert "Превышено число попыток" in job.error
    assert len(no_webhook) == 1 and no_webhook[0][1]["status"] == "error"


def test_finetune_crash_redeliveries_capped(storage, monkeypatch):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 1)
    job = storage.jobs.create("finetune", {}, queue_class="training")
    storage.jobs.claim(job.id)

    tasks.run_finetune({"job_id": job.id, "appeal_id": None, "document_id": None}, storage)

    job = storage.jobs.get(job.id)
    assert job.status == "error" and "Превышено число попыток" in job.error


def test_finish_error_infra_failure_is_retryable(storage, monkeypatch):
    msg = _enqueue(storage)

    def broken_finish(*a, **kw):
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(storage.jobs, "finish", broken_finish)
    with pytest.raises(tasks.RetryableError):
        tasks._finish_error(storage, msg["job_id"], "RuntimeError: x")
    # и в полном потоке: ошибка LLM, но записать её некуда → повтор, а не сырой psycopg
    with pytest.raises(tasks.RetryableError):
        tasks.run_classify_appeal(msg, storage, FakeAgent(storage, error=RuntimeError("llm")))


def test_claim_infra_failure_is_retryable(storage, monkeypatch):
    msg = _enqueue(storage)

    def broken_claim(job_id):
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(storage.jobs, "claim", broken_claim)
    agent = FakeAgent(storage)
    with pytest.raises(tasks.RetryableError):
        tasks.run_classify_appeal(msg, storage, agent)
    assert agent.calls == []


@pytest.mark.parametrize("retries, countdown", [(0, 5), (1, 10), (3, 40), (4, 60), (9, 60)])
def test_wrapper_backoff_and_storage_failure_retry(monkeypatch, retries, countdown):
    """Сбой БД при сборке storage тоже уходит в retry; пауза растёт экспоненциально."""
    from celery.exceptions import Retry

    def broken_storage():
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(tasks, "get_storage", broken_storage)
    captured = {}

    def fake_retry(**kw):
        captured.update(kw)
        return Retry("retry")

    monkeypatch.setattr(tasks.search_classifier, "retry", fake_retry)
    tasks.search_classifier.push_request(retries=retries)
    try:
        with pytest.raises(Retry):
            tasks.search_classifier.run({"job_id": "x", "appeal_id": None, "document_id": None})
    finally:
        tasks.search_classifier.pop_request()
    assert captured["countdown"] == countdown
    assert captured["max_retries"] == 10
    assert isinstance(captured["exc"], psycopg.OperationalError)

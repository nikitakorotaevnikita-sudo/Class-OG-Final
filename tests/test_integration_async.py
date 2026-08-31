"""Асинхронный приём для RX: сразу идентификатор, результат — опросом.

Причина появления: классификация занимает от десятков секунд до нескольких
минут, и на синхронном вызове в Directum RX висел заблокированный воркер.
"""

import sys
import time
from dataclasses import dataclass

sys.path.insert(0, "src")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import integration_api
import rx_client


@dataclass
class _Q:
    code: str
    name: str
    reasoning: str = "по смыслу обращения"


class _FakeAgent:
    """Агент, который «думает» заданное время."""

    def __init__(self, delay: float = 0.0, boom: bool = False):
        self.delay = delay
        self.boom = boom
        self.calls = 0

    def classify(self, text):
        self.calls += 1
        time.sleep(self.delay)
        if self.boom:
            raise RuntimeError("LLM недоступен после 5 попыток")

        @dataclass
        class _Result:
            applicant_fio: str = "Иванов И.И."
            applicant_email: str = "ivanov@mail.ru"
            summary: str = "суть"
            questions: list = None

        return _Result(questions=[_Q(code="0005.0005.0056.1160", name="Обращение с ТКО")])


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    """Своя очередь на каждый тест: файлы задач не должны смешиваться."""
    monkeypatch.setattr(integration_api, "_jobs", None)
    monkeypatch.setattr(integration_api.config, "JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(integration_api.config, "JOB_MAX_QUEUED", 3)
    yield


def client(agent):
    app = FastAPI()
    app.include_router(integration_api.router)
    app.state.agent = agent
    return TestClient(app)


def poll_until_done(api, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api.get(f"/integration/jobs/{job_id}").json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"задача {job_id} не завершилась за {timeout}с")


def test_accept_returns_job_id_without_waiting():
    """Ответ должен прийти сразу, не дожидаясь классификации."""
    api = client(_FakeAgent(delay=0.6))
    started = time.time()
    resp = api.post("/integration/classify-document-async",
                    json={"appeal_text": "Не вывозят мусор", "document_id": 26})
    elapsed = time.time() - started

    assert resp.status_code == 202
    assert elapsed < 0.4, f"приём занял {elapsed:.2f}с — значит ждал классификацию"
    body = resp.json()
    assert body["status"] in ("queued", "running")
    assert body["poll_url"] == f"/integration/jobs/{body['job_id']}"
    assert resp.headers["Location"] == body["poll_url"]


def test_polling_returns_the_same_result_as_sync_call():
    api = client(_FakeAgent())
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "Не вывозят мусор", "document_id": 26}).json()["job_id"]
    body = poll_until_done(api, job_id)

    assert body["status"] == "done"
    assert body["document_id"] == 26
    result = body["result"]
    sync = api.post("/integration/classify-document",
                    json={"appeal_text": "Не вывозят мусор", "document_id": 26}).json()
    assert result == sync


def test_result_carries_applicant_fields_and_reasoning():
    api = client(_FakeAgent())
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "текст"}).json()["job_id"]
    result = poll_until_done(api, job_id)["result"]
    assert result["applicant_fio"] == "Иванов И.И."
    assert result["applicant_email"] == "ivanov@mail.ru"
    assert result["reasoning"] == "по смыслу обращения"
    assert result["questions"] == [{"code": "0005.0005.0056.1160", "question": "Обращение с ТКО"}]


def test_status_is_running_while_agent_works():
    api = client(_FakeAgent(delay=0.5))
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "текст"}).json()["job_id"]
    deadline = time.time() + 3
    seen = set()
    while time.time() < deadline:
        seen.add(api.get(f"/integration/jobs/{job_id}").json()["status"])
        if "done" in seen:
            break
        time.sleep(0.05)
    assert "running" in seen or "queued" in seen
    assert "done" in seen


def test_classification_failure_is_reported_in_status_not_at_accept():
    """Сбой модели не должен ломать приём: он виден в статусе задачи."""
    api = client(_FakeAgent(boom=True))
    resp = api.post("/integration/classify-document-async", json={"appeal_text": "текст"})
    assert resp.status_code == 202

    body = poll_until_done(api, resp.json()["job_id"])
    assert body["status"] == "error"
    assert body["result"] is None
    assert "LLM недоступен" in body["error"]


def test_bad_request_is_rejected_before_queueing():
    """Ошибки самого запроса возвращаются сразу, как и на синхронном вызове."""
    api = client(_FakeAgent())
    assert api.post("/integration/classify-document-async", json={}).status_code == 400
    assert api.post("/integration/classify-document-async",
                    json={"appeal_text": "   "}).status_code == 400


def test_unknown_job_id_gives_404():
    api = client(_FakeAgent())
    resp = api.get("/integration/jobs/деадбиф")
    assert resp.status_code == 404
    assert "не найдена" in resp.json()["detail"]


def test_queue_overflow_answers_429():
    """Переполнение — сигнал «повторите позже», а не внутренняя ошибка."""
    api = client(_FakeAgent(delay=1.5))
    codes = [api.post("/integration/classify-document-async",
                      json={"appeal_text": f"обращение {i}"}).status_code
             for i in range(6)]
    assert 429 in codes
    assert codes.count(202) >= 3


def test_queue_listing_shows_stats():
    api = client(_FakeAgent())
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "текст"}).json()["job_id"]
    poll_until_done(api, job_id)
    body = api.get("/integration/jobs").json()
    assert body["stats"]["done"] >= 1
    assert any(j["job_id"] == job_id for j in body["jobs"])


def test_odata_path_still_works_for_document_id_only(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text",
                        lambda doc_id: ("текст из RX", "doc.pdf"))
    api = client(_FakeAgent())
    job_id = api.post("/integration/classify-document-async",
                      json={"document_id": 26}).json()["job_id"]
    body = poll_until_done(api, job_id)
    assert body["status"] == "done"
    assert body["document_id"] == 26


def test_odata_failure_surfaces_at_accept(monkeypatch):
    """Документ не найден — это ошибка запроса, её видно сразу."""
    def _missing(_doc_id):
        raise rx_client.DocumentNotFound("нет такого")

    monkeypatch.setattr(rx_client, "get_document_text", _missing)
    api = client(_FakeAgent())
    resp = api.post("/integration/classify-document-async", json={"document_id": 999})
    assert resp.status_code == 404

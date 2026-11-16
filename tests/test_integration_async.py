"""Асинхронный приём для RX: сразу идентификатор, результат — опросом.

Причина появления: классификация занимает от десятков секунд до нескольких
минут, и на синхронном вызове в Directum RX висел заблокированный воркер.
"""

import sys
import time
from dataclasses import dataclass

sys.path.insert(0, "src")

import pytest

from api import integration_api
from integrations import rx_client
from rx_harness import make_client, poll_until_done


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

    def classify(self, text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None,
                 defer_card=False):
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
def isolated_queue(monkeypatch):
    """Своё memory-хранилище на каждый тест (conftest) и свой лимит очереди."""
    monkeypatch.setattr(integration_api.config, "JOB_MAX_QUEUED", 3)
    yield


def client(agent):
    return make_client(agent)


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
    class _Idle:  # воркеров нет: задачи остаются queued
        def send(self, job):
            pass

    api = make_client(_FakeAgent(), dispatcher=_Idle())
    resps = [api.post("/integration/classify-document-async",
                      json={"appeal_text": f"обращение {i}"}) for i in range(6)]
    codes = [r.status_code for r in resps]
    assert 429 in codes
    assert codes.count(202) >= 3
    assert resps[0].json()["queue_position"] == 1
    refused = next(r for r in resps if r.status_code == 429)
    assert refused.json()["detail"].startswith("Очередь заполнена: ")


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


def test_webhook_host_not_allowed_returns_400(monkeypatch):
    monkeypatch.setattr(integration_api.config, "WEBHOOK_ALLOWED_HOSTS", ["rx.example"])
    api = client(_FakeAgent())
    resp = api.post("/integration/classify-document-async",
                    json={"appeal_text": "текст", "webhook_url": "http://evil.example/hook"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == ("webhook_url не разрешён: хост не входит в "
                                     "WEBHOOK_ALLOWED_HOSTS")
    assert api.get("/integration/jobs").json()["jobs"] == [], "до записи в БД"


def test_webhook_rejected_when_allowlist_empty(monkeypatch):
    monkeypatch.setattr(integration_api.config, "WEBHOOK_ALLOWED_HOSTS", [])
    resp = client(_FakeAgent()).post("/integration/classify-document-async",
                                     json={"appeal_text": "текст",
                                           "webhook_url": "http://rx.example/hook"})
    assert resp.status_code == 400


def test_webhook_delivered_on_done(monkeypatch):
    from queueing import webhook
    monkeypatch.setattr(integration_api.config, "WEBHOOK_ALLOWED_HOSTS", ["rx.example"])
    delivered = []
    monkeypatch.setattr(webhook, "deliver", lambda url, payload, **kw: delivered.append(
        (url, payload)) or True)
    api = client(_FakeAgent())
    resp = api.post("/integration/classify-document-async",
                    json={"appeal_text": "текст", "document_id": 26,
                          "webhook_url": "http://rx.example/hook"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    poll_until_done(api, job_id)
    deadline = time.time() + 5
    while not delivered and time.time() < deadline:
        time.sleep(0.05)
    assert len(delivered) == 1
    url, payload = delivered[0]
    assert url == "http://rx.example/hook"
    assert payload["job_id"] == job_id and payload["status"] == "done"
    assert payload["result"]["questions"][0]["code"] == "0005.0005.0056.1160"


def test_polling_job_created_by_classify_endpoint():
    """504 у /classify отсылает к GET /integration/jobs/{id}: результат classify-вида."""
    from storage import get_storage
    storage = get_storage()
    api = client(_FakeAgent())
    result = {"log_id": "abc", "questions": [], "operator_card": "карточка"}
    job = storage.jobs.create("classify", {"response_kind": "classify",
                                           "queue": "classify_sync"})
    storage.jobs.claim(job.id)
    storage.jobs.finish(job.id, result, None)
    body = api.get(f"/integration/jobs/{job.id}").json()
    assert body["status"] == "done"
    assert body["result"] == result


def test_polling_non_classify_job_gives_404():
    from storage import get_storage
    job = get_storage().jobs.create("finetune", {}, queue_class="training")
    assert client(_FakeAgent()).get(f"/integration/jobs/{job.id}").status_code == 404


def test_sync_timeout_returns_504(monkeypatch):
    api = client(_FakeAgent(delay=1.0))
    monkeypatch.setattr(integration_api.config, "SYNC_WAIT_TIMEOUT_SEC", 0.2)
    resp = api.post("/integration/classify-document", json={"appeal_text": "текст"})
    assert resp.status_code == 504


def test_webhook_checked_before_odata_fetch(monkeypatch):
    """Запрещённый webhook отклоняется до обращения к RX по OData."""
    calls = []
    monkeypatch.setattr(rx_client, "get_document_text",
                        lambda doc_id: calls.append(doc_id) or ("текст", "doc.pdf"))
    monkeypatch.setattr(integration_api.config, "WEBHOOK_ALLOWED_HOSTS", ["rx.example"])
    resp = client(_FakeAgent()).post(
        "/integration/classify-document-async",
        json={"document_id": 26, "webhook_url": "http://evil.example/hook"})
    assert resp.status_code == 400
    assert calls == []


def test_odata_fetch_runs_off_event_loop(monkeypatch):
    import asyncio
    seen = []

    def fetch(doc_id):
        try:
            asyncio.get_running_loop()
            seen.append(False)
        except RuntimeError:
            seen.append(True)
        return "текст из RX", "doc.pdf"

    monkeypatch.setattr(rx_client, "get_document_text", fetch)
    api = client(_FakeAgent())
    assert api.post("/integration/classify-document-async",
                    json={"document_id": 26}).status_code == 202
    assert api.post("/integration/classify-document",
                    json={"document_id": 26}).status_code == 200
    assert seen == [True, True]


def test_queue_listing_counts_only_classify_jobs_and_keeps_worker_alive():
    """Сводка — по задачам классификации (как прежняя очередь RX); ключ
    worker_alive сохранён для совместимости RX."""
    from storage import get_storage
    jobs = get_storage().jobs
    jobs.create("finetune", {}, queue_class="training")
    jobs.create("search", {})
    api = client(_FakeAgent())
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "текст"}).json()["job_id"]
    poll_until_done(api, job_id)
    stats = api.get("/integration/jobs").json()["stats"]
    assert stats == {"queued": 0, "running": 0, "done": 1, "error": 0,
                     "worker_alive": True}


def test_queue_listing_worker_alive_follows_worker_readiness(monkeypatch):
    from api import job_api

    async def no_workers():
        return False

    monkeypatch.setattr(job_api, "worker_status", no_workers)
    stats = client(_FakeAgent()).get("/integration/jobs").json()["stats"]
    assert stats["worker_alive"] is False


def _db_down(*a, **kw):
    import psycopg
    raise psycopg.OperationalError("connection refused")


@pytest.mark.parametrize("method, path, body, repo, fn", [
    ("post", "/integration/classify-document-async", {"appeal_text": "т"}, "appeals", "create"),
    ("post", "/integration/classify-document", {"appeal_text": "т"}, "appeals", "create"),
    ("get", "/integration/jobs/" + "a" * 32, None, "jobs", "get"),
    ("get", "/integration/jobs", None, "jobs", "stats"),
])
def test_rx_endpoints_answer_503_when_db_down(monkeypatch, method, path, body, repo, fn):
    """RX получает 503 «База данных недоступна», а не 500 с трассировкой."""
    from storage import get_storage
    api = client(_FakeAgent())
    monkeypatch.setattr(getattr(get_storage(), repo), fn, _db_down)
    resp = api.post(path, json=body) if method == "post" else api.get(path)
    assert resp.status_code == 503
    assert resp.json() == {"detail": "База данных недоступна"}


def test_sync_failure_returns_500():
    resp = client(_FakeAgent(boom=True)).post("/integration/classify-document",
                                              json={"appeal_text": "текст"})
    assert resp.status_code == 500
    assert resp.json()["detail"].startswith("Ошибка классификации: ")


# ── Забранный результат: delivered_at и удаление по JOB_DELIVERED_GRACE_SEC ──

def test_integration_job_poll_marks_delivered_and_repeat_poll_same_result():
    """Конечный статус в GET /integration/jobs/{id} помечает результат забранным;
    повторный опрос (сетевой сбой у RX) до чистки отдаёт то же самое."""
    from storage import get_storage
    api = client(_FakeAgent())
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "текст", "document_id": 26}).json()["job_id"]
    jobs = get_storage().jobs
    first = poll_until_done(api, job_id)
    delivered = jobs.get(job_id).delivered_at
    assert delivered is not None
    again = api.get(f"/integration/jobs/{job_id}")
    assert again.status_code == 200
    assert again.json() == first
    assert jobs.get(job_id).delivered_at == delivered  # метка не сдвигается


def test_integration_job_poll_running_is_not_delivered():
    from storage import get_storage
    jobs = get_storage().jobs
    job = jobs.create("classify", {"response_kind": "document"})
    api = client(_FakeAgent())
    assert api.get(f"/integration/jobs/{job.id}").json()["status"] == "queued"
    jobs.claim(job.id)
    assert api.get(f"/integration/jobs/{job.id}").json()["status"] == "running"
    assert jobs.get(job.id).delivered_at is None


def test_poll_after_grace_cleanup_returns_404():
    from storage import get_storage
    api = client(_FakeAgent())
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "текст"}).json()["job_id"]
    poll_until_done(api, job_id)
    assert get_storage().jobs.cleanup_expired(24, delivered_grace_sec=0) == 1
    resp = api.get(f"/integration/jobs/{job_id}")
    assert resp.status_code == 404
    assert "не найдена" in resp.json()["detail"]


def test_sync_document_marks_delivered():
    from storage import get_storage
    api = client(_FakeAgent())
    assert api.post("/integration/classify-document",
                    json={"appeal_text": "текст"}).status_code == 200
    job = get_storage().jobs.latest("classify")
    assert job.status == "done" and job.delivered_at is not None

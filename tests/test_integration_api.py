import sys
sys.path.insert(0, "src")
from dataclasses import dataclass, field
from fastapi import FastAPI
from fastapi.testclient import TestClient
import integration_api
import rx_client


@dataclass
class _Q:
    code: str
    name: str
    reasoning: str = ""


@dataclass
class _Result:
    applicant_fio: str
    summary: str
    questions: list


class _FakeAgent:
    def classify(self, text):
        return _Result(applicant_fio="Иванов И.И.", summary="суть",
                       questions=[_Q(code="0001.0002.0003.0004", name="вопрос текстом",
                                     reasoning="выбран по смыслу обращения")])


def _app(agent):
    app = FastAPI()
    app.include_router(integration_api.router)
    app.state.agent = agent
    return app


def test_classify_document_ok(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text",
                        lambda doc_id: ("текст обращения", "doc26.pdf"))
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 26})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "document_id": 26,
        "applicant_fio": "Иванов И.И.",
        "summary": "суть",
        "questions": [{"code": "0001.0002.0003.0004", "question": "вопрос текстом",
                       "reasoning": "выбран по смыслу обращения"}],
    }


def test_reasoning_clipped_to_1000(monkeypatch):
    long = "а" * 5000

    class _LongAgent:
        def classify(self, text):
            return _Result(applicant_fio="Иванов И.И.", summary="суть",
                           questions=[_Q(code="0001.0002.0003.0004", name="в", reasoning=long)])

    monkeypatch.setattr(rx_client, "get_document_text",
                        lambda doc_id: ("текст", "doc.pdf"))
    client = TestClient(_app(_LongAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 26})
    assert resp.status_code == 200
    r = resp.json()["questions"][0]["reasoning"]
    assert len(r) == 1000
    assert r.endswith("…")


def test_classify_document_not_found(monkeypatch):
    def raise_nf(doc_id):
        raise rx_client.DocumentNotFound("нет")
    monkeypatch.setattr(rx_client, "get_document_text", raise_nf)
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 999})
    assert resp.status_code == 404


def test_classify_document_body_error(monkeypatch):
    def raise_be(doc_id):
        raise rx_client.BodyFetchError("500")
    monkeypatch.setattr(rx_client, "get_document_text", raise_be)
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 26})
    assert resp.status_code == 502


def test_classify_document_empty_text(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text", lambda doc_id: ("   ", "doc.pdf"))
    client = TestClient(_app(_FakeAgent()))
    resp = client.post("/integration/classify-document", json={"document_id": 26})
    assert resp.status_code == 400

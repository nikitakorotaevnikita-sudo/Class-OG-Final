"""Интеграция с RX: текст обращения приходит В ЗАПРОСЕ.

Раньше RX присылал только document_id, а сервис сам забирал тело документа из RX
по OData. Теперь RX передаёт текст сразу — обращения к OData не требуется.
Формат ОТВЕТА: {document_id, applicant_fio, applicant_email, summary, reasoning,
questions}. Поля только добавляются — `applicant_email` появился позже остальных.
"""
import sys
from dataclasses import dataclass

sys.path.insert(0, "src")

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
    applicant_email: str | None = None


class _FakeAgent:
    def __init__(self):
        self.seen_text = None

    def classify(self, text):
        self.seen_text = text
        return _Result(applicant_fio="Иванов И.И.", summary="суть",
                       questions=[_Q(code="0005.0005.0056.1160",
                                     name="Обращение с ТКО",
                                     reasoning="по смыслу обращения")])


def _app(agent):
    app = FastAPI()
    app.include_router(integration_api.router)
    app.state.agent = agent
    return app


def _boom(_doc_id):
    raise AssertionError("OData не должен вызываться, когда текст пришёл в запросе")


def test_classifies_text_from_request_without_odata(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text", _boom)
    agent = _FakeAgent()
    resp = TestClient(_app(agent)).post("/integration/classify-document",
                                        json={"document_id": 26,
                                              "appeal_text": "Не вывозят мусор две недели"})
    assert resp.status_code == 200
    assert agent.seen_text == "Не вывозят мусор две недели"


def test_response_format(monkeypatch):
    """Полный состав ответа. Поля только добавляются: RX читает ответ по именам,
    поэтому новое `applicant_email` не ломает уже работающую интеграцию."""
    monkeypatch.setattr(rx_client, "get_document_text", _boom)
    resp = TestClient(_app(_FakeAgent())).post(
        "/integration/classify-document",
        json={"document_id": 26, "appeal_text": "Не вывозят мусор"})
    assert resp.json() == {
        "document_id": 26,
        "applicant_fio": "Иванов И.И.",
        "applicant_email": None,
        "summary": "суть",
        "reasoning": "по смыслу обращения",
        "questions": [{"code": "0005.0005.0056.1160", "question": "Обращение с ТКО"}],
    }


def test_empty_text_is_rejected(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text", _boom)
    resp = TestClient(_app(_FakeAgent())).post(
        "/integration/classify-document",
        json={"document_id": 26, "appeal_text": "   "})
    assert resp.status_code == 400


def test_request_without_text_and_without_id_is_rejected(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text", _boom)
    resp = TestClient(_app(_FakeAgent())).post("/integration/classify-document", json={})
    assert resp.status_code == 400


def test_document_id_is_optional_when_text_given(monkeypatch):
    """RX может прислать только текст — тогда document_id в ответе пустой."""
    monkeypatch.setattr(rx_client, "get_document_text", _boom)
    resp = TestClient(_app(_FakeAgent())).post(
        "/integration/classify-document", json={"appeal_text": "Яма на дороге"})
    assert resp.status_code == 200
    assert resp.json()["document_id"] is None


def test_falls_back_to_odata_when_only_id_given(monkeypatch):
    """Обратная совместимость: прежний вызов с одним document_id продолжает работать."""
    monkeypatch.setattr(rx_client, "get_document_text",
                        lambda doc_id: ("текст из RX", "doc.pdf"))
    agent = _FakeAgent()
    resp = TestClient(_app(agent)).post("/integration/classify-document",
                                        json={"document_id": 26})
    assert resp.status_code == 200
    assert agent.seen_text == "текст из RX"


def test_agent_not_ready(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text", _boom)
    app = FastAPI()
    app.include_router(integration_api.router)
    app.state.agent = None
    resp = TestClient(app).post("/integration/classify-document",
                                json={"appeal_text": "текст"})
    assert resp.status_code == 503

def test_applicant_email_returned_in_response(monkeypatch):
    """Почта заявителя доезжает до ответа RX отдельным полем."""
    monkeypatch.setattr(rx_client, "get_document_text", _boom)

    class _AgentWithEmail(_FakeAgent):
        def classify(self, text):
            result = super().classify(text)
            result.applicant_email = "zayavitel@mail.ru"
            return result

    resp = TestClient(_app(_AgentWithEmail())).post(
        "/integration/classify-document",
        json={"appeal_text": "Прошу ответить на e-mail: zayavitel@mail.ru"},
    )
    assert resp.status_code == 200
    assert resp.json()["applicant_email"] == "zayavitel@mail.ru"


def test_applicant_email_null_when_absent(monkeypatch):
    """Нет почты в тексте — поле присутствует и равно null, а не отсутствует."""
    monkeypatch.setattr(rx_client, "get_document_text", _boom)
    resp = TestClient(_app(_FakeAgent())).post(
        "/integration/classify-document",
        json={"appeal_text": "Прошу отремонтировать дорогу"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "applicant_email" in body
    assert body["applicant_email"] is None

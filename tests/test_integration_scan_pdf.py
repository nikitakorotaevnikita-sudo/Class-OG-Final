"""Скан вместо текста — понятный отказ, а не 500.

Случай со стенда: RX прислал `{"document_id": 16320}`, сервис забрал документ
по OData, а в PDF оказались только изображения. `ScanNotSupportedError` из
`text_extractor` не перехватывался и вылетал наружу как `500 Internal Server
Error` — по такому ответу на стороне RX не понять, что делать: повторять вызов
бессмысленно, документ нужно отправлять на распознавание.

`docs/INTEGRATION.md` обещает на этот случай `400` («документ в RX без
извлекаемого текста (скан)») — приводим код в соответствие с контрактом.
"""

import sys
from pathlib import Path

sys.path.insert(0, "src")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import integration_api
import rx_client
import text_extractor


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(integration_api, "_jobs", None)
    monkeypatch.setattr(integration_api.config, "JOBS_DIR", str(tmp_path / "jobs"))
    yield


class _Agent:
    """Агент, который не должен быть вызван: до классификации дело не доходит."""

    def classify(self, text):
        raise AssertionError("классификация при нечитаемом документе не нужна")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(integration_api.router)
    app.state.agent = _Agent()
    return TestClient(app)


def _raises(exc):
    def boom(document_id):
        raise exc
    return boom


SCAN = text_extractor.ScanNotSupportedError(
    "PDF содержит только изображения (скан). Для сканированных документов используйте OCR.")


def test_scanned_document_answers_400(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text", _raises(SCAN))
    r = _client().post("/integration/classify-document", json={"document_id": 16320})

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "16320" in detail, "в ответе нужен id — по логу RX иначе не найти документ"
    assert "скан" in detail.lower()


def test_broken_document_answers_400(monkeypatch):
    """Прочие сбои извлечения — тоже не 500: повтор вызова не поможет."""
    monkeypatch.setattr(rx_client, "get_document_text",
                        _raises(text_extractor.TextExtractionError("Не удалось открыть PDF")))
    r = _client().post("/integration/classify-document", json={"document_id": 16320})

    assert r.status_code == 400, r.text
    assert "16320" in r.json()["detail"]


def test_unsupported_format_answers_400(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text",
                        _raises(text_extractor.TextExtractionError("Формат не поддерживается: .zip")))
    assert _client().post("/integration/classify-document",
                          json={"document_id": 16320}).status_code == 400


def test_async_call_rejects_a_scan_at_accept(monkeypatch):
    """Ошибка запроса возвращается сразу, а не через опрос статуса."""
    monkeypatch.setattr(rx_client, "get_document_text", _raises(SCAN))
    api = _client()

    r = api.post("/integration/classify-document-async", json={"document_id": 16320})

    assert r.status_code == 400, r.text
    assert api.get("/integration/jobs").json()["stats"]["queued"] == 0, "в очередь такое не ставим"


def test_missing_document_is_still_404(monkeypatch):
    monkeypatch.setattr(rx_client, "get_document_text",
                        _raises(rx_client.DocumentNotFound("нет такого")))
    r = _client().post("/integration/classify-document", json={"document_id": 16320})
    assert r.status_code == 404

import sys
sys.path.insert(0, "src")
import base64
import httpx
import pytest
import rx_client

# Минимальный валидный PDF с текстовым слоем не нужен — text_extractor мокаем.
PDF_BYTES = b"%PDF-1.4 fake body"


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://rx")


def test_get_document_text_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("(26)"):
            return httpx.Response(200, json={"Name": "Вх. письмо",
                                              "Versions": [{"Id": 2, "Number": 1}]})
        if "$value" in str(request.url):
            return httpx.Response(200, content=PDF_BYTES)
        return httpx.Response(404)

    monkeypatch.setattr(rx_client, "RX_ODATA_URL", "http://rx")
    monkeypatch.setattr(rx_client, "build_client", lambda: _mock_client(handler))
    monkeypatch.setattr(rx_client, "extract_text",
                        lambda body, filename: ("извлечённый текст", False))

    text, filename = rx_client.get_document_text(26)
    assert text == "извлечённый текст"
    assert filename.endswith(".pdf")


def test_get_document_text_not_found(monkeypatch):
    def handler(request):
        return httpx.Response(404)
    monkeypatch.setattr(rx_client, "RX_ODATA_URL", "http://rx")
    monkeypatch.setattr(rx_client, "build_client", lambda: _mock_client(handler))
    with pytest.raises(rx_client.DocumentNotFound):
        rx_client.get_document_text(999)


def test_get_document_text_body_error(monkeypatch):
    def handler(request):
        if request.url.path.endswith("(26)"):
            return httpx.Response(200, json={"Name": "x", "Versions": [{"Id": 2, "Number": 1}]})
        return httpx.Response(500, text="boom")
    monkeypatch.setattr(rx_client, "RX_ODATA_URL", "http://rx")
    monkeypatch.setattr(rx_client, "build_client", lambda: _mock_client(handler))
    with pytest.raises(rx_client.BodyFetchError):
        rx_client.get_document_text(26)

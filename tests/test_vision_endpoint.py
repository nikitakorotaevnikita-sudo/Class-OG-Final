"""Страница распознавания: загрузка файла → текст по страницам.

Отдельный инструмент оператора, не часть пайплайна классификации: результат
показывается человеку и никуда не подставляется автоматически.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from fastapi.testclient import TestClient

import api_server
import vision_ocr

client = TestClient(api_server.app)


def png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


# ── Страница ────────────────────────────────────────────────────────────────

def test_page_is_served():
    response = client.get("/vision")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_page_warns_that_recognition_can_invent_text():
    """Проверка показала: на мелком изображении модель выдумывает детали."""
    page = (Path(__file__).parent.parent / "src" / "static" / "vision.html").read_text(
        encoding="utf-8")
    assert "провер" in page.lower(), "нужна явная пометка о проверке результата"


# ── Эндпоинт ────────────────────────────────────────────────────────────────

def test_recognises_an_image(monkeypatch):
    monkeypatch.setattr(vision_ocr, "recognize",
                        lambda data, name, **kw: [{"page": 1, "text": "текст со скана"}])
    response = client.post("/api/recognize-image",
                           files={"file": ("скан.png", png_bytes(), "image/png")})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pages"] == [{"page": 1, "text": "текст со скана"}]
    assert body["needs_verification"] is True, "результат всегда требует проверки"
    assert body["provider"], "оператору важно видеть, какая модель распознавала"


def test_multi_page_pdf_comes_back_page_by_page(monkeypatch):
    monkeypatch.setattr(vision_ocr, "recognize",
                        lambda data, name, **kw: [{"page": n, "text": f"стр {n}"} for n in (1, 2)])
    response = client.post("/api/recognize-image",
                           files={"file": ("скан.pdf", b"%PDF-fake", "application/pdf")})
    assert [p["page"] for p in response.json()["pages"]] == [1, 2]


def test_unsupported_format_is_refused():
    response = client.post("/api/recognize-image",
                           files={"file": ("обращение.docx", b"...", "application/octet-stream")})
    assert response.status_code == 400
    assert "docx" in response.json()["detail"].lower()


def test_oversized_file_is_refused():
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (21 * 1024 * 1024)
    response = client.post("/api/recognize-image",
                           files={"file": ("скан.png", big, "image/png")})
    assert response.status_code == 400
    assert "мб" in response.json()["detail"].lower()


def test_model_without_vision_is_explained_not_crashed(monkeypatch):
    def boom(data, name, **kw):
        raise vision_ocr.VisionError("Модель не принимает изображения")

    monkeypatch.setattr(vision_ocr, "recognize", boom)
    response = client.post("/api/recognize-image",
                           files={"file": ("скан.png", png_bytes(), "image/png")})
    assert response.status_code == 400, "это не сбой сервиса, а несовместимость"
    assert "изображения" in response.json()["detail"]


def test_file_is_not_written_to_disk(monkeypatch, tmp_path):
    """В скане персональные данные — на диске ему делать нечего."""
    seen = []
    monkeypatch.setattr(vision_ocr, "recognize",
                        lambda data, name, **kw: [{"page": 1, "text": "ок"}])
    real_open = io.open

    def watched_open(file, mode="r", *args, **kwargs):
        if "w" in mode and "temp_upload" in str(file):
            seen.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", watched_open)
    client.post("/api/recognize-image", files={"file": ("скан.png", png_bytes(), "image/png")})
    assert not seen, f"файл сохранён на диск: {seen}"

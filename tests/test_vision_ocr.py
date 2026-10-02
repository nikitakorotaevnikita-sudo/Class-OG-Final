"""Распознавание текста с изображения и со скана PDF.

Появилось после случая с документом 16320: скан без текстового слоя пайплайн
отвергает, а модель на endpoint Ario изображения читает — проверено живым
запросом. Страница распознавания даёт оператору способ достать текст из такого
документа руками.

Отдельно фиксируется то, что выяснилось при проверке: на мелком изображении
модель уверенно выдумывает правдоподобные детали (в пробе — чужой e-mail).
Поэтому результат помечается как требующий проверки, а не идёт в карточку.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import vision_ocr


# ── Подготовка образцов ─────────────────────────────────────────────────────

def make_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


def make_pdf(pages: int = 3) -> bytes:
    import fitz
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"страница {i + 1}")
    return doc.tobytes()


# ── Разбор PDF на страницы ──────────────────────────────────────────────────

def test_pdf_becomes_one_image_per_page():
    images = vision_ocr.pdf_to_images(make_pdf(3))
    assert len(images) == 3
    assert all(img[:8] == b"\x89PNG\r\n\x1a\n" for img in images), "ожидали PNG"


def test_page_limit_protects_from_a_huge_scan():
    """Скан на 40 страниц — это 40 вызовов модели; предел нужен."""
    assert len(vision_ocr.pdf_to_images(make_pdf(5), max_pages=2)) == 2


def test_broken_pdf_is_reported_clearly():
    with pytest.raises(vision_ocr.VisionError) as exc:
        vision_ocr.pdf_to_images(b"not a pdf at all")
    assert "pdf" in str(exc.value).lower()


# ── Какие файлы принимаем ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["скан.png", "ФОТО.JPG", "scan.jpeg", "page.webp", "doc.pdf"])
def test_supported_formats(name):
    assert vision_ocr.is_supported(name)


@pytest.mark.parametrize("name", ["обращение.docx", "архив.zip", "скан", "data.xlsx"])
def test_unsupported_formats(name):
    assert not vision_ocr.is_supported(name)


# ── Вызов модели ────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {"choices": [{"message": {"content": "узнанный текст"}}]}
        self.text = text or "ошибка"

    def json(self):
        return self._payload


def test_image_is_sent_to_the_active_provider(monkeypatch):
    sent = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["payload"] = kwargs.get("json")
        return _Resp()

    monkeypatch.setattr(vision_ocr.httpx, "post", fake_post)
    pages = vision_ocr.recognize(make_png(), "скан.png")

    assert pages == [{"page": 1, "text": "узнанный текст"}]
    assert sent["url"].endswith("/chat/completions")
    content = sent["payload"]["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content), "картинка не ушла"
    assert any("data:image/png;base64," in str(part) for part in content)


def test_every_pdf_page_is_recognised(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _Resp(payload={"choices": [{"message": {"content": f"текст {len(calls)}"}}]})

    monkeypatch.setattr(vision_ocr.httpx, "post", fake_post)
    pages = vision_ocr.recognize(make_pdf(3), "скан.pdf")

    assert [p["page"] for p in pages] == [1, 2, 3]
    assert pages[2]["text"] == "текст 3"
    assert len(calls) == 3


def test_provider_without_vision_gets_a_readable_error(monkeypatch):
    """vLLM без vision отвечает 400 про image_url — не 500 в лицо оператору."""
    def fake_post(url, **kwargs):
        return _Resp(status=400, text='{"error":"image_url is not supported by this model"}')

    monkeypatch.setattr(vision_ocr.httpx, "post", fake_post)
    with pytest.raises(vision_ocr.VisionError) as exc:
        vision_ocr.recognize(make_png(), "скан.png")
    assert "изображен" in str(exc.value).lower()


def test_unsupported_file_is_refused_before_any_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("вызова модели быть не должно")

    monkeypatch.setattr(vision_ocr.httpx, "post", boom)
    with pytest.raises(vision_ocr.VisionError):
        vision_ocr.recognize(b"...", "обращение.docx")

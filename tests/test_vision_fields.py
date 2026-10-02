"""Ключевые поля — отдельным вопросом, с правом ответить «не читаю».

Замер на трудном образце показал характер ошибки: в сплошной расшифровке модель
не оставляет пробел, а дополняет его правдоподобным — адрес `petrova.m@mail.ru`
превращался в огрызок. Два прогона при этом совпадали дословно, то есть сверкой
повторов такую ошибку не поймать.

Но на точечный вопрос с явно разрешённым отказом та же модель на той же картинке
отвечает `null`. Отсюда правило: почта, телефон и ФИО берутся отдельными
вопросами, где «не разобрал» — допустимый ответ.
"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import vision_ocr


def make_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (60, 30), "white").save(buf, format="PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, content, status=200):
        self.status_code = status
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def answers(*contents):
    """Поочерёдные ответы модели на последовательные запросы."""
    queue = list(contents)

    def fake_post(url, **kwargs):
        return _Resp(queue.pop(0) if queue else "null")
    return fake_post


# ── Отдельный вопрос про поля ───────────────────────────────────────────────

def test_fields_are_asked_separately_from_the_transcript(monkeypatch):
    seen = []

    def fake_post(url, **kwargs):
        seen.append(kwargs["json"]["messages"][0]["content"][0]["text"])
        return _Resp('{"applicant_fio": "Петрова Мария Ивановна", '
                     '"email": "petrova.m@mail.ru", "phone": null}'
                     if len(seen) > 1 else "текст страницы")

    monkeypatch.setattr(vision_ocr.httpx, "post", fake_post)
    result = vision_ocr.recognize_document(make_png(), "скан.png")

    assert len(seen) == 2, "ожидали расшифровку и отдельный вопрос про поля"
    assert "null" in seen[1].lower(), "в вопросе про поля должен быть разрешён отказ"
    assert result["fields"]["email"] == "petrova.m@mail.ru"
    assert result["fields"]["phone"] is None


def test_unreadable_field_stays_empty(monkeypatch):
    """Главное свойство: лучше пусто, чем правдоподобно выдуманный адрес."""
    monkeypatch.setattr(vision_ocr.httpx, "post",
                        answers("текст", '{"applicant_fio": null, "email": "null", "phone": ""}'))
    fields = vision_ocr.recognize_document(make_png(), "скан.png")["fields"]
    assert fields == {"applicant_fio": None, "email": None, "phone": None}


def test_broken_json_from_the_model_does_not_break_recognition(monkeypatch):
    monkeypatch.setattr(vision_ocr.httpx, "post",
                        answers("текст страницы", "я не смог разобрать поля"))
    result = vision_ocr.recognize_document(make_png(), "скан.png")
    assert result["pages"][0]["text"] == "текст страницы"
    assert result["fields"]["email"] is None


def test_first_readable_value_across_pages_wins(monkeypatch):
    """Шапка на первой странице, подпись с почтой — на последней."""
    import fitz
    doc = fitz.open()
    for _ in range(2):
        doc.new_page()
    pdf = doc.tobytes()

    monkeypatch.setattr(vision_ocr.httpx, "post", answers(
        "страница 1", '{"applicant_fio": "Петрова Мария Ивановна", "email": null, "phone": null}',
        "страница 2", '{"applicant_fio": null, "email": "petrova.m@mail.ru", "phone": null}'))

    fields = vision_ocr.recognize_document(pdf, "скан.pdf")["fields"]
    assert fields["applicant_fio"] == "Петрова Мария Ивановна"
    assert fields["email"] == "petrova.m@mail.ru"


# ── Разрешение рендера ──────────────────────────────────────────────────────

def test_pdf_is_rendered_at_300_dpi():
    """Единственная настройка, улучшившая CER в замере: 4.2% → 2.6%."""
    assert vision_ocr.PDF_DPI == 300


# ── Картинка рядом с текстом ────────────────────────────────────────────────

def test_page_image_comes_back_for_side_by_side_check(monkeypatch):
    monkeypatch.setattr(vision_ocr.httpx, "post", answers("текст", "{}"))
    page = vision_ocr.recognize_document(make_png(), "скан.png")["pages"][0]
    assert page["image"].startswith("data:image/"), "оператору нужно сверить текст с оригиналом"


def test_page_image_is_downscaled(monkeypatch):
    """Полноразмерные страницы раздувают ответ; для сверки хватает уменьшенной."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(buf, format="PNG")

    monkeypatch.setattr(vision_ocr.httpx, "post", answers("текст", "{}"))
    page = vision_ocr.recognize_document(buf.getvalue(), "скан.png")["pages"][0]

    import base64
    raw = base64.b64decode(page["image"].split(",", 1)[1])
    assert Image.open(io.BytesIO(raw)).width <= vision_ocr.PREVIEW_WIDTH


def test_fio_from_the_header_comes_back_in_nominative(monkeypatch):
    """Шапка пишется в родительном: «от Петровой Марии Ивановны»."""
    monkeypatch.setattr(vision_ocr.httpx, "post", answers(
        "текст", '{"applicant_fio": "Петровой Марии Ивановны", "email": null, "phone": null}'))
    fields = vision_ocr.recognize_document(make_png(), "скан.png")["fields"]
    assert fields["applicant_fio"] == "Петрова Мария Ивановна"


# ── Предобработка мелких изображений ────────────────────────────────────────
# На живом образце (фото резолюции 653×132) увеличение втрое с автоконтрастом
# было единственным, что сдвинуло результат: фамилия «Иванова» распозналась
# верно, тогда как без него выходило «Иванов А.».

def _sent_image(monkeypatch, png: bytes):
    import base64
    captured = {}

    def fake_post(url, **kwargs):
        part = kwargs["json"]["messages"][0]["content"][1]["image_url"]["url"]
        captured["raw"] = base64.b64decode(part.split(",", 1)[1])
        return _Resp("текст")

    monkeypatch.setattr(vision_ocr.httpx, "post", fake_post)
    vision_ocr.recognize_document(png, "скан.png")
    from PIL import Image
    return Image.open(io.BytesIO(captured["raw"]))


def test_small_image_is_enlarged_before_sending(monkeypatch):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (653, 132), "white").save(buf, format="PNG")
    assert _sent_image(monkeypatch, buf.getvalue()).width > 653 * 2


def test_large_image_is_left_alone(monkeypatch):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1600), "white").save(buf, format="PNG")
    assert _sent_image(monkeypatch, buf.getvalue()).width == 2400


# ── Сверка полей с расшифровкой ─────────────────────────────────────────────
# На живом образце поле ФИО пришло как «Иванова А.А», тогда как в расшифровке
# той же страницы стояло «Д. Иванова», а в оригинале — «Иванова Н.А.».
# Значение, которого нет в расшифровке, помечается неподтверждённым: оператор
# должен видеть разницу между «модель прочитала» и «модель предположила».

def test_field_present_in_the_transcript_is_confirmed(monkeypatch):
    monkeypatch.setattr(vision_ocr.httpx, "post", answers(
        "Ответ прошу на petrova.m@mail.ru",
        '{"applicant_fio": null, "email": "petrova.m@mail.ru", "phone": null}'))
    result = vision_ocr.recognize_document(make_png(), "скан.png")
    assert result["confirmed"]["email"] is True


def test_field_absent_from_the_transcript_is_flagged(monkeypatch):
    monkeypatch.setattr(vision_ocr.httpx, "post", answers(
        "Д. Иванова",
        '{"applicant_fio": "Иванова А.А", "email": null, "phone": null}'))
    result = vision_ocr.recognize_document(make_png(), "скан.png")
    assert result["fields"]["applicant_fio"] == "Иванова А.А"
    assert result["confirmed"]["applicant_fio"] is False, "инициалов в расшифровке не было"


def test_empty_field_is_neither_confirmed_nor_flagged(monkeypatch):
    monkeypatch.setattr(vision_ocr.httpx, "post", answers("текст", '{"email": null}'))
    result = vision_ocr.recognize_document(make_png(), "скан.png")
    assert result["confirmed"]["email"] is None

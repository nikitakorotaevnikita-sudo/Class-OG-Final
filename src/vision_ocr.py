"""Распознавание текста с изображения или скана PDF силами LLM с vision.

Пайплайн классификации работает с текстом, и документ без текстового слоя он
отвергает — повторять вызов бессмысленно, читать там нечего. Но модель на
endpoint Ario изображения читает (проверено живым запросом), поэтому оператору
имеет смысл дать отдельный инструмент: загрузить скан и получить текст.

Важное ограничение, выясненное при проверке: на мелком или шумном изображении
модель не отказывается и не сомневается, а уверенно выдумывает правдоподобные
детали — в пробе подменила адрес электронной почты. Поэтому распознанный текст
здесь только показывается оператору и ни во что не подставляется автоматически.
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx

from config import LLM_VIA_PROXY
from fio_extractor import normalize_fio, to_nominative
from llm_check import resolve_endpoint
from proxy_policy import uses_env_proxy

# Сколько страниц скана разбирать за один раз. Каждая страница — отдельный
# вызов модели на десятки секунд, поэтому предел нужен не для красоты.
MAX_PAGES = 10

# Плотность рендера страницы PDF. 300 dpi — единственная настройка, которая
# улучшила качество в замере на трудном образце: CER 4.2% → 2.6%. Предобработка
# (серый, контраст, резкость) и нарезка на полосы там не дали ничего.
PDF_DPI = 300

# Мелкие фотографии (снимок резолюции, кусок страницы) увеличиваются перед
# отправкой: на живом образце 653×132 это было единственное, что помогло —
# фамилия распозналась верно, тогда как без увеличения выходила чужая.
MIN_WIDTH = 1600
MAX_WIDTH = 2600

# Ширина уменьшенной копии страницы, которую отдаём оператору для сверки текста
# с оригиналом. Полноразмерные страницы раздували бы ответ впустую.
PREVIEW_WIDTH = 900

TIMEOUT = 180.0

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES + (".pdf",)

PROMPT = ("Это скан обращения. Перепиши текст ДОСЛОВНО, строка в строку. Ничего не "
          "додумывай и не заменяй синонимами. Нечитаемый фрагмент обозначь [?]. "
          "Выведи только текст. Если текста нет, ответь: (пусто)")

# Ключевые поля спрашиваются ОТДЕЛЬНО и с явно разрешённым отказом. Замер
# показал, почему: в сплошной расшифровке модель не оставляет пробел, а
# дополняет его правдоподобным — адрес почты превращался в похожий, но чужой.
# На этот же вопрос с правом ответить null она честно отвечает null.
FIELDS_PROMPT = (
    "Это скан обращения гражданина. Найди на изображении три значения и верни "
    "СТРОГО JSON без пояснений:\n"
    '{"applicant_fio": "ФИО заявителя", "email": "адрес почты", "phone": "телефон"}\n'
    "Выводи ДОСЛОВНО, символ в символ, как написано на изображении. "
    "Если значение не читается уверенно или его нет — поставь null. "
    "Выдумывать и достраивать по смыслу нельзя: null лучше, чем правдоподобная догадка.")

FIELD_NAMES = ("applicant_fio", "email", "phone")


class VisionError(Exception):
    """Не смогли распознать: формат, битый файл или модель без vision."""


def is_supported(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in SUPPORTED_SUFFIXES


def pdf_to_images(pdf_bytes: bytes, max_pages: int = MAX_PAGES, dpi: int = PDF_DPI) -> list[bytes]:
    """Страницы PDF → PNG, по одной картинке на страницу."""
    try:
        import fitz
    except ImportError as exc:                                   # noqa: BLE001
        raise VisionError("PyMuPDF не установлен — разбор PDF недоступен") from exc

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:                                     # noqa: BLE001
        raise VisionError(f"Не удалось открыть PDF: {exc}") from exc

    images = []
    try:
        for page in doc.pages(0, min(len(doc), max_pages)):
            images.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    finally:
        doc.close()

    if not images:
        raise VisionError("В PDF нет страниц")
    return images


def prepare(png: bytes) -> bytes:
    """Мелкое изображение — увеличить и поднять контраст; крупное не трогать."""
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        return png

    try:
        img = Image.open(io.BytesIO(png))
        if img.width >= MIN_WIDTH:
            return png

        factor = min(MIN_WIDTH / img.width, MAX_WIDTH / img.width, 4)
        img = img.convert("RGB").resize(
            (int(img.width * factor), int(img.height * factor)), Image.LANCZOS)
        img = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
        img = img.filter(ImageFilter.UnsharpMask(radius=3, percent=200, threshold=2))

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:                                            # noqa: BLE001
        return png


def _ask_model(png: bytes, base_url: str, api_key: str, model: str,
               prompt: str = PROMPT) -> str:
    payload = {
        "model": model,
        "max_tokens": 2000,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(png).decode()}},
            ],
        }],
    }
    headers = {"Authorization": f"Bearer {api_key}"} if (api_key or "").strip() else {}

    try:
        response = httpx.post(f"{base_url}/chat/completions", json=payload, headers=headers,
                              timeout=TIMEOUT,
                              trust_env=uses_env_proxy(base_url, LLM_VIA_PROXY))
    except Exception as exc:                                     # noqa: BLE001
        raise VisionError(f"Модель недоступна: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        body = (response.text or "")[:300]
        # Модель без vision отвечает отказом именно про картинку — говорим это
        # прямо, иначе оператор видит голый код ошибки и не понимает причины.
        if "image" in body.lower() or response.status_code == 400:
            raise VisionError(
                "Модель не принимает изображения — у выбранного провайдера нет "
                f"распознавания. Ответ сервера: HTTP {response.status_code}: {body}")
        raise VisionError(f"Ошибка модели: HTTP {response.status_code}: {body}")

    try:
        return (response.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:                                     # noqa: BLE001
        raise VisionError(f"Непонятный ответ модели: {type(exc).__name__}") from exc


def recognize(file_bytes: bytes, filename: str, max_pages: int = MAX_PAGES) -> list[dict]:
    """Текст по страницам: `[{"page": 1, "text": "..."}]`.

    Изображение — одна «страница». PDF разбирается постранично, каждая страница
    уходит в модель отдельным запросом.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise VisionError(
            f"Формат {suffix or 'без расширения'} не поддерживается. "
            f"Нужен один из: {', '.join(SUPPORTED_SUFFIXES)}")

    endpoint = resolve_endpoint()
    if not endpoint["supported"] or not endpoint["base_url"]:
        raise VisionError(endpoint["detail"] or "Не настроен провайдер LLM")

    images = pdf_to_images(file_bytes, max_pages) if suffix == ".pdf" else [file_bytes]

    pages = []
    for number, image in enumerate(images, 1):
        text = _ask_model(image, endpoint["base_url"], endpoint["api_key"], endpoint["model"])
        pages.append({"page": number, "text": text})
    return pages


def _preview(png: bytes) -> str:
    """Уменьшенная копия страницы как data-URI — чтобы оператор сверил текст."""
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        img = Image.open(io.BytesIO(png))
        if img.width > PREVIEW_WIDTH:
            height = int(img.height * PREVIEW_WIDTH / img.width)
            img = img.resize((PREVIEW_WIDTH, height))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=82)
    except Exception:                                            # noqa: BLE001
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _parse_fields(raw: str) -> dict:
    """JSON из ответа модели → поля. «null», пустая строка и мусор дают None."""
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {name: None for name in FIELD_NAMES}

    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return {name: None for name in FIELD_NAMES}

    fields = {}
    for name in FIELD_NAMES:
        value = parsed.get(name)
        value = "" if value is None else str(value).strip()
        fields[name] = None if value.lower() in ("", "null", "none", "-", "[?]") else value
    return fields


def _confirmed_by_transcript(value: str, transcript: str) -> bool:
    """Есть ли значение в расшифровке той же страницы.

    Поле и расшифровка получены разными запросами, поэтому совпадение — это
    два независимых прочтения одного места. На живом образце поле пришло как
    «Иванова А.А», а в расшифровке стояло «Д. Иванова»: инициалы модель
    домыслила, и такое расхождение оператору надо видеть.
    """
    needle = " ".join((value or "").split()).lower()
    hay = " ".join((transcript or "").split()).lower()
    if not needle or not hay:
        return False
    if needle in hay:
        return True
    # Многословное значение считаем подтверждённым, только если в расшифровке
    # есть каждое его слово: фамилия без инициалов — ещё не подтверждение.
    parts = [part for part in needle.split() if len(part) > 1]
    return bool(parts) and all(part in hay for part in parts)


def recognize_document(file_bytes: bytes, filename: str, max_pages: int = MAX_PAGES) -> dict:
    """Расшифровка по страницам, ключевые поля и уменьшенные копии страниц.

    Поля спрашиваются отдельным запросом на каждую страницу: шапка с ФИО обычно
    на первой, подпись с почтой — на последней. Берётся первое прочитанное
    значение; незаполненное поле остаётся пустым, догадка не подставляется.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise VisionError(
            f"Формат {suffix or 'без расширения'} не поддерживается. "
            f"Нужен один из: {', '.join(SUPPORTED_SUFFIXES)}")

    endpoint = resolve_endpoint()
    if not endpoint["supported"] or not endpoint["base_url"]:
        raise VisionError(endpoint["detail"] or "Не настроен провайдер LLM")

    base_url, api_key, model = endpoint["base_url"], endpoint["api_key"], endpoint["model"]
    images = pdf_to_images(file_bytes, max_pages) if suffix == ".pdf" else [file_bytes]
    images = [prepare(image) for image in images]

    pages, fields = [], {name: None for name in FIELD_NAMES}
    for number, image in enumerate(images, 1):
        text = _ask_model(image, base_url, api_key, model)
        pages.append({"page": number, "text": text, "image": _preview(image)})

        if any(value is None for value in fields.values()):
            found = _parse_fields(_ask_model(image, base_url, api_key, model, FIELDS_PROMPT))
            for name, value in found.items():
                if fields[name] is None and value:
                    # В шапке ФИО стоит в родительном падеже: «от Петровой Марии
                    # Ивановны». На прикладной стороне по нему ищут заявителя,
                    # поэтому приводим к именительному, как в остальном пайплайне.
                    if name == "applicant_fio":
                        value = normalize_fio(to_nominative(value)) or value
                    fields[name] = value

    transcript = "\n".join(page["text"] for page in pages)
    confirmed = {
        name: (None if not value else _confirmed_by_transcript(value, transcript))
        for name, value in fields.items()
    }
    return {"pages": pages, "fields": fields, "confirmed": confirmed}

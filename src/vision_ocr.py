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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx

from config import LLM_VIA_PROXY
from llm_check import resolve_endpoint
from proxy_policy import uses_env_proxy

# Сколько страниц скана разбирать за один раз. Каждая страница — отдельный
# вызов модели на десятки секунд, поэтому предел нужен не для красоты.
MAX_PAGES = 10

# Плотность рендера страницы PDF. 150 dpi — компромисс: мельче режет мелкий
# рукописный текст, крупнее раздувает картинку и время ответа.
PDF_DPI = 150

TIMEOUT = 180.0

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES + (".pdf",)

PROMPT = ("Распознай текст с изображения. Выведи только сам текст, построчно, "
          "без комментариев. Если текста нет, ответь: (пусто)")


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


def _ask_model(png: bytes, base_url: str, api_key: str, model: str) -> str:
    payload = {
        "model": model,
        "max_tokens": 2000,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
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

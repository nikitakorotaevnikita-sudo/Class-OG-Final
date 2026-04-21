"""
Модуль извлечения текста из файлов обращений.
Поддержка: TXT (UTF-8/CP1251), PDF (текстовые).

Зависимости:
    - PyMuPDF (для PDF)

Ограничения MVP:
    - Только текстовые PDF (не сканы)
    - Максимум 5000 символов
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import logging

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 5000
SUPPORTED_FORMATS = {".txt", ".pdf"}


class TextExtractionError(Exception):
    """Ошибка извлечения текста"""
    pass


class ScanNotSupportedError(TextExtractionError):
    """PDF является сканом без текста"""
    pass


def extract_text(file_bytes: bytes, filename: str) -> tuple[str, bool]:
    """
    Извлечь текст из файла.
    
    Args:
        file_bytes: Содержимое файла
        filename: Имя файла (для определения формата)
    
    Returns:
        Кортеж: (текст, было ли обрезано)
    
    Raises:
        TextExtractionError: Формат не поддерживается
        ScanNotSupportedError: PDF является сканом без текста
    """
    ext = Path(filename).suffix.lower()
    
    if ext not in SUPPORTED_FORMATS:
        raise TextExtractionError(
            f"Формат '{ext}' не поддерживается. "
            f"Используйте: {', '.join(SUPPORTED_FORMATS)}"
        )
    
    if ext == ".txt":
        text = _extract_txt(file_bytes)
    elif ext == ".pdf":
        text = _extract_pdf(file_bytes)
    else:
        raise TextExtractionError(f"Формат не поддерживается: {ext}")
    
    text = text.strip()
    
    truncated = False
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]
        truncated = True
        logger.warning(f"Текст обрезан до {MAX_TEXT_LENGTH} символов")
    
    return text, truncated


def _extract_txt(file_bytes: bytes) -> str:
    """Извлечь текст из TXT с автоопределением кодировки"""
    encodings_to_try = ["utf-8", "cp1251", "koi8-r", "iso-8859-5"]
    
    for encoding in encodings_to_try:
        try:
            return file_bytes.decode(encoding)
        except (UnicodeDecodeError, AttributeError):
            continue
    
    return file_bytes.decode("utf-8", errors="replace")


def _extract_pdf(file_bytes: bytes) -> str:
    """Извлечь текст из PDF"""
    try:
        import fitz
    except ImportError:
        raise ImportError(
            "PyMuPDF не установлен. Установите: pip install PyMuPDF"
        )
    
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        raise TextExtractionError(f"Не удалось открыть PDF: {e}")
    
    if doc.page_count == 0:
        raise TextExtractionError("PDF файл пуст")
    
    text_parts = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        page_text = page.get_text()
        text_parts.append(page_text)
    
    doc.close()
    
    full_text = "\n".join(text_parts)
    
    if not full_text.strip():
        raise ScanNotSupportedError(
            "PDF содержит только изображения (скан). "
            "Для сканированных документов используйте OCR."
        )
    
    return full_text


def validate_file_size(file_size: int, max_size_mb: int = 5) -> None:
    """
    Проверить размер файла.
    
    Args:
        file_size: Размер в байтах
        max_size_mb: Максимальный размер в МБ
    
    Raises:
        TextExtractionError: Файл слишком большой
    """
    max_bytes = max_size_mb * 1024 * 1024
    if file_size > max_bytes:
        raise TextExtractionError(
            f"Файл слишком большой (максимум: {max_size_mb} МБ)"
        )


def get_supported_formats() -> set:
    """Вернуть список поддерживаемых форматов"""
    return SUPPORTED_FORMATS.copy()
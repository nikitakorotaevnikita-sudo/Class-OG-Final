"""Клиент RX OData: получение текста документа по id (pull-модель)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from config import RX_ODATA_URL, RX_USER, RX_PASSWORD
from text_extractor import extract_text


class DocumentNotFound(Exception):
    """Документ с указанным id не найден в RX."""


class BodyFetchError(Exception):
    """Не удалось получить тело документа из RX."""


def build_client() -> httpx.Client:
    """Фабрика HTTP-клиента (монкипатчится в тестах)."""
    return httpx.Client(auth=(RX_USER, RX_PASSWORD), timeout=60.0, verify=False)


def _last_version_id(versions: list) -> int:
    return max(versions, key=lambda v: v.get("Number", 0))["Id"]


def _fetch_body(client: httpx.Client, document_id: int, version_id: int) -> bytes:
    """Скачать бинарное тело версии. Точка отладки OData-500 (см. Task 6)."""
    url = f"{RX_ODATA_URL}/IElectronicDocuments({document_id})/Versions({version_id})/Body/$value"
    r = client.get(url)
    if r.status_code == 200 and r.content:
        return r.content
    raise BodyFetchError(f"HTTP {r.status_code}: {r.text[:200]}")


def _filename_for(body: bytes, document_id: int) -> str:
    """Определяем расширение по сигнатуре, чтобы text_extractor выбрал парсер."""
    ext = ".pdf" if body[:4] == b"%PDF" else ".txt"
    return f"doc{document_id}{ext}"


def get_document_text(document_id: int) -> tuple[str, str]:
    """Вернуть (текст, имя_файла) для документа RX по id."""
    with build_client() as client:
        meta_url = f"{RX_ODATA_URL}/IElectronicDocuments({document_id})"
        r = client.get(meta_url, params={"$expand": "Versions($select=Id,Number)"})
        if r.status_code == 404:
            raise DocumentNotFound(f"Документ {document_id} не найден в RX")
        if r.status_code != 200:
            raise BodyFetchError(f"HTTP {r.status_code} при запросе документа")

        doc = r.json()
        versions = doc.get("Versions") or []
        if not versions:
            raise BodyFetchError(f"Документ {document_id} не имеет версий")

        version_id = _last_version_id(versions)
        body = _fetch_body(client, document_id, version_id)
        filename = _filename_for(body, document_id)
        text, _ = extract_text(body, filename)
        return text, filename

"""
annotations_storage.py — пользовательские аннотации к записям классификатора.

Аннотации хранятся в БД (classifier_vectors, source_type='annotation'):
одна строка на текст. Новые строки привязываются к АКТИВНОЙ версии модели,
эмбеддинг считает воркер (embed_annotation) или build_vectordb.

Старые поля-«словарь» (correct_name / category / note) хранятся как тексты
с префиксом "<поле>: <значение>" и учитываются в build_search_text.

Используется из finetune_model.py, operator_cli.py, appeals_logger.py, api_server.py.
"""

from __future__ import annotations

from typing import Optional

from storage import get_storage

# Поля старого словарного формата, которые влияют на search_text
_LEGACY_FIELDS = ("correct_name", "category", "note")


def _store(storage=None):
    return (storage or get_storage()).classifier


def _active_version(storage=None) -> int:
    active = (storage or get_storage()).models.active()
    if active is None:
        raise RuntimeError(
            "Нет активной модели в ml_model_registry — запустите scripts/db/migrate_from_files.py"
        )
    return active.version


# ── 1. Добавление аннотации ──────────────────────────────────────────────────

def add_annotation(code: str, text: str, case_id: Optional[str] = None,
                   operator_code: str = "", *, storage=None) -> int:
    """
    Добавляет текст аннотации к записи code (версия модели — активная).

    case_id / operator_code принимаются для совместимости вызовов, в БД не хранятся.
    Возвращает id строки classifier_vectors.
    """
    return _store(storage).add_annotation(code, text, _active_version(storage))


# ── 2. Сохранение поля старого формата ───────────────────────────────────────

def save_annotation(code: str, field: str, value: str, *, storage=None) -> None:
    """
    Сохраняет (заменяет) поле field аннотации для записи code.

    Args:
        code:  код записи классификатора
        field: имя поля ("note", "correct_name", "category", …)
        value: значение (строка)
    """
    _store(storage).delete_annotations(code, f"{field}: ")
    add_annotation(code, f"{field}: {value}", storage=storage)


# ── 3. Получение всех аннотаций для кода ─────────────────────────────────────

def get_annotations(code: str, *, storage=None) -> list[str]:
    """Тексты всех аннотаций записи code (по порядку добавления); [] если нет."""
    return _store(storage).annotations_for(code)


# ── 4. Удаление аннотации ────────────────────────────────────────────────────

def delete_annotation(code: str, field: Optional[str] = None, *, storage=None) -> None:
    """
    Удаляет поле field (строки "<field>: ...") или все аннотации записи (field=None).
    """
    _store(storage).delete_annotations(code, None if field is None else f"{field}: ")


# ── 5. Список кодов с аннотациями ─────────────────────────────────────────────

def list_annotated_codes(*, storage=None) -> list[str]:
    """Отсортированный список кодов, для которых есть хотя бы одна аннотация."""
    return _store(storage).annotated_codes()


# ── 6. Построение расширенного search_text с учётом аннотаций ───────────────

def _legacy_fields(texts: list[str]) -> dict:
    """Поля старого формата из текстов "<поле>: <значение>" (последнее значение побеждает)."""
    fields: dict[str, str] = {}
    for text in texts:
        for name in _LEGACY_FIELDS:
            prefix = f"{name}: "
            if text.startswith(prefix):
                fields[name] = text[len(prefix):]
    return fields


def build_search_text(code: str, base_name: str, base_full_path: str,
                      annotations: Optional[list[str]] = None, *, storage=None) -> str:
    """
    Формирует расширенный search_text для записи классификатора.

    Если для code есть аннотация correct_name — она подменяет base_name
    в итоговой строке. Также дописывает category и note (если есть).

    Args:
        code:           код записи
        base_name:      название записи классификатора
        base_full_path: полный путь записи
        annotations:    тексты аннотаций, если уже загружены (иначе читаются из БД)

    Returns:
        Строку вида:
          "<correct_name or name>. <full_path>"
          "#category: <category>"   (если есть)
          "Note: <note>"             (если есть)
    """
    if annotations is None:
        annotations = get_annotations(code, storage=storage)
    ann = _legacy_fields(annotations)

    # Основной текст: prefer correct_name
    name = ann.get("correct_name", base_name)
    primary = f"{name}. {base_full_path}"

    parts = [primary]

    if "category" in ann:
        parts.append(f"#category: {ann['category']}")
    if "note" in ann:
        parts.append(f"Note: {ann['note']}")

    return " | ".join(parts)

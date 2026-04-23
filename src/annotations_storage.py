"""
annotations_storage.py — хранение и управление пользовательскими аннотациями
к записям классификатора обращений граждан.

Аннотации хранятся в data/classifier_annotations.json как словарь:
    {<code>: {<field>: <value>, ...}, ...}

Запуск: используется из finetune_model.py и operator_cli.py
"""

import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import CLASSIFIER_FLAT_PATH, VECTOR_DB_DIR, MODELS_DIR

# ── Путь к файлу аннотаций ───────────────────────────────────────────────────
ANNOTATIONS_PATH = Path(__file__).parent.parent / "data" / "classifier_annotations.json"


def _load_annotations() -> dict:
    """Внутренняя: загружает JSON-файл аннотаций. Возвращает {} при отсутствии файла."""
    if not ANNOTATIONS_PATH.exists():
        return {}
    with open(ANNOTATIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_annotations(data: dict) -> None:
    """Внутренняя: сохраняет словарь аннотаций в JSON-файл."""
    with open(ANNOTATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 1. Сохранение аннотации ──────────────────────────────────────────────────

def save_annotation(code: str, field: str, value: str) -> None:
    """
    Сохраняет (или обновляет) поле field аннотации для записи code.

    Args:
        code:  код записи классификатора (напр. "03.01.01.001")
        field: имя поля ("note", "correct_name", "category", …)
        value: значение (строка)
    """
    data = _load_annotations()
    if code not in data:
        data[code] = {"_updated": datetime.now().isoformat(timespec="seconds")}
    data[code][field] = value
    data[code]["_updated"] = datetime.now().isoformat(timespec="seconds")
    _save_annotations(data)


# ── 2. Получение всех аннотаций для кода ─────────────────────────────────────

def get_annotations(code: str) -> dict:
    """
    Возвращает все аннотации для записи code.
    Если записей нет — пустой словарь.

    Returns:
        dict с полями аннотации, ключ _updated содержит ISO-дату последнего изменения
    """
    data = _load_annotations()
    return data.get(code, {})


# ── 3. Удаление аннотации ────────────────────────────────────────────────────

def delete_annotation(code: str, field: Optional[str] = None) -> None:
    """
    Удаляет конкретное поле или всю запись аннотации.

    Args:
        code:  код записи классификатора
        field: если указано — удалить только это поле; если None — удалить всю запись
    """
    data = _load_annotations()
    if code not in data:
        return

    if field is None:
        del data[code]
    else:
        data[code].pop(field, None)
        # если остались только служебные поля — удаляем запись целиком
        if not any(k for k in data[code] if not k.startswith("_")):
            del data[code]

    _save_annotations(data)


# ── 4. Список кодов с аннотациями ─────────────────────────────────────────────

def list_annotated_codes() -> list[str]:
    """
    Возвращает список кодов, для которых есть хотя бы одна пользовательская аннотация.
    Служебные ключи (_updated) не учитываются.
    """
    data = _load_annotations()
    result = []
    for code, fields in data.items():
        if any(not k.startswith("_") for k in fields):
            result.append(code)
    return sorted(result)


# ── 5. Построение расширенного search_text с учётом аннотаций ───────────────

def build_search_text(code: str, base_name: str, base_full_path: str) -> str:
    """
    Формирует расширенный search_text для записи классификатора.

    Если для code есть аннотация correct_name — она подменяет base_name
    в итоговой строке. Также дописывает category и note (если есть).

    Args:
        code:         код записи
        base_name:    название из classifier_flat.json
        base_full_path: полный путь из metadata.json

    Returns:
        Строку вида:
          "<correct_name or name>. <full_path>"
          "#category: <category>"   (если есть)
          "Note: <note>"             (если есть)
    """
    ann = get_annotations(code)

    # Основной текст: prefer correct_name
    name = ann.get("correct_name", base_name)
    primary = f"{name}. {base_full_path}"

    parts = [primary]

    if "category" in ann:
        parts.append(f"#category: {ann['category']}")
    if "note" in ann:
        parts.append(f"Note: {ann['note']}")

    return " | ".join(parts)


# ── Дополнительная функция: бэкап аннотаций ─────────────────────────────────

def backup_annotations(dest_dir: Optional[Path] = None) -> Path:
    """
    Создаёт бэкап-копию classifier_annotations.json.

    Args:
        dest_dir: папка для бэкапа (по умолчанию — MODELS_DIR / "annotation_backups")

    Returns:
        Path к созданному бэкап-файлу
    """
    if not ANNOTATIONS_PATH.exists():
        raise FileNotFoundError(f"Аннотации не найдены: {ANNOTATIONS_PATH}")

    dest = dest_dir or (Path(MODELS_DIR) / "annotation_backups")
    dest.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = dest / f"classifier_annotations_{timestamp}.json"

    shutil.copy2(ANNOTATIONS_PATH, backup_path)
    return backup_path
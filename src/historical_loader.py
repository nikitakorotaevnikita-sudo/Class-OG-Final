"""historical_loader.py — Парсинг и валидация исторических данных"""

import json
import pandas as pd
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).parent))
from config import CLASSIFIER_FLAT_PATH


def load_classifier_codes() -> set:
    """Загрузить все коды из classifier_flat.json"""
    with open(CLASSIFIER_FLAT_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    return {e["code"] for e in entries}


def parse_file(file_path: str) -> list[dict]:
    """
    Парсит файл любого поддерживаемого формата.

    Args:
        file_path: путь к файлу (.xlsx, .xls, .csv, .json)

    Returns:
        list[dict]: [{appeal_text, assigned_code, specialist, date}]
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(file_path)
    elif suffix == ".csv":
        df = pd.read_csv(file_path)
    elif suffix == ".json":
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            raise ValueError("JSON must contain array of records")
    else:
        raise ValueError(f"Unsupported file format: {suffix}")

    # Normalize column names
    df.columns = df.columns.str.lower().str.strip()

    # Map columns
    column_map = {
        "appeal_text": ["appeal_text", "text", "текст", "обращение"],
        "assigned_code": ["assigned_code", "code", "код", "код_классификатора"],
        "specialist": ["specialist", "specialist_name", "специалист", "фио"],
        "date": ["date", "дата", "registration_date"]
    }

    result = {}
    for target, variants in column_map.items():
        for var in variants:
            if var in df.columns:
                result[target] = df[var].tolist()
                break

    if "appeal_text" not in result or "assigned_code" not in result:
        raise ValueError("Required columns 'appeal_text' and 'assigned_code' not found")

    # Build list of dicts
    records = []
    for i in range(len(result["appeal_text"])):
        record = {
            "appeal_text": str(result["appeal_text"][i]) if i < len(result.get("appeal_text", [])) else "",
            "assigned_code": str(result["assigned_code"][i]) if i < len(result.get("assigned_code", [])) else "",
        }
        if "specialist" in result and i < len(result["specialist"]):
            record["specialist"] = str(result["specialist"][i]) if pd.notna(result["specialist"][i]) else None
        if "date" in result and i < len(result["date"]):
            record["date"] = str(result["date"][i]) if pd.notna(result["date"][i]) else None
        records.append(record)

    return records


class ValidationResult:
    def __init__(self, valid_records, invalid_records, errors, stats):
        self.valid_records = valid_records
        self.invalid_records = invalid_records
        self.errors = errors
        self.stats = stats

    def to_dict(self):
        return {
            "valid_records": self.valid_records,
            "invalid_records": self.invalid_records,
            "errors": self.errors,
            "stats": self.stats
        }


def validate_codes(records: list[dict]) -> ValidationResult:
    """
    Валидирует коды по classifier_flat.json.

    Returns:
        ValidationResult with valid_records, invalid_records, errors, stats
    """
    valid_codes = load_classifier_codes()

    valid_records = []
    invalid_records = []
    errors = []

    for i, record in enumerate(records):
        code = record.get("assigned_code", "").strip()

        if not code:
            errors.append({"row": i + 1, "code": "", "error": "Пустой код"})
            invalid_records.append(record)
        elif code not in valid_codes:
            errors.append({"row": i + 1, "code": code, "error": "Код не найден в классификаторе"})
            invalid_records.append(record)
        elif len(code.split(".")) != 4:
            errors.append({"row": i + 1, "code": code, "error": "Неверный формат кода (ожидается XXXX.XXXX.XXXX.XXXX)"})
            invalid_records.append(record)
        else:
            valid_records.append(record)

    stats = {
        "total": len(records),
        "valid": len(valid_records),
        "invalid": len(invalid_records)
    }

    return ValidationResult(valid_records, invalid_records, errors, stats)


HISTORICAL_FILE = Path("data/historical_verified.jsonl")


def save_to_historical_jsonl(records: list[dict], source_file: str) -> str:
    """
    Сохраняет валидные записи в data/historical_verified.jsonl

    Returns:
        путь к файлу
    """
    HISTORICAL_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(HISTORICAL_FILE, "a", encoding="utf-8") as f:
        for record in records:
            entry = {
                "source": source_file,
                "appeal_text": record.get("appeal_text", ""),
                "assigned_code": record.get("assigned_code", ""),
                "specialist": record.get("specialist"),
                "date": record.get("date"),
                "timestamp": pd.Timestamp.now().isoformat()
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return str(HISTORICAL_FILE)
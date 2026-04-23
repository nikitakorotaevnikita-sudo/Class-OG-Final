# EPIC-07: Загрузка исторических данных — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Загрузка исторических данных (обращения + коды от специалистов) → парсинг → валидация → fine-tuning → улучшенная модель.

**Architecture:** Три источника данных (UI, папка, Directum API). Модуль historical_loader.py парсит все форматы. API endpoint для загрузки. UI страница с предпросмотром и валидацией. Fine-tuning на исторических данных.

**Tech Stack:** Python 3.11, pandas, openpyxl, FastAPI, HTML/JS

---

## File Structure

```
src/
  historical_loader.py              NEW - парсинг и валидация
  auto_import_historical.py        NEW - слежение за папкой
  api_server.py                   MODIFY - endpoints для загрузки
  finetune_model.py               MODIFY - поддержка historical source

src/static/
  historical.html                  NEW - UI страница
  historical.js                    NEW - JS для страницы

data/
  historical/                      NEW - входная папка
    README.md                     NEW - инструкция
  historical_verified.jsonl       MODIFY - сюда сохраняем
```

---

## Task 1: historical_loader.py

**Files:**
- Create: `src/historical_loader.py`
- Create: `tests/test_historical_loader.py`

- [ ] **Step 1: Create src/historical_loader.py**

```python
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
```

- [ ] **Step 2: Create tests/test_historical_loader.py**

```python
"""Tests for historical_loader.py"""

import pytest
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from historical_loader import parse_file, validate_codes, save_to_historical_jsonl, ValidationResult


def test_parse_csv(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("appeal_text,assigned_code,specialist,date\nне вывозят мусор,0005.0005.0056.1160,Петрова,2025-01-01\n", encoding="utf-8")

    records = parse_file(str(csv_file))
    assert len(records) == 1
    assert records[0]["appeal_text"] == "не вывозят мусор"
    assert records[0]["assigned_code"] == "0005.0005.0056.1160"


def test_validate_codes_valid(tmp_path):
    records = [{"appeal_text": "текст", "assigned_code": "0005.0005.0056.1160"}]
    result = validate_codes(records)
    assert result.stats["valid"] == 1
    assert result.stats["invalid"] == 0


def test_validate_codes_invalid(tmp_path):
    records = [{"appeal_text": "текст", "assigned_code": "9999.9999.9999.9999"}]
    result = validate_codes(records)
    assert result.stats["valid"] == 0
    assert result.stats["invalid"] == 1
    assert len(result.errors) == 1


def test_save_to_historical_jsonl(tmp_path, monkeypatch):
    hist_file = tmp_path / "historical_verified.jsonl"
    monkeypatch.setattr("historical_loader.HISTORICAL_FILE", hist_file)

    records = [{"appeal_text": "текст", "assigned_code": "0005.0005.0056.1160"}]
    path = save_to_historical_jsonl(records, "test.csv")

    assert hist_file.exists()
    with open(hist_file, encoding="utf-8") as f:
        line = f.readline()
        data = json.loads(line)
        assert data["appeal_text"] == "текст"
        assert data["assigned_code"] == "0005.0005.0056.1160"
```

- [ ] **Step 3: Run tests**

```bash
cd "c:/Users/Администратор/Desktop/Class OG Final"
pytest tests/test_historical_loader.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/historical_loader.py tests/test_historical_loader.py
git commit -m "feat: add historical_loader.py for parsing and validating historical data"
```

---

## Task 2: API endpoints

**Files:**
- Modify: `src/api_server.py`

- [ ] **Step 1: Add upload endpoint to api_server.py**

Add these imports:
```python
from fastapi import UploadFile, File, HTTPException
from historical_loader import parse_file, validate_codes
```

Add request model:
```python
class UploadHistoricalResponse(BaseModel):
    status: Literal["ok", "validation_errors"]
    filename: str
    stats: dict  # {total, valid, invalid}
    errors: list[dict]
    preview: list[dict] | None
```

Add endpoint:
```python
@app.post("/api/upload-historical")
async def upload_historical(file: UploadFile = File(...)):
    """Загрузить файл исторических данных для валидации"""
    if file.size > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size > 50MB")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv", ".json"):
        raise HTTPException(status_code=400, detail="Unsupported format")

    # Save temp file
    temp_path = Path("data/temp_upload") / file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        records = parse_file(str(temp_path))
        result = validate_codes(records)
        response = {
            "status": "ok" if result.stats["invalid"] == 0 else "validation_errors",
            "filename": file.filename,
            "stats": result.stats,
            "errors": result.errors,
            "preview": result.valid_records[:5] if result.valid_records else []
        }
    finally:
        temp_path.unlink(missing_ok=True)

    return response
```

Add confirm endpoint:
```python
@app.post("/api/confirm-historical")
async def confirm_historical(request: dict):
    """Подтвердить и сохранить валидные записи"""
    # Implementation saves records to historical_verified.jsonl
    pass
```

- [ ] **Step 2: Test endpoint**

```bash
cd "c:/Users/Администратор/Desktop/Class OG Final"
python -c "from src.api_server import app; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/api_server.py
git commit -m "feat: add /api/upload-historical endpoint for file upload"
```

---

## Task 3: UI page

**Files:**
- Create: `src/static/historical.html`
- Create: `src/static/historical.js`

- [ ] **Step 1: Create src/static/historical.html**

Full HTML page with:
- Drag & drop upload area
- File preview table
- Validation summary (3 cards)
- Action buttons
- Fine-tuning result section

- [ ] **Step 2: Create src/static/historical.js**

JavaScript for:
- File upload handling
- API calls to /api/upload-historical
- Preview rendering
- Confirm button handler

- [ ] **Step 3: Test page**

```bash
# Start server and open http://localhost:8000/static/historical.html
```

- [ ] **Step 4: Commit**

```bash
git add src/static/historical.html src/static/historical.js
git commit -m "feat: add historical.html UI page for uploading historical data"
```

---

## Task 4: Fine-tuning integration

**Files:**
- Modify: `src/finetune_model.py`

- [ ] **Step 1: Add historical data loading**

Add function:
```python
def load_historical_examples(path: str = "data/historical_verified.jsonl") -> list:
    """Load historical data and return training examples"""
    # Read jsonl, build (query, passage) pairs
    pass
```

- [ ] **Step 2: Modify main() to accept --source**

Add argparse:
```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--source", choices=["verified", "historical", "combined"], default="verified")
args = parser.parse_args()
```

- [ ] **Step 3: Commit**

```bash
git add src/finetune_model.py
git commit -m "feat: add historical data source to finetune_model.py"
```

---

## Task 5: Auto-import script

**Files:**
- Create: `src/auto_import_historical.py`
- Create: `data/historical/README.md`

- [ ] **Step 1: Create auto_import_historical.py**

Script that:
- Scans data/historical/ for files
- Parses and validates
- Moves to processed/ or errors/
- Optionally watches for new files (--watch flag)

- [ ] **Step 2: Create README**

Instructions for using the folder import

- [ ] **Step 3: Commit**

```bash
git add src/auto_import_historical.py data/historical/README.md
git commit -m "feat: add auto_import_historical.py for folder watching"
```

---

## Spec coverage check

- [x] TICKET-28: historical_loader.py → Task 1
- [x] TICKET-29: API endpoints → Task 2
- [x] TICKET-30: UI page → Task 3
- [x] TICKET-31: fine-tuning integration → Task 4
- [x] TICKET-32: folder auto-import → Task 5
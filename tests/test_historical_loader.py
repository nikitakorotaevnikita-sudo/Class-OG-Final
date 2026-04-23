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
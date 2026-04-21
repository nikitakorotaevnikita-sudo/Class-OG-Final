"""
Базовые тесты поиска по классификатору.
Запуск: pytest tests/
"""

import json
import pytest
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="module")
def flat_entries():
    with open(DATA_DIR / "classifier_flat.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def hierarchy():
    with open(DATA_DIR / "classifier_hierarchy.json", encoding="utf-8") as f:
        return json.load(f)


def test_flat_entries_count(flat_entries):
    """Классификатор должен содержать ровно 2108 записей"""
    assert len(flat_entries) == 2108


def test_flat_entries_have_required_fields(flat_entries):
    """Каждая запись должна содержать обязательные поля"""
    required = {"code", "name", "level", "full_path", "search_text"}
    for entry in flat_entries[:50]:
        assert required.issubset(entry.keys()), f"Missing fields in {entry['code']}"


def test_level_distribution(flat_entries):
    """Проверяем распределение по уровням"""
    from collections import Counter
    levels = Counter(e["level"] for e in flat_entries)
    assert levels[1] == 5,    "Должно быть 5 разделов верхнего уровня"
    assert levels[2] == 21,   "Должно быть 21 подраздел"
    assert levels[4] >= 1000, "Должно быть более 1000 вопросов 4-го уровня"


def test_root_sections(flat_entries):
    """Проверяем наличие 5 основных разделов"""
    root_codes = {e["code"] for e in flat_entries if e["level"] == 1}
    expected = {
        "0001.0000.0000.0000",
        "0002.0000.0000.0000",
        "0003.0000.0000.0000",
        "0004.0000.0000.0000",
        "0005.0000.0000.0000",
    }
    assert root_codes == expected


def test_hierarchy_has_five_roots(hierarchy):
    """Иерархия должна содержать 5 корневых разделов"""
    assert len(hierarchy) == 5


def test_all_entries_have_nonempty_name(flat_entries):
    """Ни одна запись не должна иметь пустое наименование"""
    empty = [e["code"] for e in flat_entries if not e["name"].strip()]
    assert not empty, f"Записи с пустым наименованием: {empty}"

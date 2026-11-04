"""xlsx-шаблон ручной проверки агента ArioCowork оператором.

Тексты обращений копируются в шаблон для вставки в чат агента, но в консоль не
печатаются: это персональные данные (59-ФЗ).
"""

import json
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_cowork_check_template as tmpl

VET = "0004.0015.0158.0974"
PER_VARIANT = ["Коды агента", "Совпало с эталоном", "Время, с", "История использована",
               "Коды вне классификатора", "Комментарий"]


@pytest.fixture()
def sheet(tmp_path):
    out = tmp_path / "check.xlsx"
    count = tmpl.make_template(out)
    return count, openpyxl.load_workbook(out)["Проверка"]


def test_has_all_cases(sheet):
    count, ws = sheet
    assert count == 16
    assert ws.max_row == 17


def test_headers(sheet):
    _, ws = sheet
    headers = [c.value for c in ws[1]]
    assert headers[:5] == ["id", "Набор", "Эталонные коды", "Текст для вставки",
                           "Пайплайн (Qwen3 + предобработка)"]
    assert headers[5:] == [f"{v}: {h}" for v in ("A", "B") for h in PER_VARIANT]


def test_vet_expected_code(sheet):
    _, ws = sheet
    rows = {r[0].value: r for r in ws.iter_rows(min_row=2)}
    for i in range(1, 6):
        assert rows[f"vet{i}"][2].value == VET


def test_pipeline_column_blank_without_summary(sheet):
    _, ws = sheet
    assert all(r[4].value in (None, "") for r in ws.iter_rows(min_row=2))


def test_does_not_print_texts(tmp_path, capsys):
    tmpl.make_template(tmp_path / "check.xlsx")
    out = capsys.readouterr().out
    cases = json.loads((ROOT / "tests" / "fixtures" / "test_appeals_real12.json").read_text(encoding="utf-8"))
    assert not any(case["text"][:30] in out for case in cases)
    assert not any(text[:30] in out for text in tmpl.VET_TEXTS)

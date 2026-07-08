import sys
sys.path.insert(0, "src")
import pytest
from fio_extractor import normalize_fio


@pytest.mark.parametrize("raw,expected", [
    ("Иванов Иван Иванович", "Иванов И.И."),
    ("иванов иван иванович", "Иванов И.И."),
    ("Иванов Иван", "Иванов И."),
    ("  Петров   Пётр   Петрович ", "Петров П.П."),
    ("Сидоров С.С.", "Сидоров С.С."),
    ("Иванов", None),
    ("", None),
    (None, None),
    ("12345 !!!", None),
])
def test_normalize_fio(raw, expected):
    assert normalize_fio(raw) == expected

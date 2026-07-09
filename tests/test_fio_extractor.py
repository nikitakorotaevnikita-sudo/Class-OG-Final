import sys
sys.path.insert(0, "src")
import pytest
from fio_extractor import normalize_fio


@pytest.mark.parametrize("raw,expected", [
    ("Иванов Иван Иванович", "Иванов Иван Иванович"),
    ("иванов иван иванович", "Иванов Иван Иванович"),
    ("Иванов Иван", "Иванов Иван"),
    ("  Петров   Пётр   Петрович ", "Петров Пётр Петрович"),
    ("Сидоров С.С.", "Сидоров С.С."),
    ("Владленов Игорь Михайлович", "Владленов Игорь Михайлович"),
    ("Иванов", None),
    ("", None),
    (None, None),
    ("12345 !!!", None),
    ("poop@puck.pas", None),
])
def test_normalize_fio(raw, expected):
    assert normalize_fio(raw) == expected

"""Детектор повторного обращения (regex-маркеры)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import detect_repeat_markers


def test_positive_markers():
    positives = [
        "Обращаюсь к вам не в первый раз по поводу мусора",
        "Обращаюсь повторно, ситуация не изменилась",
        "Ответа так и не получил на прошлое письмо",
        "Ранее обращался по этому вопросу, реакции нет",
        "На моё предыдущее обращение ответ не пришёл",
        "Уже писала вам об этом месяц назад",
    ]
    for t in positives:
        assert detect_repeat_markers(t), f"должно детектироваться: {t}"


def test_negative_no_markers():
    negatives = [
        "Прошу отремонтировать дорогу по улице Ленина",
        "Не вывозят мусор две недели, контейнеры переполнены",
        "Прошу разъяснить порядок оформления субсидии",
    ]
    for t in negatives:
        assert not detect_repeat_markers(t), f"НЕ должно детектироваться: {t}"


def test_empty_and_none():
    assert not detect_repeat_markers("")
    assert not detect_repeat_markers(None)

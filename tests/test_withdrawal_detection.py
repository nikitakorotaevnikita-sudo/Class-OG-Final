"""Детектор просьбы прекратить рассмотрение / отозвать обращение (regex-маркеры).

Код 0001.0002.0027.0131 «Прекращение рассмотрения обращения».
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import detect_withdrawal_markers


def test_positive_stop_consideration():
    positives = [
        "Прошу прекратить рассмотрение моего обращения от 12.05.2026",
        "Прошу отозвать моё обращение, вопрос решён",
        "Отзываю своё заявление, направленное ранее",
        "Прошу снять с рассмотрения мою жалобу",
        "Прошу не рассматривать моё обращение",
        "Отказываюсь от ранее поданной жалобы",
        "Прошу аннулировать обращение №231003059",
        "Вопрос урегулирован, прошу закрыть обращение",
        "Прошу отозвать письмо, направленное в ваш адрес",
    ]
    for text in positives:
        assert detect_withdrawal_markers(text), f"должно детектироваться: {text}"


def test_negative_similar_words_but_other_meaning():
    """«Прекратить» и «отозвать» встречаются и в обычных обращениях — не ловить их."""
    negatives = [
        "Прошу прекратить незаконное строительство во дворе",
        "Прошу отозвать лицензию у управляющей компании",
        "Прошу рассмотреть моё обращение и дать ответ",
        "Прошу прекратить сброс сточных вод в реку",
        "Требую отозвать некачественный товар из продажи",
        "Не вывозят мусор две недели, контейнеры переполнены",
    ]
    for text in negatives:
        assert not detect_withdrawal_markers(text), f"НЕ должно детектироваться: {text}"


def test_empty_and_none():
    assert not detect_withdrawal_markers("")
    assert not detect_withdrawal_markers(None)

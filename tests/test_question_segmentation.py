"""
Tests for _split_appeal_questions() — conservative deterministic segmenter.

Covers:
- Single-question appeals stay as ONE segment
- Numbered markers (1. / 2) / 3) ) create separate segments
- Verbal markers (во-первых, во-вторых, кроме того) create segments
- Preamble/header without explicit verbs is folded into next segment
- Real-world regression: 5-question appeal from operator
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import split_appeal_questions, AppealQuestion


def test_single_question_stays_one_segment():
    text = "Прошу провести ремонт дорожного покрытия по улице Ленина у дома 15."
    segments = split_appeal_questions(text)
    assert len(segments) == 1
    assert segments[0].text == text
    assert segments[0].ordinal == 1


def test_numbered_markers_split():
    text = (
        "1. Прошу провести ремонт дороги по улице Ленина.\n"
        "2. Также жалуюсь на вывоз мусора по адресу Гагарина 7.\n"
        "3. Прошу разъяснить порядок субсидии ЖКУ."
    )
    segments = split_appeal_questions(text)
    assert len(segments) == 3
    assert "ремонт дороги" in segments[0].text.lower()
    assert "мусор" in segments[1].text.lower()
    assert "субсиди" in segments[2].text.lower()


def test_verbal_markers_split():
    text = (
        "Во-первых, прошу разъяснить порядок оформления субсидии. "
        "Во-вторых, прошу провести проверку начислений за ЖКУ."
    )
    segments = split_appeal_questions(text)
    assert len(segments) == 2


def test_preamble_folded_into_first_question():
    text = (
        "Заявитель повторно обращается!!\n\n"
        "1. Необходима доставка лекарств на дом.\n"
        "2. О бесплатном социальном работнике."
    )
    segments = split_appeal_questions(text)
    assert len(segments) == 2
    assert "лекарств" in segments[0].text.lower()
    assert "социальном работник" in segments[1].text.lower() or "социальном" in segments[1].text.lower()


def test_max_segments_capped():
    text = "\n".join(f"{i}. Вопрос номер {i} прошу рассмотреть." for i in range(1, 20))
    segments = split_appeal_questions(text)
    assert 1 <= len(segments) <= 7


def test_regression_5question_appeal():
    """Real failing case: 5 distinct topics, all classified as fallback code."""
    text = """Заявитель повторно обращается!!

1.Необходима доставка лекарств на дом (инсулин). Звоню в Городскую поликлинику №5, даже инсулин привести некому.

2. О бесплатном социально работнике. Заявитель сама себя обслуживает, на улицу выйти не может, так как ничего не видит.

3. Об оказании помощи в настройке телефона и обучении пользованию цифровыми сервисами.

15 летний внук оставил меня, переехал в деревню. У его мамы (Яны) онкозаболевание, она растит мальчика. Яну сняли с группы инвалидности в городской поликлинике № 5.

Сейчас живем на мою пенсию. У внука даже нет проездного. За проезд оплачивает из моей пенсии."""

    segments = split_appeal_questions(text)
    assert len(segments) >= 3, f"Expected >=3 segments, got {len(segments)}: {[s.text[:50] for s in segments]}"
    joined = " | ".join(s.text.lower() for s in segments)
    assert "лекарств" in joined or "инсулин" in joined
    assert "социальн" in joined and ("работник" in joined or "обслуж" in joined)
    assert "телефон" in joined or "цифров" in joined


def test_short_preamble_not_separate_segment():
    text = "Уважаемая Администрация!\n\n1. Прошу рассмотреть вопрос ЖКУ.\n2. Прошу разобраться с уборкой."
    segments = split_appeal_questions(text)
    assert len(segments) == 2
    assert all(len(s.text) > 20 for s in segments)


def test_segments_have_ordinals():
    text = "1. Первый вопрос про ремонт.\n2. Второй вопрос про мусор."
    segments = split_appeal_questions(text)
    assert [s.ordinal for s in segments] == [1, 2]

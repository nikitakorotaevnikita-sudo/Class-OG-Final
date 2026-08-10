"""Дедупликация вопросов с одинаковым кодом классификатора.

По анализу ОС Заказчика (68 обращений): 12 % обращений выдавали 2-4 карточки
с одним и тем же кодом — оператор видел дубли.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import ClassifiedQuestion, dedupe_questions


def _q(code, text="в", conf=0.5, reasoning="", alts=None, reasons=None):
    return ClassifiedQuestion(
        question_text=text, code=code, name=f"имя {code}", level=4,
        full_path=f"путь {code}", predmet_vedeniya="", confidence=conf,
        reasoning=reasoning, alternatives=alts if alts is not None else [],
        verification_reasons=reasons if reasons is not None else [],
    )


def test_merges_questions_with_same_code():
    out = dedupe_questions([
        _q("0003.0011.0123.0850", text="про отказ"),
        _q("0003.0011.0123.0850", text="про аренду"),
        _q("0003.0011.0123.0850", text="про разъяснение"),
    ])
    assert len(out) == 1
    assert out[0].code == "0003.0011.0123.0850"


def test_keeps_distinct_codes_and_their_order():
    out = dedupe_questions([
        _q("0005.0005.0056.1160"),
        _q("0001.0002.0027.0125"),
        _q("0005.0005.0056.1160"),
    ])
    assert [q.code for q in out] == ["0005.0005.0056.1160", "0001.0002.0027.0125"]


def test_merged_question_keeps_max_confidence():
    out = dedupe_questions([
        _q("0002.0014.0143.0389", conf=0.4),
        _q("0002.0014.0143.0389", conf=0.75),
    ])
    assert out[0].confidence == 0.75


def test_merged_question_combines_texts_and_reasoning():
    out = dedupe_questions([
        _q("0002.0014.0143.0389", text="первый вопрос", reasoning="первое обоснование"),
        _q("0002.0014.0143.0389", text="второй вопрос", reasoning="второе обоснование"),
    ])
    assert "первый вопрос" in out[0].question_text
    assert "второй вопрос" in out[0].question_text
    assert "первое обоснование" in out[0].reasoning
    assert "второе обоснование" in out[0].reasoning


def test_merged_question_is_marked_for_logs():
    out = dedupe_questions([
        _q("0002.0014.0143.0389"),
        _q("0002.0014.0143.0389"),
    ])
    assert "merged_duplicate_questions" in out[0].verification_reasons


def test_identical_text_and_reasoning_not_duplicated_twice():
    """Модель часто повторяет дословно — не склеивать одно и то же дважды."""
    out = dedupe_questions([
        _q("0002.0014.0143.0389", text="один и тот же", reasoning="одно и то же"),
        _q("0002.0014.0143.0389", text="один и тот же", reasoning="одно и то же"),
    ])
    assert out[0].question_text == "один и тот же"
    assert out[0].reasoning == "одно и то же"


def test_no_duplicates_returns_list_unchanged():
    src = [_q("0005.0005.0056.1160"), _q("0003.0009.0099.0742")]
    out = dedupe_questions(src)
    assert [q.code for q in out] == [q.code for q in src]
    assert all("merged_duplicate_questions" not in q.verification_reasons for q in out)


def test_empty_list():
    assert dedupe_questions([]) == []

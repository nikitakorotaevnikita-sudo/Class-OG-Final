import sys
sys.path.insert(0, "src")
from classifier_agent import extract_extra_fields


def test_extract_fio_and_summary():
    gr = {"applicant_fio": "Иванов Иван Иванович", "summary": "Жалоба на мусор."}
    text = "Главе города\nот Иванова Ивана Ивановича\nВо дворе не вывозят мусор."
    fio, summary = extract_extra_fields(gr, text)
    assert fio == "Иванов Иван Иванович"
    assert summary == "Жалоба на мусор."


def test_fio_from_the_model_is_checked_against_the_text():
    """ФИО, которого в тексте нет, в карточку не попадает: по нему заводят заявителя."""
    gr = {"applicant_fio": "Выдуманов Иван Иванович", "summary": "суть"}
    fio, _ = extract_extra_fields(gr, "Во дворе дома 7 не убирают снег.")
    assert fio is None


def test_summary_truncated_to_250():
    gr = {"applicant_fio": None, "summary": "я" * 400}
    fio, summary = extract_extra_fields(gr)
    assert fio is None
    assert len(summary) == 250


def test_missing_fields():
    fio, summary = extract_extra_fields({})
    assert fio is None
    assert summary == ""

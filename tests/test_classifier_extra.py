import sys
sys.path.insert(0, "src")
from classifier_agent import extract_extra_fields


def test_extract_fio_and_summary():
    gr = {"applicant_fio": "Иванов Иван Иванович", "summary": "Жалоба на мусор."}
    fio, summary = extract_extra_fields(gr)
    assert fio == "Иванов И.И."
    assert summary == "Жалоба на мусор."


def test_summary_truncated_to_250():
    gr = {"applicant_fio": None, "summary": "я" * 400}
    fio, summary = extract_extra_fields(gr)
    assert fio is None
    assert len(summary) == 250


def test_missing_fields():
    fio, summary = extract_extra_fields({})
    assert fio is None
    assert summary == ""

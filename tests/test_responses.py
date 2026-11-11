"""Общие построители ответов: src/responses.py (без тяжёлых импортов)."""

import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, "src")

import responses


@dataclass
class _Q:
    code: str
    name: str
    reasoning: str = ""
    question_text: str = "вопрос"
    level: int = 4
    full_path: str = "путь"
    predmet_vedeniya: str = ""
    confidence: float = 0.9
    alternatives: list = field(default_factory=list)


@dataclass
class _Result:
    questions: list
    applicant_fio: str = None
    applicant_email: str = None
    summary: str = "суть"
    log_id: str = "L1"
    vid_obrascheniya: str = "вид"
    tip_obrascheniya: str = "тип"
    is_ustное: bool = True
    overall_confidence: float = 0.8
    needs_verification: bool = False
    llm_provider: str = "p"
    llm_model: str = "m"
    full_fallback_used: bool = False


SAME = "0005.0005.0056.1161"


def test_document_response_dedups_by_code():
    r = _Result([_Q(SAME, "Свалка", "а"), _Q(SAME, "Свалка", "б")])
    resp = responses.build_document_response(r, 7)
    assert resp.document_id == 7
    assert [q.code for q in resp.questions] == [SAME]


def test_document_response_reasoning_not_repeated():
    r = _Result([_Q(SAME, "Свалка", "одно и то же")] * 3)
    assert responses.build_document_response(r, None).reasoning == "одно и то же"


def test_reasoning_prefixed_when_multiple_and_clipped():
    qs = [_Q("A", "a", "x"), _Q("B", "b", "y")]
    assert responses._build_reasoning(qs) == "[A] x [B] y"
    assert len(responses._clip_reasoning("я" * 5000)) == responses.REASONING_MAX_LEN


def test_unique_by_code_keeps_order():
    qs = [_Q("A", "1"), _Q("B", "2"), _Q("A", "3")]
    assert [q.name for q in responses._unique_by_code(qs)] == ["1", "2"]


def test_classify_response_built_from_result():
    r = _Result([_Q(SAME, "Свалка", "обосн", alternatives=[
        {"code": "C", "name": "n", "full_path": "p"}])])
    resp = responses.build_classify_response(r, "КАРТОЧКА", appeal_id="a1", was_truncated=True)
    assert resp.operator_card == "КАРТОЧКА"
    assert resp.appeal_id == "a1" and resp.was_truncated is True
    assert resp.is_ustnoe is True and resp.log_id == "L1"
    assert resp.questions[0].alternatives[0].code == "C"


def test_request_has_webhook_url():
    assert responses.ClassifyDocumentRequest().webhook_url is None


def test_responses_module_has_no_heavy_imports():
    code = ("import sys; sys.path.insert(0, 'src'); import responses; "
            "assert 'torch' not in sys.modules; "
            "assert 'classifier_agent' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)

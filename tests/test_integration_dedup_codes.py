"""Один код — один вопрос в ответе RX.

Сегментатор делит обращение по смысловым вопросам, и несколько из них могут
свестись к одному коду классификатора: «во дворе свалка, мусор не вывозят,
контейнер переполнен» — три вопроса, один код. На стороне RX это три
одинаковых строки в карточке, которые оператор вычищает руками.

Дублируется и обоснование: `reasoning` склеивается по вопросам, и при трёх
одинаковых кодах оператор читает один и тот же текст трижды.
"""

import sys
from dataclasses import dataclass

sys.path.insert(0, "src")

from rx_harness import make_client, poll_until_done


@dataclass
class _Q:
    code: str
    name: str
    reasoning: str = ""


@dataclass
class _Result:
    questions: list
    applicant_fio: str = None
    applicant_email: str = None
    summary: str = "суть"


class _Agent:
    def __init__(self, questions):
        self.questions = questions

    def classify(self, text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None,
                 defer_card=False):
        return _Result(questions=self.questions)


def answer(questions) -> dict:
    response = make_client(_Agent(questions)).post("/integration/classify-document",
                                    json={"appeal_text": "текст обращения"})
    assert response.status_code == 200, response.text
    return response.json()


def test_repeated_code_collapses_into_one_question():
    same = "0005.0005.0056.1161"
    data = answer([
        _Q(same, "Несанкционированная свалка мусора, отходов", "свалка во дворе"),
        _Q(same, "Несанкционированная свалка мусора, отходов", "мусор не вывозят"),
        _Q(same, "Несанкционированная свалка мусора, отходов", "контейнер переполнен"),
    ])
    assert [q["code"] for q in data["questions"]] == [same]


def test_first_occurrence_keeps_its_name():
    same = "0005.0005.0056.1161"
    data = answer([_Q(same, "Первое наименование"), _Q(same, "Второе наименование")])
    assert data["questions"][0]["question"] == "Первое наименование"


def test_different_codes_are_not_merged():
    codes = ["0005.0005.0056.1161", "0003.0009.0099.0742.0110", "0001.0002.0027.0125"]
    data = answer([_Q(code, f"вопрос {code}") for code in codes])
    assert [q["code"] for q in data["questions"]] == codes


def test_order_of_first_occurrences_is_kept():
    """Процедурный код приходит первым элементом — порядок ломать нельзя."""
    withdrawal, topic = "0001.0002.0027.0131", "0005.0005.0056.1161"
    data = answer([_Q(withdrawal, "Прекращение рассмотрения"),
                   _Q(topic, "Свалка"),
                   _Q(withdrawal, "Прекращение рассмотрения")])
    assert [q["code"] for q in data["questions"]] == [withdrawal, topic]


def test_reasoning_is_not_repeated_for_the_same_code():
    same = "0005.0005.0056.1161"
    data = answer([_Q(same, "Свалка", "одно и то же обоснование")] * 3)
    assert data["reasoning"].count("одно и то же обоснование") == 1


def test_single_question_reasoning_has_no_code_prefix():
    """Префикс кода нужен только когда вопросов в ответе несколько."""
    same = "0005.0005.0056.1161"
    data = answer([_Q(same, "Свалка", "обоснование")] * 2)
    assert data["reasoning"] == "обоснование"


def test_async_path_returns_the_same_deduplicated_result():
    same = "0005.0005.0056.1161"
    questions = [_Q(same, "Свалка", "обоснование")] * 3

    api = make_client(_Agent(questions))
    job_id = api.post("/integration/classify-document-async",
                      json={"appeal_text": "текст"}).json()["job_id"]
    status = poll_until_done(api, job_id)

    assert status["status"] == "done", status
    assert [q["code"] for q in status["result"]["questions"]] == [same]

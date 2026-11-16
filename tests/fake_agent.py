"""Фейковый агент классификации для тестов задач и воркера (без модели и LLM)."""
from types import SimpleNamespace

import numpy as np

from storage.models import AppealQuestionRow, AppealResult

CODE = "0005.0005.0051.1103"
CODE2 = "0001.0001.0001.0001"


def _question(code=CODE, name="Вывоз мусора"):
    return SimpleNamespace(
        question_text="вопрос", code=code, name=name, level=4, full_path="путь",
        predmet_vedeniya="", confidence=0.9, reasoning="потому что", alternatives=[],
    )


class FakeAgent:
    """Агент без модели: пишет карточку через storage на log_conn, как настоящий."""

    model_version = 0

    def __init__(self, storage, codes=(CODE,), error=None, delay=0.0):
        self.storage = storage
        self.codes = list(codes)
        self.error = error
        self.delay = delay
        self.calls = []

    def classify(self, text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None):
        self.calls.append(dict(text=text, llm_provider=llm_provider, llm_model=llm_model,
                               appeal_id=appeal_id, log_conn=log_conn))
        if self.delay:
            import time

            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        questions = [_question(c) for c in self.codes]
        self.storage.appeals.save_result(appeal_id, AppealResult(
            summary="суть", applicant_fio="Иванов И.И.", applicant_email=None,
            vid_obrascheniya="Заявление", tip_obrascheniya="Письменное", is_ustnoe=False,
            overall_confidence=0.9, needs_verification=False, full_fallback_used=False,
            candidates=[], llm_provider=llm_provider or "ario", llm_model=llm_model or "qwen",
            elapsed_seconds=1.0,
            questions=[
                AppealQuestionRow(ordinal=i, question_text=q.question_text,
                                  selected_code=q.code, confidence=q.confidence,
                                  alternative_codes=[])
                for i, q in enumerate(questions, 1)
            ],
        ), conn=log_conn)
        return SimpleNamespace(
            questions=questions, log_id=appeal_id, vid_obrascheniya="Заявление",
            tip_obrascheniya="Письменное", is_ustное=False, overall_confidence=0.9,
            needs_verification=False, llm_provider=llm_provider or "ario",
            llm_model=llm_model or "qwen", full_fallback_used=False,
            applicant_fio="Иванов И.И.", applicant_email=None, summary="суть",
        )

    def format_for_operator(self, result):
        return "карточка"

    def _search_candidates(self, q, top_k=None):
        return [{"code": f"{i:04d}.0000.0000.0000", "similarity": 1 - i / 10} for i in range(10)]

    def embed_passage(self, text):
        v = np.zeros(768, dtype=np.float32)
        v[len(text) % 768] = 1.0
        return v

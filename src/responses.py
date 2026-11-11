"""Общие модели запросов/ответов и сборка ответов.

Модуль лёгкий: только pydantic и стандартная библиотека. Его импортируют и
тонкий API, и Celery-воркер, чтобы ответ собирался одинаково, а API не тянул
за собой классификатор (torch, sentence-transformers).
"""

from typing import Optional

from pydantic import BaseModel


# ── /classify ────────────────────────────────────────────────────────────────

class ClassifyRequest(BaseModel):
    appeal_text: Optional[str] = None
    appeal_id: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


class AlternativeItem(BaseModel):
    code: str
    name: str
    full_path: str


class QuestionResult(BaseModel):
    question_text: str
    code: str
    name: str
    level: int
    full_path: str
    predmet_vedeniya: str
    confidence: float
    reasoning: str
    alternatives: list[AlternativeItem]


class ClassifyResponse(BaseModel):
    appeal_id: Optional[str]
    log_id: Optional[str]            # Для последующего /verify
    vid_obrascheniya: str
    tip_obrascheniya: str
    is_ustnoe: bool
    questions: list[QuestionResult]
    overall_confidence: float
    needs_verification: bool
    operator_card: str              # Текст карточки верификации для оператора
    was_truncated: bool = False       # Был ли текст обрезан до 5000 символов
    llm_provider: str = ""
    llm_model: str = ""
    full_fallback_used: bool = False  # Сработал ли full-classifier fallback (ретривер промахнулся)


def build_classify_response(
    result,
    operator_card: str,
    appeal_id: Optional[str] = None,
    was_truncated: bool = False,
) -> ClassifyResponse:
    """Собирает ClassifyResponse из ClassificationResult."""
    questions_out = [
        QuestionResult(
            question_text=q.question_text,
            code=q.code,
            name=q.name,
            level=q.level,
            full_path=q.full_path,
            predmet_vedeniya=q.predmet_vedeniya,
            confidence=q.confidence,
            reasoning=q.reasoning,
            alternatives=[AlternativeItem(**a) for a in q.alternatives],
        )
        for q in result.questions
    ]
    return ClassifyResponse(
        appeal_id=appeal_id,
        log_id=result.log_id,
        vid_obrascheniya=result.vid_obrascheniya,
        tip_obrascheniya=result.tip_obrascheniya,
        is_ustnoe=result.is_ustное,
        questions=questions_out,
        overall_confidence=result.overall_confidence,
        needs_verification=result.needs_verification,
        operator_card=operator_card,
        was_truncated=was_truncated,
        llm_provider=result.llm_provider,
        llm_model=result.llm_model,
        full_fallback_used=result.full_fallback_used,
    )


# ── /integration (Directum RX) ───────────────────────────────────────────────

class ClassifyDocumentRequest(BaseModel):
    # Текст обращения — основной способ: RX присылает его сразу
    appeal_text: Optional[str] = None
    # Идентификатор документа: возвращается в ответе как есть; если текста нет,
    # используется для получения тела документа из RX по OData
    document_id: Optional[int] = None
    # Адрес для уведомления о завершении (пока не используется)
    webhook_url: Optional[str] = None


REASONING_MAX_LEN = 1000  # Ограничение длины обоснования в ответе RX (символов)


def _clip_reasoning(text: str) -> str:
    text = text or ""
    return text if len(text) <= REASONING_MAX_LEN else text[:REASONING_MAX_LEN - 1] + "…"


class RxQuestion(BaseModel):
    code: str
    question: str


class ClassifyDocumentResponse(BaseModel):
    document_id: Optional[int]
    applicant_fio: Optional[str]
    applicant_email: Optional[str] = None
    summary: str
    reasoning: str = ""   # Обоснование модели по всем вопросам, сплошным текстом (<=1000 симв.)
    questions: list[RxQuestion]


def _build_reasoning(questions) -> str:
    """Склеить обоснования по всем вопросам в один сплошной текст (<=1000 симв.).

    При нескольких вопросах каждое обоснование префиксуется кодом, чтобы
    было видно, к какому коду оно относится; всё соединяется пробелами.
    """
    parts = []
    multi = len(questions) > 1
    for q in questions:
        r = (q.reasoning or "").strip()
        if not r:
            continue
        parts.append(f"[{q.code}] {r}" if multi else r)
    return _clip_reasoning(" ".join(parts))


def _unique_by_code(questions) -> list:
    """Один код — один вопрос, в порядке первого появления.

    Сегментатор делит обращение по смыслу, и несколько вопросов могут свестись
    к одному коду: «свалка во дворе, мусор не вывозят, контейнер переполнен» —
    три вопроса, один код. Для RX это один вопрос: иначе в карточке три
    одинаковых строки, которые оператор вычищает руками.
    """
    seen: set[str] = set()
    unique = []
    for question in questions:
        if question.code in seen:
            continue
        seen.add(question.code)
        unique.append(question)
    return unique


def build_document_response(result, document_id: Optional[int]) -> ClassifyDocumentResponse:
    """Собирает ответ RX из ClassificationResult."""
    questions = _unique_by_code(result.questions)
    return ClassifyDocumentResponse(
        document_id=document_id,
        applicant_fio=result.applicant_fio,
        applicant_email=result.applicant_email,
        summary=result.summary,
        # Обоснование склеивается по тем же вопросам: при повторе кода оператор
        # иначе читает один и тот же текст несколько раз.
        reasoning=_build_reasoning(questions),
        questions=[RxQuestion(code=q.code, question=q.name) for q in questions],
    )


# ── Фоновые задачи ───────────────────────────────────────────────────────────

class AcceptedResponse(BaseModel):
    job_id: str
    status: str
    queue_position: Optional[int] = None
    poll_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str                                   # queued | running | done | error
    queue_position: Optional[int] = None          # только пока задача ждёт
    elapsed_sec: float = 0.0
    document_id: Optional[int] = None
    result: Optional[ClassifyDocumentResponse] = None
    error: Optional[str] = None

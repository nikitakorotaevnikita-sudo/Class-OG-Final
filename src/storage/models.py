"""Модели данных слоя хранения и контейнер Storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class ClassifierEntry:
    code: str
    name: str
    level: int
    parent_code: Optional[str]
    full_path: str
    children_count: int
    search_text: str = ""  # текст базовой строки активной версии модели


@dataclass
class ModelVersion:
    version: int
    base_model_name: str
    model_path: str
    is_active: bool
    recall5_before: Optional[float] = None
    recall5_after: Optional[float] = None
    training_samples_count: Optional[int] = None
    trained_at: Optional[datetime] = None
    report: Optional[dict] = None
    adapter: Optional[dict] = None


@dataclass
class AppealQuestionRow:
    ordinal: int
    question_text: str
    selected_code: str
    confidence: float
    alternative_codes: list[str]
    predmet_vedeniya: str = ""
    reasoning: str = ""
    verification_reasons: list[str] = field(default_factory=list)


@dataclass
class AppealResult:
    summary: Optional[str]
    applicant_fio: Optional[str]
    applicant_email: Optional[str]
    vid_obrascheniya: str
    tip_obrascheniya: str
    is_ustnoe: bool
    overall_confidence: float
    needs_verification: bool
    full_fallback_used: bool
    candidates: list[dict]
    llm_provider: str
    llm_model: str
    elapsed_seconds: Optional[float]
    questions: list[AppealQuestionRow]


@dataclass
class Storage:
    classifier: Any
    models: Any
    # Заполняются задачами 2b-2d
    appeals: Any = None
    jobs: Any = None
    historical: Any = None

"""Модели данных слоя хранения и контейнер Storage."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    sort_order: Optional[int] = None  # порядок в classifier_flat.json (None -> индекс в списке)


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
    # Optional: старые карточки из файлового лога этих полей не имели (NULL)
    vid_obrascheniya: Optional[str]
    tip_obrascheniya: Optional[str]
    is_ustnoe: Optional[bool]
    overall_confidence: float
    needs_verification: Optional[bool]
    full_fallback_used: Optional[bool]
    candidates: list[dict]
    llm_provider: Optional[str]
    llm_model: Optional[str]
    elapsed_seconds: Optional[float]
    questions: list[AppealQuestionRow]


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"


class QueueFull(RuntimeError):
    """Очередь заполнена — вызывающей стороне нужно повторить позже."""


class ModelVersionExists(RuntimeError):
    """Версия модели с таким номером уже есть в реестре (register_new)."""


@dataclass
class Job:
    """Фоновая задача (строка таблицы jobs)."""
    id: str
    job_type: str
    status: str
    appeal_id: Optional[str]
    document_id: Optional[int]
    webhook_url: Optional[str]
    payload: dict
    result: Optional[dict]
    error: Optional[str]
    attempts: int
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def elapsed_sec(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        start = self.started_at or self.created_at
        return round((end - start).total_seconds(), 1)


@dataclass
class Storage:
    classifier: Any
    models: Any
    # Заполняются задачами 2b-2d
    appeals: Any = None
    jobs: Any = None
    historical: Any = None

    @contextmanager
    def transaction(self):
        """Атомарная группа записей. Memory: no-op (None); PostgreSQL: соединение для conn=."""
        yield None

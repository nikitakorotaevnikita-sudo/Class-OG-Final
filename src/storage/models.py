"""Модели данных слоя хранения и контейнер Storage."""

from __future__ import annotations

from dataclasses import dataclass
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
class Storage:
    classifier: Any
    models: Any
    # Заполняются задачами 2b-2d
    appeals: Any = None
    jobs: Any = None
    historical: Any = None

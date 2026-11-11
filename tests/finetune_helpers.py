"""Общие заготовки тестов дообучения: хранилище с данными, фейковые модель и train_fn."""

import numpy as np

from storage.memory import MemoryStorage
from storage.models import AppealQuestionRow, AppealResult, ClassifierEntry, ModelVersion

BASE_MODEL = "intfloat/multilingual-e5-base"
CODES = [f"0001.0001.0001.{i:04d}" for i in range(1, 6)]
ANNOTATION = "жалоба на шум соседей"


def _result(code: str) -> AppealResult:
    return AppealResult(
        summary=None, applicant_fio=None, applicant_email=None,
        vid_obrascheniya=None, tip_obrascheniya=None, is_ustnoe=None,
        overall_confidence=0.9, needs_verification=False, full_fallback_used=False,
        candidates=[], llm_provider=None, llm_model=None, elapsed_seconds=None,
        questions=[AppealQuestionRow(ordinal=1, question_text="вопрос", selected_code=code,
                                     confidence=0.9, alternative_codes=[])],
    )


def seed(storage=None, confirmed: int = 8, corrected: int = 4, historical: int = 3):
    """Классификатор из 5 записей, активная v0, верифицированные карточки и история."""
    storage = storage or MemoryStorage()
    storage.classifier.upsert_entries([
        ClassifierEntry(code=c, name=f"Вопрос {i}", level=4, parent_code=None,
                        full_path=f"Раздел > Тема > Вопрос {i}", children_count=0)
        for i, c in enumerate(CODES, 1)
    ])
    storage.models.register(ModelVersion(version=0, base_model_name=BASE_MODEL,
                                         model_path=BASE_MODEL, is_active=True))
    storage.classifier.add_vectors(
        0, [(c, "base", f"Вопрос {i}", np.ones(768, dtype=np.float32))
            for i, c in enumerate(CODES, 1)])
    storage.classifier.add_annotation(CODES[0], ANNOTATION, 0)

    for i in range(confirmed):
        aid = storage.appeals.create(f"подтверждённое обращение {i}")
        storage.appeals.save_result(aid, _result(CODES[i % len(CODES)]))
        storage.appeals.set_verification(aid, "confirmed", None, None)
    for i in range(corrected):
        aid = storage.appeals.create(f"исправленное обращение {i}")
        storage.appeals.save_result(aid, _result(CODES[0]))
        storage.appeals.set_verification(aid, "corrected", [CODES[1]], "не тот код")
    storage.historical.add(
        [{"appeal_text": f"историческое обращение {i}", "assigned_code": CODES[2]}
         for i in range(historical)],
        "history.xlsx",
    )
    return storage


class FakeTrain:
    """train_fn без модели: запоминает аргументы, создаёт папку модели."""

    def __init__(self, before=0.4, after=0.6, error=None):
        self.before, self.after, self.error = before, after, error
        self.calls = []

    def __call__(self, base_model_path, train_examples, val_examples, output_path):
        self.calls.append(dict(base=base_model_path, train=list(train_examples),
                               val=list(val_examples), output=output_path))
        if self.error is not None:
            raise self.error
        return self.before, self.after


class FakeSentenceTransformer:
    """Подмена SentenceTransformer в build_vectordb: детерминированные векторы."""

    loaded = []

    def __init__(self, path):
        FakeSentenceTransformer.loaded.append(str(path))

    def encode(self, texts, normalize_embeddings=True, **kw):
        rng = np.random.default_rng(len(texts))
        m = rng.normal(size=(len(texts), 768)).astype(np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

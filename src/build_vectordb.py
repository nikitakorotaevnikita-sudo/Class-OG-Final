"""
Построение векторов классификатора для версии модели в БД (classifier_vectors).

Для каждой записи классификатора:
  - базовая строка: search_text = build_search_text(...) (аннотации старого
    формата вшиты: correct_name подменяет название, category/note дописываются);
  - по строке на каждый текст аннотации (source_type='annotation').
Всё векторизуется моделью model_path (префикс "passage: " для e5-семейства).

Вызывается из дообучения (finetune) для новой версии модели. Версию 0 (текущие
прод-векторы) переносит scripts/db/migrate_from_files.py без пересчёта.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import env_bootstrap  # noqa: F401  — .env до HF-библиотек

from sentence_transformers import SentenceTransformer
from annotations_storage import build_search_text

BATCH_SIZE = 128


def uses_passage_prefix(model_path: str) -> bool:
    """Префикс "passage: " только для e5-семейства; BGE-M3 и др. современные
    модели префиксов не требуют (см. model card)."""
    return "e5" in str(model_path).lower()


def passage_text(text: str, model_path: str) -> str:
    return f"passage: {text}" if uses_passage_prefix(model_path) else text


def build_database(storage, model_path: str, version: int) -> int:
    """Пишет базовые и аннотационные векторы версии version. Возвращает число строк."""
    entries = storage.classifier.load_entries()
    annotated = set(storage.classifier.annotated_codes())
    print(f"Построение векторов v{version}: {len(entries)} записей, "
          f"кодов с аннотациями: {len(annotated)}; модель {model_path}")

    rows: list[tuple[str, str, str]] = []
    for e in entries:
        anns = storage.classifier.annotations_for(e.code) if e.code in annotated else []
        rows.append((e.code, "base", build_search_text(e.code, e.name, e.full_path, anns)))
        rows.extend((e.code, "annotation", text) for text in anns)

    model = SentenceTransformer(model_path)
    vectors = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        vectors.append(np.asarray(
            model.encode([passage_text(t, model_path) for _, _, t in batch],
                         normalize_embeddings=True),
            dtype=np.float32,
        ))
        done = min(start + BATCH_SIZE, len(rows))
        print(f"  [{done / len(rows) * 100:5.1f}%] {done}/{len(rows)}", end="\r")
    matrix = np.vstack(vectors) if vectors else np.zeros((0, 768), dtype=np.float32)

    n = storage.classifier.add_vectors(
        version, [(code, st, text, matrix[i]) for i, (code, st, text) in enumerate(rows)]
    )
    print(f"\nГотово: {n} векторов для версии {version}")
    return n


if __name__ == "__main__":
    print(
        "Векторы классификатора теперь хранятся в PostgreSQL (classifier_vectors).\n"
        "  Перенос текущей базы:        python scripts/db/migrate_from_files.py\n"
        "  Векторы новой версии модели строит дообучение (finetune) через build_database()."
    )
    # Ненулевой код: старые вызовы (дообучение через subprocess) не должны считать,
    # что база перестроена.
    sys.exit(2)

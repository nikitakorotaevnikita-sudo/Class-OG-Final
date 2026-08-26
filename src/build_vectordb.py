"""
Скрипт построения векторной базы данных классификатора.
Запускать один раз перед началом работы агента.

Сохраняет:
  data/vector_db/embeddings.npy   — матрица эмбеддингов (2108 × 768)
  data/vector_db/metadata.json    — список записей классификатора

Использование:
    python src/build_vectordb.py
"""

import sys
import json
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import env_bootstrap  # noqa: F401  — .env до HF-библиотек

from sentence_transformers import SentenceTransformer
from config import EMBEDDING_MODEL, CLASSIFIER_FLAT_PATH, VECTOR_DB_DIR
from annotations_storage import build_search_text, list_annotated_codes

BATCH_SIZE = 128


def build_database():
    print("=" * 60)
    print("Построение векторной базы классификатора обращений")
    print("=" * 60)

    # ── Загрузка классификатора ───────────────────────────────────────
    print(f"\n[1/4] Загрузка классификатора из {CLASSIFIER_FLAT_PATH}...")
    with open(CLASSIFIER_FLAT_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    print(f"      Загружено записей: {len(entries)}")

    codes_with_ann = list_annotated_codes()
    print(f"      Кодов с аннотациями: {len(codes_with_ann)}")

    # ── Загрузка модели эмбеддингов ───────────────────────────────────
    print(f"\n[2/4] Загрузка модели эмбеддингов: {EMBEDDING_MODEL}...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    print("      Модель загружена.")

    # ── Векторизация ──────────────────────────────────────────────────
    print(f"\n[3/4] Векторизация {len(entries)} записей (батч {BATCH_SIZE})...")
    total = len(entries)
    all_embeddings = []
    metadata = []

    # Префикс "passage: " только для e5-семейства; BGE-M3 и др. современные
    # модели префиксов не требуют (см. model card)
    use_prefix = "e5" in EMBEDDING_MODEL.lower()
    if not use_prefix:
        print(f"      (без 'passage: ' префикса — модель {EMBEDDING_MODEL} префиксов не требует)")

    for start in range(0, total, BATCH_SIZE):
        batch = entries[start:start + BATCH_SIZE]
        texts = []
        for e in batch:
            search_txt = build_search_text(
                code=e["code"],
                base_name=e["name"],
                base_full_path=e.get("full_path", "")
            )
            texts.append(f"passage: {search_txt}" if use_prefix else search_txt)
        embeddings = model.encode(texts, normalize_embeddings=True)
        all_embeddings.append(embeddings)

        for e in batch:
            search_txt = build_search_text(
                code=e["code"],
                base_name=e["name"],
                base_full_path=e.get("full_path", "")
            )
            metadata.append({
                "code":           e["code"],
                "name":           e["name"],
                "level":          e["level"],
                "parent_code":    e.get("parent_code") or "",
                "full_path":      e["full_path"],
                "children_count": e.get("children_count", 0),
                "search_text":    search_txt,
            })

        done = min(start + BATCH_SIZE, total)
        pct  = done / total * 100
        print(f"      [{pct:5.1f}%] {done}/{total} записей обработано", end="\r")

    # ── Сохранение ────────────────────────────────────────────────────
    print(f"\n\n[4/4] Сохранение базы в {VECTOR_DB_DIR}...")
    Path(VECTOR_DB_DIR).mkdir(parents=True, exist_ok=True)

    embeddings_matrix = np.vstack(all_embeddings)
    np.save(Path(VECTOR_DB_DIR) / "embeddings.npy", embeddings_matrix)

    with open(Path(VECTOR_DB_DIR) / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"   embeddings.npy : {embeddings_matrix.shape}  ({embeddings_matrix.nbytes // 1024} KB)")
    print(f"   metadata.json  : {len(metadata)} записей")
    print(f"\nБаза построена! {len(metadata)} записей в {VECTOR_DB_DIR}")


if __name__ == "__main__":
    build_database()

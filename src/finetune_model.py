"""
finetune_model.py — Дообучение эмбеддинговой модели на верифицированных обращениях

Запуск:
    python src/finetune_model.py

Что делает:
    1. Читает верифицированные записи из data/appeals_log.jsonl
    2. Строит обучающие пары (текст обращения, правильная запись классификатора)
    3. Дообучает multilingual-e5-base через MultipleNegativesRankingLoss
    4. Сохраняет модель в models/e5-finetuned-vN/
    5. Выводит метрику recall@5 (до и после)

Требования:
    - Минимум 50 верифицированных записей (см. FINETUNE_THRESHOLD в config.py)
    - PyTorch (входит в sentence-transformers)
    - CPU: ~1-4 часа | GPU: ~10-20 минут
"""

import sys
import json
import random
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from sentence_transformers import SentenceTransformer, InputExample, evaluation
from sentence_transformers.losses import MultipleNegativesRankingLoss
from torch.utils.data import DataLoader

from appeals_logger import get_logger
from config import EMBEDDING_MODEL, MODELS_DIR, VECTOR_DB_DIR, FINETUNE_THRESHOLD


def load_metadata() -> dict:
    """Загружает metadata.json — нужен для получения имён по коду."""
    meta_path = Path(VECTOR_DB_DIR) / "metadata.json"
    with open(meta_path, encoding="utf-8") as f:
        metadata = json.load(f)
    return {m["code"]: m for m in metadata}


def build_training_examples(verified_entries: list, code_index: dict) -> list[InputExample]:
    """
    Строит обучающие пары для MultipleNegativesRankingLoss.
    Формат: InputExample(texts=[anchor, positive])
      anchor   = "query: <текст обращения>"
      positive = "passage: <название записи классификатора>"

    Сигналы из лога:
      confirmed → (appeal, agent_code)     — агент был прав
      corrected → (appeal, operator_code)  — берём код оператора
    """
    examples = []

    for entry in verified_entries:
        appeal_text = entry["appeal_text"][:800]  # ограничение для модели
        status = entry["verification"]["status"]

        if status == "confirmed":
            for q in entry["agent_questions"]:
                code = q["selected_code"]
                meta = code_index.get(code)
                if meta:
                    anchor   = f"query: {appeal_text}"
                    positive = f"passage: {meta['name']}. {meta.get('full_path', '')}"
                    examples.append(InputExample(texts=[anchor, positive]))

        elif status == "corrected":
            operator_codes = entry["verification"].get("operator_codes") or []
            for code in operator_codes:
                meta = code_index.get(code)
                if meta:
                    anchor   = f"query: {appeal_text}"
                    positive = f"passage: {meta['name']}. {meta.get('full_path', '')}"
                    examples.append(InputExample(texts=[anchor, positive]))

    return examples


def get_next_version(models_dir: Path) -> int:
    """Определяет следующий номер версии модели."""
    existing = sorted(models_dir.glob("e5-finetuned-v*"))
    if not existing:
        return 1
    last = existing[-1].name  # e5-finetuned-v3
    try:
        return int(last.split("-v")[-1]) + 1
    except ValueError:
        return len(existing) + 1


def evaluate_recall(model: SentenceTransformer, examples: list[InputExample], k: int = 5) -> float:
    """
    Простая оценка recall@k: для каждого anchor проверяем,
    входит ли его positive в топ-k по cosine similarity.
    """
    if not examples:
        return 0.0

    anchors   = [e.texts[0] for e in examples]
    positives = [e.texts[1] for e in examples]

    anchor_emb   = model.encode(anchors,   normalize_embeddings=True, show_progress_bar=False)
    positive_emb = model.encode(positives, normalize_embeddings=True, show_progress_bar=False)

    import numpy as np
    scores = anchor_emb @ positive_emb.T  # (N, N) матрица сходства

    hits = 0
    for i in range(len(anchors)):
        top_k_indices = scores[i].argsort()[::-1][:k]
        if i in top_k_indices:
            hits += 1

    return hits / len(anchors)


def main():
    print()
    print("═" * 52)
    print("  Fine-tuning эмбеддинговой модели")
    print(f"  Базовая модель: {EMBEDDING_MODEL}")
    print("═" * 52)

    # ── Шаг 1: Загрузка данных ────────────────────────────────────────────────
    logger = get_logger()
    verified = logger.read_verified()
    stats = logger.stats()

    print(f"\n  Верифицированных записей: {stats['verified']}")
    print(f"    подтверждено: {stats['confirmed']}")
    print(f"    исправлено:   {stats['corrected']}")

    if len(verified) < FINETUNE_THRESHOLD:
        print(f"\n  Недостаточно данных.")
        print(f"  Нужно минимум {FINETUNE_THRESHOLD} записей, есть {len(verified)}.")
        print(f"  Продолжите верификацию обращений в operator_cli.py")
        sys.exit(1)

    # ── Шаг 2: Загрузка метаданных классификатора ─────────────────────────────
    print("\n  Загрузка метаданных классификатора...")
    code_index = load_metadata()

    # ── Шаг 3: Построение обучающих пар ──────────────────────────────────────
    all_examples = build_training_examples(verified, code_index)
    print(f"  Обучающих пар: {len(all_examples)}")

    if len(all_examples) < 10:
        print("  Слишком мало обучающих пар (< 10). Проверьте качество данных.")
        sys.exit(1)

    # Train / val split (80/20)
    random.shuffle(all_examples)
    split = int(len(all_examples) * 0.8)
    train_examples = all_examples[:split]
    val_examples   = all_examples[split:]
    print(f"  Train: {len(train_examples)}  |  Val: {len(val_examples)}")

    # ── Шаг 4: Загрузка базовой модели ───────────────────────────────────────
    print(f"\n  Загрузка модели {EMBEDDING_MODEL}...")
    model = SentenceTransformer(EMBEDDING_MODEL)

    # ── Шаг 5: Оценка ДО дообучения ──────────────────────────────────────────
    print("  Оценка базовой модели (recall@5)...")
    recall_before = evaluate_recall(model, val_examples, k=5)
    print(f"  recall@5 до дообучения: {recall_before:.3f}")

    # ── Шаг 6: Дообучение ─────────────────────────────────────────────────────
    models_dir = Path(MODELS_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)
    version = get_next_version(models_dir)
    output_path = models_dir / f"e5-finetuned-v{version}"

    print(f"\n  Дообучение... (выходная папка: {output_path})")
    print("  На CPU это займёт от 15 минут до нескольких часов.")
    print("  Прогресс:")

    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=16)
    train_loss = MultipleNegativesRankingLoss(model)

    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=3,
        warmup_steps=max(10, len(train_examples) // 10),
        output_path=str(output_path),
        show_progress_bar=True,
        checkpoint_path=str(output_path / "checkpoints"),
        checkpoint_save_steps=100,
    )

    # ── Шаг 7: Оценка ПОСЛЕ дообучения ───────────────────────────────────────
    print("\n  Оценка дообученной модели (recall@5)...")
    recall_after = evaluate_recall(model, val_examples, k=5)
    print(f"  recall@5 после дообучения: {recall_after:.3f}")

    delta = recall_after - recall_before
    improved = delta > 0

    print()
    print("─" * 52)
    print(f"  recall@5:  {recall_before:.3f} → {recall_after:.3f}  "
          f"({'↑ +' if delta >= 0 else '↓ '}{delta:.3f})")
    print("─" * 52)

    # ── Шаг 8: Результат ──────────────────────────────────────────────────────
    if improved:
        print(f"\n  Модель улучшилась. Сохранена в: {output_path}")
        print()
        print("  Чтобы применить новую модель, добавьте в .env:")
        print(f"    EMBEDDING_MODEL={output_path}")
        print()
        print("  Затем пересоберите векторную базу:")
        print("    python src/build_vectordb.py")
    else:
        print(f"\n  Модель не улучшилась (recall не вырос).")
        print(f"  Модель сохранена в {output_path} — можете проверить вручную.")
        print("  Рекомендация: соберите больше верифицированных данных.")

    # Сохраняем отчёт об обучении
    report = {
        "timestamp":      datetime.now().isoformat(timespec="seconds"),
        "base_model":     EMBEDDING_MODEL,
        "output_path":    str(output_path),
        "train_size":     len(train_examples),
        "val_size":       len(val_examples),
        "recall5_before": round(recall_before, 4),
        "recall5_after":  round(recall_after, 4),
        "improved":       improved,
    }
    report_path = output_path / "training_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  Отчёт сохранён: {report_path}")


if __name__ == "__main__":
    main()

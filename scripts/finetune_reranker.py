"""Fine-tune BAAI/bge-reranker-base on ii25 hard negatives.

Input:  data/ii25_reranker_train.jsonl (405 пар query+candidate+label)
Output: models/bge-reranker-ii25-v1/

Использует CrossEncoder.fit с BinaryCrossEntropyLoss (стандарт для reranker'ов).
Малый датасет (43 query × ~9 negatives) — 3-5 эпох, маленький LR, ранний останов
по eval-loss на held-out части.

Usage:
  python scripts/finetune_reranker.py [--epochs 5] [--batch-size 16] [--lr 2e-5]
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

# Set HF mirror before imports (consistent with the rest of the project)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SSL", "1")
# Limit torch threads to avoid memory issues
import torch
torch.set_num_threads(2)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "data" / "ii25_reranker_train.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "models" / "bge-reranker-ii25-v1"
DEFAULT_BASE = "BAAI/bge-reranker-base"


def load_triplets(path: Path) -> list[dict]:
    items = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-model", default=DEFAULT_BASE)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--val-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    # Load triplets
    triplets = load_triplets(args.input)
    print(f"Загружено {len(triplets)} обучающих пар")

    # Group by source_id — split by query, not by pair (чтобы валидация была independent)
    by_source: dict[str, list[dict]] = {}
    for t in triplets:
        by_source.setdefault(t["source_id"], []).append(t)

    source_ids = list(by_source.keys())
    rng.shuffle(source_ids)
    val_n = max(1, int(len(source_ids) * args.val_fraction))
    val_ids = set(source_ids[:val_n])
    train_pairs = [t for sid, items in by_source.items() if sid not in val_ids for t in items]
    val_pairs = [t for sid in val_ids for t in by_source[sid]]
    print(f"Train: {len(train_pairs)} пар из {len(source_ids) - val_n} запросов")
    print(f"Val:   {len(val_pairs)} пар из {val_n} запросов")

    # Lazy import of sentence_transformers
    from sentence_transformers import CrossEncoder, InputExample
    from torch.utils.data import DataLoader

    print(f"\nЗагрузка базовой модели: {args.base_model}")
    model = CrossEncoder(args.base_model, num_labels=1, max_length=512)

    train_samples = [
        InputExample(texts=[t["query"], t["candidate"]], label=float(t["label"]))
        for t in train_pairs
    ]
    val_samples = [
        InputExample(texts=[t["query"], t["candidate"]], label=float(t["label"]))
        for t in val_pairs
    ]

    train_dataloader = DataLoader(train_samples, batch_size=args.batch_size, shuffle=True)

    # Evaluator — measure correlation between predicted score и label на val
    from sentence_transformers.cross_encoder.evaluation import CECorrelationEvaluator
    evaluator = CECorrelationEvaluator.from_input_examples(val_samples, name="ii25_val")

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"\nОбучение: {args.epochs} эпох, batch={args.batch_size}, lr={args.lr}")
    print(f"Output → {args.output}\n")

    model.fit(
        train_dataloader=train_dataloader,
        evaluator=evaluator,
        epochs=args.epochs,
        warmup_steps=args.warmup,
        evaluation_steps=max(1, len(train_dataloader) // 2),
        optimizer_params={"lr": args.lr},
        output_path=str(args.output),
        save_best_model=True,
        show_progress_bar=True,
    )

    print(f"\nГотово. Модель сохранена в {args.output}")
    print("Используй: ENABLE_CROSS_ENCODER_RERANKER=true CROSS_ENCODER_MODEL=" + str(args.output))


if __name__ == "__main__":
    main()

"""Pre-encode ii25_train queries (and optionally test) with frozen e5-base.

Сохраняет:
  - data/ii25_train_query_embs.npy   — (N, 768) float32
  - data/ii25_train_query_meta.json — id, assigned_code per row

Дальше adapter обучается ТОЛЬКО на этих кэшированных эмбеддингах
без повторных загрузок e5. Память остаётся низкой.

Usage:
  python scripts/encode_queries.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Same HF setup as agent
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SSL", "1")

import numpy as np
import torch
torch.set_num_threads(2)


REPO_ROOT = Path(__file__).resolve().parents[1]


def encode_split(split_path: Path, output_emb: Path, output_meta: Path, model_name: str):
    from sentence_transformers import SentenceTransformer

    records = []
    with split_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"Loading {model_name}...", flush=True)
    model = SentenceTransformer(model_name)
    print(f"Encoding {len(records)} queries...", flush=True)

    texts = [f"query: {r['appeal_text']}" for r in records]
    embs = model.encode(texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False)
    embs = np.asarray(embs, dtype=np.float32)

    meta = [{"id": r["id"], "assigned_code": r["assigned_code"], "split": r["split"]} for r in records]

    np.save(output_emb, embs)
    with output_meta.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  saved {embs.shape} -> {output_emb}")
    print(f"  meta  {len(meta)} -> {output_meta}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="intfloat/multilingual-e5-base")
    parser.add_argument("--train-in", type=Path, default=REPO_ROOT / "data" / "ii25_train.jsonl")
    parser.add_argument("--test-in", type=Path, default=REPO_ROOT / "data" / "ii25_test.jsonl")
    args = parser.parse_args()

    encode_split(
        args.train_in,
        REPO_ROOT / "data" / "ii25_train_query_embs.npy",
        REPO_ROOT / "data" / "ii25_train_query_meta.json",
        args.model,
    )
    encode_split(
        args.test_in,
        REPO_ROOT / "data" / "ii25_test_query_embs.npy",
        REPO_ROOT / "data" / "ii25_test_query_meta.json",
        args.model,
    )


if __name__ == "__main__":
    main()

"""Apply trained adapter to classifier code embeddings → new vector DB.

Read:
  data/vector_db/embeddings.npy  (2108, 768) — оригинальные e5-эмбеддинги
  models/adapter_v1.npz          — W (768, 768), b (768,)

Write:
  data/vector_db_adapted/embeddings.npy  (2108, 768) — после адаптера + L2-normalize
  data/vector_db_adapted/metadata.json    — копия

Usage:
  python scripts/apply_adapter.py
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "data" / "vector_db")
    parser.add_argument("--adapter", type=Path, default=REPO_ROOT / "models" / "adapter_v1.npz")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data" / "vector_db_adapted")
    args = parser.parse_args()

    # Load
    print(f"Source: {args.source}")
    embs = np.load(args.source / "embeddings.npy").astype(np.float32)
    print(f"  embeddings: {embs.shape}")

    adapter = np.load(args.adapter)
    W = adapter["W"].astype(np.float32)  # (768, 768)
    b = adapter["b"].astype(np.float32)  # (768,)
    print(f"Adapter W: {W.shape}, b: {b.shape}")

    # Apply: y = W @ x + b  (numpy use: y_i = x_i @ W.T + b)
    y = embs @ W.T + b
    # L2-normalize (same as in training)
    norms = np.linalg.norm(y, axis=1, keepdims=True) + 1e-12
    y_norm = (y / norms).astype(np.float32)

    # Save
    args.output.mkdir(parents=True, exist_ok=True)
    np.save(args.output / "embeddings.npy", y_norm)
    shutil.copy2(args.source / "metadata.json", args.output / "metadata.json")
    print(f"Saved {y_norm.shape} -> {args.output / 'embeddings.npy'}")
    print(f"Copied metadata.json")
    print(f"\nReady. To enable: set VECTOR_DB_DIR={args.output} в .env")


if __name__ == "__main__":
    main()

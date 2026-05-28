"""Smoke-test BGE-M3 model loading + encoding (CPU)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-m3"

print(f"Loading {MODEL_NAME}...")
t0 = time.time()
model = SentenceTransformer(MODEL_NAME)
print(f"  Loaded in {time.time() - t0:.1f}s")

texts = [
    "query: жалоба на бездействие администрации",
    "passage: Жилищно-коммунальная сфера / Управляющие компании / Бездействие УК",
]
print("Encoding 2 texts...")
t0 = time.time()
embs = model.encode(texts, normalize_embeddings=True)
print(f"  Encoded in {time.time() - t0:.2f}s")
print(f"  Shape: {embs.shape}")
print(f"  Cosine sim: {(embs[0] @ embs[1]):.4f}")

assert embs.shape == (2, 1024), f"Expected (2, 1024), got {embs.shape}"
print("\nOK: BGE-M3 loads, produces 1024-dim embeddings.")

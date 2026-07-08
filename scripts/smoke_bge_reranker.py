"""Smoke-test BGE-Reranker-v2-m3 loading + scoring (CPU)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentence_transformers import CrossEncoder

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

print(f"Loading {MODEL_NAME}...")
t0 = time.time()
ce = CrossEncoder(MODEL_NAME, max_length=512)
print(f"  Loaded in {time.time() - t0:.1f}s")

query = "Жалоба на бездействие управляющей компании по уборке двора"
candidates = [
    "Жилищно-коммунальная сфера / Управление многоквартирными домами / Бездействие УК",
    "Социальная сфера / Здравоохранение / Льготы инвалидам",
    "Экономика / Финансы / Налог на имущество",
]
pairs = [(query, c) for c in candidates]

print("Scoring 3 pairs...")
t0 = time.time()
scores = ce.predict(pairs)
print(f"  Scored in {time.time() - t0:.2f}s")

for c, s in zip(candidates, scores):
    print(f"  {s:.4f} | {c}")

# Sanity: первый candidate (УК) должен иметь самый высокий score
assert scores[0] > scores[1], f"Expected УК > здравоохранение, got {scores[0]} <= {scores[1]}"
assert scores[0] > scores[2], f"Expected УК > налоги, got {scores[0]} <= {scores[2]}"
print("\nOK: BGE-Reranker-v2-m3 loads, ranks plausibly.")

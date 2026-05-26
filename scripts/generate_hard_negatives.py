"""Generate hard-negative triplets for CrossEncoder fine-tuning.

Для каждой train-записи из ii25_train.jsonl:
  - positive = (text, search_text правильного кода) → label 1.0
  - hard negatives = (text, search_text соседнего кода в той же L3-ветке) → label 0.0
  - доп. негативы = (text, search_text кода из соседней L3 в той же L2) → label 0.0

Это даёт reranker'у учиться различать sibling-коды — самая слабая зона текущего пайплайна.

Output: data/ii25_reranker_train.jsonl с записями вида
  {"query": "...", "candidate": "...", "label": 1.0|0.0, "candidate_code": "..."}

Usage:
  python scripts/generate_hard_negatives.py
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from collections import defaultdict


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAIN = REPO_ROOT / "data" / "ii25_train.jsonl"
DEFAULT_CLASSIFIER = REPO_ROOT / "data" / "classifier_flat.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "ii25_reranker_train.jsonl"


def code_search_text(meta: dict) -> str:
    """Build candidate text fed to CrossEncoder (same as in retrieval)."""
    name = meta.get("name", "")
    path = meta.get("full_path", "")
    return f"{name}. {path}"


def code_l3_prefix(code: str) -> str:
    parts = code.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else code


def code_l2_prefix(code: str) -> str:
    parts = code.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--classifier", type=Path, default=DEFAULT_CLASSIFIER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--negs-l3", type=int, default=6, help="негативы из той же L3-ветки")
    parser.add_argument("--negs-l2", type=int, default=3, help="негативы из соседних L3, но той же L2")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    # Load classifier
    with args.classifier.open(encoding="utf-8") as f:
        classifier = json.load(f)
    code_index = {e["code"]: e for e in classifier}

    # Group by L3 prefix
    by_l3: dict[str, list[dict]] = defaultdict(list)
    by_l2: dict[str, list[dict]] = defaultdict(list)
    for entry in classifier:
        code = entry["code"]
        # Only entries with at least L4 (real targets) — filter parent placeholders
        if entry.get("level") not in (4, "4", 5, "5"):
            continue
        by_l3[code_l3_prefix(code)].append(entry)
        by_l2[code_l2_prefix(code)].append(entry)

    # Load train
    train_records = []
    with args.train.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                train_records.append(json.loads(line))

    triplets = []
    skipped = 0
    for record in train_records:
        query = record["appeal_text"]
        positive_code = record["assigned_code"]
        positive_meta = code_index.get(positive_code)
        if not positive_meta:
            skipped += 1
            continue

        positive_text = code_search_text(positive_meta)
        triplets.append({
            "query": query,
            "candidate": positive_text,
            "candidate_code": positive_code,
            "label": 1.0,
            "source_id": record["id"],
            "negative_type": None,
        })

        # L3 siblings (same parent at L3 level, different L4)
        l3_pool = [e for e in by_l3[code_l3_prefix(positive_code)] if e["code"] != positive_code]
        rng.shuffle(l3_pool)
        for neg in l3_pool[: args.negs_l3]:
            triplets.append({
                "query": query,
                "candidate": code_search_text(neg),
                "candidate_code": neg["code"],
                "label": 0.0,
                "source_id": record["id"],
                "negative_type": "l3_sibling",
            })

        # L2 distant: same L2, different L3
        positive_l3 = code_l3_prefix(positive_code)
        l2_pool = [e for e in by_l2[code_l2_prefix(positive_code)] if code_l3_prefix(e["code"]) != positive_l3]
        rng.shuffle(l2_pool)
        for neg in l2_pool[: args.negs_l2]:
            triplets.append({
                "query": query,
                "candidate": code_search_text(neg),
                "candidate_code": neg["code"],
                "label": 0.0,
                "source_id": record["id"],
                "negative_type": "l2_distant",
            })

    # Save
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for t in triplets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    # Stats
    pos = sum(1 for t in triplets if t["label"] == 1.0)
    neg_l3 = sum(1 for t in triplets if t.get("negative_type") == "l3_sibling")
    neg_l2 = sum(1 for t in triplets if t.get("negative_type") == "l2_distant")
    print(f"Train records:    {len(train_records)} (skipped {skipped})")
    print(f"Positives:        {pos}")
    print(f"L3-sibling negs:  {neg_l3}")
    print(f"L2-distant negs:  {neg_l2}")
    print(f"Total triplets:   {len(triplets)}")
    print(f"Saved to:         {args.output}")


if __name__ == "__main__":
    main()

"""Build Learning-to-Rank training dataset from ii25_train.

Для каждой train-записи:
  1. Запускаем dense + lexical retrieval (как в production, но без CE и без routing).
  2. Получаем pool ~100-130 кандидатов.
  3. Для каждого кандидата считаем те же features что использует _rerank_candidates:
     dense_score, lexical_score, branch_agreement, parent_boost, level, is_l4/l5.
  4. Label = 1 если candidate.code == assigned_code, 0 — нет.

Output: data/ii25_ltr_train.jsonl с записями вида
  {"query_id": "...", "candidate_code": "...", "features": {...}, "label": 0|1}

Usage:
  python scripts/build_ltr_dataset.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, default=REPO_ROOT / "data" / "ii25_train.jsonl")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data" / "ii25_ltr_train.jsonl")
    parser.add_argument("--dense-pool", type=int, default=100)
    parser.add_argument("--lexical-pool", type=int, default=30)
    args = parser.parse_args()

    # Load train
    train_records = []
    with args.train.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                train_records.append(json.loads(line))
    print(f"Train records: {len(train_records)}")

    # Init agent (без routing — нам нужно сырое поведение retrieval)
    from classifier_agent import ClassifierAgent
    from hierarchy import branch_agreement_scores, parent_similarity_boost

    agent = ClassifierAgent()

    rows = []
    for i, rec in enumerate(train_records, 1):
        appeal_text = rec["appeal_text"]
        correct_code = rec["assigned_code"]

        dense_pool = agent._search_candidates(appeal_text, top_k=args.dense_pool)
        lex_pool = agent._search_lexical_candidates(appeal_text, top_k=args.lexical_pool)
        merged = agent._merge_candidate_pools(dense_pool, lex_pool)

        branch_scores = branch_agreement_scores(merged, level=2)
        parent_boosts = parent_similarity_boost(merged)
        query_tokens = agent._query_tokens_for_rerank(appeal_text)

        n_pos = 0
        for c in merged:
            code = c["code"]
            level_val = int(c.get("level", 0) or 0)
            features = {
                "dense_score": float(c.get("similarity", 0.0)),
                "lexical_score": float(agent._lexical_score(query_tokens, c)),
                "branch_score": float(branch_scores.get(code, 0.0)),
                "parent_score": float(parent_boosts.get(code, 0.0)),
                "level": level_val,
                "is_l4": 1 if level_val == 4 else 0,
                "is_l5": 1 if level_val == 5 else 0,
            }
            label = 1 if code == correct_code else 0
            n_pos += label
            rows.append({
                "query_id": rec["id"],
                "candidate_code": code,
                "features": features,
                "label": label,
            })

        print(f"  [{i:2d}/{len(train_records)}] {rec['id']}: pool={len(merged)}, positives={n_pos}, correct={correct_code}")

    # Save
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    total_pos = sum(r["label"] for r in rows)
    print(f"\nTotal pairs: {len(rows)}")
    print(f"Positives:   {total_pos} ({total_pos/len(rows)*100:.1f}%)")
    print(f"Negatives:   {len(rows) - total_pos}")
    print(f"Coverage:    {total_pos}/{len(train_records)} queries with positive in pool")
    print(f"Saved to:    {args.output}")


if __name__ == "__main__":
    main()

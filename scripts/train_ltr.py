"""Train Learning-to-Rank model on ii25_ltr_train.jsonl.

Features:
  dense_score, lexical_score, branch_score, parent_score, level, is_l4, is_l5

Сильно несбалансированные классы (1 positive на ~180 negatives), поэтому:
  - class_weight='balanced' для LogisticRegression
  - GradientBoostingClassifier для нелинейных взаимодействий между features

Сохраняем веса в `models/ltr_v1.json` для использования в _rerank_candidates.

Usage:
  python scripts/train_ltr.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import GroupKFold


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "data" / "ii25_ltr_train.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "models" / "ltr_v1.json"
DEFAULT_GBM_OUTPUT = REPO_ROOT / "models" / "ltr_gbm_v1.json"

FEATURE_KEYS = ["dense_score", "lexical_score", "branch_score", "parent_score", "level", "is_l4", "is_l5"]


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def to_matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    X = np.array([[float(r["features"][k]) for k in FEATURE_KEYS] for r in rows], dtype=np.float32)
    y = np.array([r["label"] for r in rows], dtype=np.int32)
    groups = [r["query_id"] for r in rows]
    return X, y, groups


def per_query_top1_accuracy(rows: list[dict], scores: np.ndarray) -> float:
    """Для каждого query_id — был ли positive на ранге 1 в reranked списке."""
    by_query: dict[str, list[tuple[float, int]]] = {}
    for i, r in enumerate(rows):
        by_query.setdefault(r["query_id"], []).append((scores[i], r["label"]))

    hits = 0
    total = 0
    for q_id, items in by_query.items():
        items.sort(key=lambda x: -x[0])
        total += 1
        if items and items[0][1] == 1:
            hits += 1
    return hits / max(total, 1)


def per_query_recall_at_k(rows: list[dict], scores: np.ndarray, k: int) -> float:
    by_query: dict[str, list[tuple[float, int]]] = {}
    for i, r in enumerate(rows):
        by_query.setdefault(r["query_id"], []).append((scores[i], r["label"]))

    hits = 0
    total = 0
    for q_id, items in by_query.items():
        items.sort(key=lambda x: -x[0])
        total += 1
        if any(label == 1 for _, label in items[:k]):
            hits += 1
    return hits / max(total, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-logreg", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-gbm", type=Path, default=DEFAULT_GBM_OUTPUT)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = load_rows(args.input)
    X, y, groups = to_matrix(rows)
    print(f"Pairs:     {len(rows)}")
    print(f"Positives: {int(y.sum())} ({y.mean() * 100:.2f}%)")
    print(f"Features:  {FEATURE_KEYS}")
    print()

    # Baseline: текущая heuristic — comnbined = dense + 0.12*lex + 0.05*branch + 0.04*parent + 0.01*is_l4
    baseline_scores = (
        X[:, 0]  # dense
        + 0.12 * X[:, 1]  # lexical
        + 0.05 * X[:, 2]  # branch
        + 0.04 * X[:, 3]  # parent
        + 0.01 * X[:, 5]  # is_l4 (proxy for level >= 4)
    )
    bl_top1 = per_query_top1_accuracy(rows, baseline_scores)
    bl_top5 = per_query_recall_at_k(rows, baseline_scores, 5)
    bl_top10 = per_query_recall_at_k(rows, baseline_scores, 10)
    print(f"BASELINE (current heuristic): Top-1={bl_top1*100:.1f}%, Top-5={bl_top5*100:.1f}%, Top-10={bl_top10*100:.1f}%")
    print()

    # === LogisticRegression ===
    print("=== LogisticRegression (class_weight=balanced) ===")
    clf = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=args.seed)
    clf.fit(X, y)
    scores = clf.decision_function(X)
    lr_top1 = per_query_top1_accuracy(rows, scores)
    lr_top5 = per_query_recall_at_k(rows, scores, 5)
    lr_top10 = per_query_recall_at_k(rows, scores, 10)
    print(f"In-sample: Top-1={lr_top1*100:.1f}%, Top-5={lr_top5*100:.1f}%, Top-10={lr_top10*100:.1f}%")
    print("Coefficients:")
    for k, c in zip(FEATURE_KEYS, clf.coef_[0]):
        print(f"  {k:15s}: {c:+.4f}")
    print(f"  intercept       : {clf.intercept_[0]:+.4f}")
    print()

    # Cross-validation by query (GroupKFold)
    if len(set(groups)) >= args.cv_folds:
        gkf = GroupKFold(n_splits=args.cv_folds)
        cv_top1 = []
        cv_top5 = []
        cv_top10 = []
        for fold, (train_idx, test_idx) in enumerate(gkf.split(X, y, groups), 1):
            clf_cv = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=args.seed)
            clf_cv.fit(X[train_idx], y[train_idx])
            scores_test = clf_cv.decision_function(X[test_idx])
            rows_test = [rows[i] for i in test_idx]
            t1 = per_query_top1_accuracy(rows_test, scores_test)
            t5 = per_query_recall_at_k(rows_test, scores_test, 5)
            t10 = per_query_recall_at_k(rows_test, scores_test, 10)
            cv_top1.append(t1)
            cv_top5.append(t5)
            cv_top10.append(t10)
        print(f"CV ({args.cv_folds}-fold by query): Top-1={np.mean(cv_top1)*100:.1f}%±{np.std(cv_top1)*100:.1f}, "
              f"Top-5={np.mean(cv_top5)*100:.1f}%±{np.std(cv_top5)*100:.1f}, "
              f"Top-10={np.mean(cv_top10)*100:.1f}%±{np.std(cv_top10)*100:.1f}")
        print()

    # Save LogReg
    weights = {
        "model_type": "logreg",
        "features": FEATURE_KEYS,
        "coef": clf.coef_[0].tolist(),
        "intercept": float(clf.intercept_[0]),
    }
    args.output_logreg.parent.mkdir(parents=True, exist_ok=True)
    with args.output_logreg.open("w", encoding="utf-8") as f:
        json.dump(weights, f, ensure_ascii=False, indent=2)
    print(f"LogReg saved to {args.output_logreg}")

    # === GradientBoosting ===
    print("\n=== GradientBoostingClassifier ===")
    gbm = GradientBoostingClassifier(
        n_estimators=100, max_depth=3, learning_rate=0.05, random_state=args.seed,
    )
    # Sample weights чтобы дать вес positives
    sample_weights = np.where(y == 1, 1.0 / max(y.mean(), 1e-6) / 10, 1.0)
    gbm.fit(X, y, sample_weight=sample_weights)
    gbm_scores = gbm.decision_function(X)
    gbm_top1 = per_query_top1_accuracy(rows, gbm_scores)
    gbm_top5 = per_query_recall_at_k(rows, gbm_scores, 5)
    gbm_top10 = per_query_recall_at_k(rows, gbm_scores, 10)
    print(f"In-sample: Top-1={gbm_top1*100:.1f}%, Top-5={gbm_top5*100:.1f}%, Top-10={gbm_top10*100:.1f}%")
    print("Feature importance:")
    for k, imp in zip(FEATURE_KEYS, gbm.feature_importances_):
        print(f"  {k:15s}: {imp:.4f}")

    # Save GBM via joblib (used by classifier_agent if available)
    try:
        import joblib
        joblib_path = args.output_gbm.with_suffix(".joblib")
        joblib.dump(gbm, joblib_path)
        print(f"GBM model saved to {joblib_path}")
    except Exception as e:
        print(f"GBM save failed: {e}")


if __name__ == "__main__":
    main()

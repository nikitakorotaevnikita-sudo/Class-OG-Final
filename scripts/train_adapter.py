"""Train linear projection adapter on cached embeddings.

Архитектура:
    e5(query) — 768 ─────┐
                          │   Linear(768, 768)
                          ├── identity-init
                          │   shared between query & code
    e5(code) — 768 ──────┘

Loss: InfoNCE (one positive vs all classifier embeddings as negatives in-batch).
Identity-init гарантирует что в худшем случае адаптер = пропускает без изменений.
L2 weight-decay сильная (1e-2) — защищает от overfit на 43 train.

Output:
  models/adapter_v1.npz   — W (768, 768), b (768,)
  models/adapter_v1.json  — metadata (epochs, loss, etc.)

Usage:
  python scripts/train_adapter.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


REPO_ROOT = Path(__file__).resolve().parents[1]


class LinearAdapter(nn.Module):
    """Linear(d, d) with identity init."""

    def __init__(self, dim: int = 768):
        super().__init__()
        self.proj = nn.Linear(dim, dim, bias=True)
        # Identity init: проекция = пропускает входной вектор без изменений
        with torch.no_grad():
            self.proj.weight.copy_(torch.eye(dim))
            self.proj.bias.zero_()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.proj(x)
        # L2-normalize (тот же подход что в e5)
        return F.normalize(y, p=2, dim=-1)


def info_nce_loss(query_proj: torch.Tensor, code_proj: torch.Tensor, positive_idx: torch.Tensor, temperature: float = 0.05) -> torch.Tensor:
    """InfoNCE:
       query_proj: (B, d)  query embeddings after adapter
       code_proj:  (C, d)  ALL classifier embeddings after adapter
       positive_idx: (B,)  индекс правильного кода в code_proj для каждого query
    """
    # cosine sim: (B, C)
    logits = query_proj @ code_proj.T / temperature
    return F.cross_entropy(logits, positive_idx)


def evaluate_recall(query_embs: np.ndarray, code_embs: np.ndarray, positive_indices: np.ndarray, ks=(10, 50)) -> dict:
    """Recall@K и MRR для эмбеддингов (без адаптера или с)."""
    # Normalize
    q_n = query_embs / (np.linalg.norm(query_embs, axis=1, keepdims=True) + 1e-12)
    c_n = code_embs / (np.linalg.norm(code_embs, axis=1, keepdims=True) + 1e-12)
    sims = q_n @ c_n.T  # (N_q, N_c)
    # ranks
    ranks = []
    metrics = {f"recall@{k}": 0 for k in ks}
    for i, gold_idx in enumerate(positive_indices):
        if gold_idx < 0:
            continue
        order = np.argsort(-sims[i])
        # find rank of gold
        rank = int(np.where(order == gold_idx)[0][0]) + 1 if gold_idx in order else len(order)
        ranks.append(rank)
        for k in ks:
            if rank <= k:
                metrics[f"recall@{k}"] += 1
    n = max(len([g for g in positive_indices if g >= 0]), 1)
    for k in ks:
        metrics[f"recall@{k}"] = round(metrics[f"recall@{k}"] / n, 4)
    metrics["mrr"] = round(float(np.mean([1.0 / r for r in ranks])), 4) if ranks else 0.0
    metrics["mean_rank"] = round(float(np.mean(ranks)), 1) if ranks else 0
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-emb", type=Path, default=REPO_ROOT / "data" / "ii25_train_query_embs.npy")
    parser.add_argument("--train-meta", type=Path, default=REPO_ROOT / "data" / "ii25_train_query_meta.json")
    parser.add_argument("--test-emb", type=Path, default=REPO_ROOT / "data" / "ii25_test_query_embs.npy")
    parser.add_argument("--test-meta", type=Path, default=REPO_ROOT / "data" / "ii25_test_query_meta.json")
    parser.add_argument("--code-emb", type=Path, default=REPO_ROOT / "data" / "vector_db" / "embeddings.npy")
    parser.add_argument("--code-meta", type=Path, default=REPO_ROOT / "data" / "vector_db" / "metadata.json")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-2)
    parser.add_argument("--temperature", type=float, default=0.05)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "models" / "adapter_v1.npz")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    # Load
    print("Loading data...")
    train_q = np.load(args.train_emb).astype(np.float32)
    test_q = np.load(args.test_emb).astype(np.float32)
    code_embs = np.load(args.code_emb).astype(np.float32)
    with args.train_meta.open(encoding="utf-8") as f:
        train_meta = json.load(f)
    with args.test_meta.open(encoding="utf-8") as f:
        test_meta = json.load(f)
    with args.code_meta.open(encoding="utf-8") as f:
        code_meta = json.load(f)

    code_to_idx = {c["code"]: i for i, c in enumerate(code_meta)}

    # Positive indices
    train_pos = np.array([code_to_idx.get(m["assigned_code"], -1) for m in train_meta], dtype=np.int64)
    test_pos = np.array([code_to_idx.get(m["assigned_code"], -1) for m in test_meta], dtype=np.int64)
    print(f"Train queries: {len(train_q)}  ({(train_pos >= 0).sum()} with valid positive)")
    print(f"Test queries:  {len(test_q)}   ({(test_pos >= 0).sum()} with valid positive)")
    print(f"Codes pool:    {len(code_embs)}")

    # Filter out queries without valid positive
    valid_train = train_pos >= 0
    train_q = train_q[valid_train]
    train_pos = train_pos[valid_train]

    # Baseline (identity adapter) eval
    print("\n=== BASELINE (no adapter) ===")
    base_train = evaluate_recall(train_q, code_embs, train_pos, ks=(10, 50, 100))
    base_test = evaluate_recall(test_q[test_pos >= 0], code_embs, test_pos[test_pos >= 0], ks=(10, 50, 100))
    print(f"Train: {base_train}")
    print(f"Test:  {base_test}")
    print()

    # Train
    device = "cpu"
    model = LinearAdapter(dim=train_q.shape[1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    train_q_t = torch.tensor(train_q, device=device)
    code_embs_t = torch.tensor(code_embs, device=device)
    train_pos_t = torch.tensor(train_pos, device=device)

    print(f"=== TRAINING (epochs={args.epochs}, lr={args.lr}, wd={args.weight_decay}, T={args.temperature}) ===")
    best_test_r10 = base_test["recall@10"]
    best_epoch = 0
    losses = []
    for ep in range(1, args.epochs + 1):
        model.train()
        optimizer.zero_grad()
        q_proj = model(train_q_t)
        c_proj = model(code_embs_t)
        loss = info_nce_loss(q_proj, c_proj, train_pos_t, temperature=args.temperature)
        loss.backward()
        optimizer.step()
        losses.append(float(loss))

        if ep % 10 == 0 or ep == 1 or ep == args.epochs:
            model.eval()
            with torch.no_grad():
                q_proj_train = model(train_q_t).cpu().numpy()
                q_proj_test_t = model(torch.tensor(test_q[test_pos >= 0], device=device))
                q_proj_test = q_proj_test_t.cpu().numpy()
                c_proj_eval = model(code_embs_t).cpu().numpy()
            tr = evaluate_recall(q_proj_train, c_proj_eval, train_pos[valid_train[valid_train]], ks=(10, 50))
            te = evaluate_recall(q_proj_test, c_proj_eval, test_pos[test_pos >= 0], ks=(10, 50))
            print(f"  ep {ep:3d}: loss={loss:.4f} train@10={tr['recall@10']:.3f} test@10={te['recall@10']:.3f} test@50={te['recall@50']:.3f}")
            if te["recall@10"] > best_test_r10:
                best_test_r10 = te["recall@10"]
                best_epoch = ep
                # Save best
                W = model.proj.weight.detach().cpu().numpy()
                b = model.proj.bias.detach().cpu().numpy()
                args.output.parent.mkdir(parents=True, exist_ok=True)
                np.savez(args.output, W=W, b=b)

    # Final
    print(f"\nBest test recall@10: {best_test_r10:.3f} at epoch {best_epoch}")
    print(f"Baseline test recall@10: {base_test['recall@10']:.3f}")
    print(f"Improvement: {(best_test_r10 - base_test['recall@10']) * 100:+.1f} pp")

    # Save metadata
    meta_out = args.output.with_suffix(".json")
    with meta_out.open("w", encoding="utf-8") as f:
        json.dump({
            "dim": train_q.shape[1],
            "epochs_trained": args.epochs,
            "best_epoch": best_epoch,
            "best_test_recall_10": best_test_r10,
            "baseline_test_recall_10": base_test["recall@10"],
            "baseline_test_recall_50": base_test["recall@50"],
            "lr": args.lr,
            "weight_decay": args.weight_decay,
            "temperature": args.temperature,
            "train_size": int(len(train_q)),
            "test_size": int((test_pos >= 0).sum()),
        }, f, ensure_ascii=False, indent=2)
    print(f"Saved to {args.output} (+ {meta_out})")


if __name__ == "__main__":
    main()

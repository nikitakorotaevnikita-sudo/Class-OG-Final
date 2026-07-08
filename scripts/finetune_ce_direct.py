"""Fine-tune cross-encoder через transformers напрямую (memory-safe, без sentence-transformers).

Дообучаем DiTy/cross-encoder-russian-msmarco на ii25_reranker_train.jsonl (405 pairs)
с BinaryCrossEntropyLoss. Маленький batch, короткие пары — чтобы не упасть по памяти.

Output:
  models/ce_finetuned_v1/  — сохранённая модель (HF format)

Usage:
  python scripts/finetune_ce_direct.py [--base DiTy/cross-encoder-russian-msmarco]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
import torch
torch.set_num_threads(1)


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_pairs(path: Path) -> list[dict]:
    items = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=REPO_ROOT / "data" / "ii25_reranker_train.jsonl")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "models" / "ce_finetuned_v1")
    parser.add_argument("--base", default="DiTy/cross-encoder-russian-msmarco")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--accum-steps", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--val-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)

    triplets = load_pairs(args.input)
    print(f"Loaded {len(triplets)} pairs", flush=True)

    # Split by source_id (group split)
    by_src: dict[str, list[dict]] = {}
    for t in triplets:
        by_src.setdefault(t["source_id"], []).append(t)
    src_ids = list(by_src.keys())
    rng.shuffle(src_ids)
    val_n = max(1, int(len(src_ids) * args.val_fraction))
    val_ids = set(src_ids[:val_n])
    train_pairs = [t for sid, items in by_src.items() if sid not in val_ids for t in items]
    val_pairs = [t for sid in val_ids for t in by_src[sid]]
    print(f"Train: {len(train_pairs)} ({len(src_ids) - val_n} queries)", flush=True)
    print(f"Val:   {len(val_pairs)} ({val_n} queries)", flush=True)

    print(f"Loading base model: {args.base}", flush=True)
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForSequenceClassification.from_pretrained(args.base)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    loss_fn = torch.nn.BCEWithLogitsLoss()

    def eval_val() -> tuple[float, float]:
        model.eval()
        with torch.no_grad():
            queries = [p["query"] for p in val_pairs]
            cands = [p["candidate"] for p in val_pairs]
            labels = torch.tensor([p["label"] for p in val_pairs], dtype=torch.float32)
            # Batch inference
            all_logits = []
            for i in range(0, len(queries), args.batch_size):
                batch_q = queries[i:i+args.batch_size]
                batch_c = cands[i:i+args.batch_size]
                inputs = tok(batch_q, batch_c, padding=True, truncation=True, return_tensors="pt", max_length=args.max_length)
                out = model(**inputs, return_dict=True)
                all_logits.append(out.logits.view(-1).float())
            logits = torch.cat(all_logits)
            loss = float(loss_fn(logits, labels))
            preds = (logits > 0).float()
            acc = float((preds == labels).float().mean())
            return loss, acc

    print(f"\nVal BEFORE training:", flush=True)
    val_loss_b, val_acc_b = eval_val()
    print(f"  loss={val_loss_b:.4f}  acc={val_acc_b:.4f}", flush=True)

    print(f"\n=== TRAINING (epochs={args.epochs}, batch={args.batch_size}*{args.accum_steps}, lr={args.lr}) ===", flush=True)
    model.train()
    step = 0
    for ep in range(1, args.epochs + 1):
        rng.shuffle(train_pairs)
        epoch_loss = 0.0
        n_batches = 0
        optimizer.zero_grad()
        for i in range(0, len(train_pairs), args.batch_size):
            batch = train_pairs[i:i+args.batch_size]
            q = [p["query"] for p in batch]
            c = [p["candidate"] for p in batch]
            labels = torch.tensor([p["label"] for p in batch], dtype=torch.float32)
            inputs = tok(q, c, padding=True, truncation=True, return_tensors="pt", max_length=args.max_length)
            out = model(**inputs, return_dict=True)
            logits = out.logits.view(-1).float()
            loss = loss_fn(logits, labels) / args.accum_steps
            loss.backward()
            epoch_loss += float(loss) * args.accum_steps
            n_batches += 1
            step += 1
            if step % args.accum_steps == 0:
                optimizer.step()
                optimizer.zero_grad()
        # Final step if pending
        optimizer.step()
        optimizer.zero_grad()
        avg_loss = epoch_loss / max(n_batches, 1)
        val_loss, val_acc = eval_val()
        model.train()
        print(f"  ep {ep}: train_loss={avg_loss:.4f}  val_loss={val_loss:.4f}  val_acc={val_acc:.4f}", flush=True)

    val_loss_a, val_acc_a = eval_val()
    print(f"\nVal AFTER training:", flush=True)
    print(f"  loss={val_loss_a:.4f}  acc={val_acc_a:.4f}", flush=True)
    print(f"  Acc improvement: {(val_acc_a - val_acc_b) * 100:+.1f} pp", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output)
    tok.save_pretrained(args.output)
    print(f"\nSaved to {args.output}", flush=True)
    print(f"Use: CROSS_ENCODER_MODEL={args.output}", flush=True)


if __name__ == "__main__":
    main()

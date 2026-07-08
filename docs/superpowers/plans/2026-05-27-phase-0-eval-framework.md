# Phase 0 — Eval Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Построить eval framework, который мерит per-stage метрики с failure attribution, и зафиксировать baseline текущего pipeline на `data/ii25_test.jsonl` (56 кейсов).

**Architecture:** Расширяем существующий `tests/eval_metrics_helpers.py` чистыми helper-функциями (`attribute_failure`, `compute_per_stage_metrics`). Строим новый orchestrator `tests/eval_hierarchical.py` с `--stage` флагом для селективного запуска (retrieval | reranker | final | all). Output: per-case JSONL + markdown summary + baseline snapshot JSON.

**Tech Stack:** Python 3.11, pytest, existing ClassifierAgent (`src/classifier_agent.py`), existing eval_metrics_helpers.

**Связь со spec:** реализует [Section 8 (Evaluation Framework) из spec](../specs/2026-05-27-hierarchical-classification-design.md#8-evaluation-framework), Phase 0 из rollout plan.

---

## File Structure

| Файл | Назначение | Создаём/Меняем |
|---|---|---|
| `tests/eval_metrics_helpers.py` | + `attribute_failure()`, + `compute_per_stage_metrics()` | Меняем |
| `tests/test_eval_metrics_helpers.py` | + тесты для новых helpers | Меняем |
| `tests/eval_hierarchical.py` | Новый orchestrator, --stage flag, output writers | **Создаём** |
| `eval_results/` | Output dir (gitignored, кроме .gitkeep) | **Создаём** |
| `data/eval_baselines/` | Baseline snapshots (трекаются git'ом) | **Создаём** |
| `data/eval_baselines/2026-05-27_baseline.json` | Фиксированный baseline | **Создаём** |
| `.gitignore` | Добавить `eval_results/*` кроме .gitkeep | Меняем |
| `docs/HANDOFF.md` | Описание eval framework usage | Меняем |

**Принцип:** все pure-функции (метрики, attribution) — в `eval_metrics_helpers.py` с тестами. I/O и orchestration — в `eval_hierarchical.py`. Это даёт ~80% покрытия unit-тестами без необходимости тяжёлых fixture'ов.

---

## Task 1: Создать output-директории

**Files:**
- Create: `eval_results/.gitkeep`
- Create: `data/eval_baselines/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Создать директории и .gitkeep**

```bash
mkdir -p eval_results data/eval_baselines
touch eval_results/.gitkeep data/eval_baselines/.gitkeep
```

- [ ] **Step 2: Обновить .gitignore**

Добавить в конец `.gitignore`:

```
# Eval results (raw output, generated per-run)
eval_results/*
!eval_results/.gitkeep
```

`data/eval_baselines/` НЕ игнорируем — baseline snapshots должны быть в git для diff'ов.

- [ ] **Step 3: Commit**

```bash
git add eval_results/.gitkeep data/eval_baselines/.gitkeep .gitignore
git commit -m "chore: add eval framework directories"
```

---

## Task 2: Тесты для `attribute_failure()`

**Files:**
- Modify: `tests/test_eval_metrics_helpers.py`

- [ ] **Step 1: Прочитать существующий test file для понимания стиля**

```bash
cat tests/test_eval_metrics_helpers.py | head -50
```

- [ ] **Step 2: Написать failing-тесты для `attribute_failure()`**

Добавить в конец `tests/test_eval_metrics_helpers.py`:

```python
# Tests for attribute_failure (Phase 0 baseline attribution)

from eval_metrics_helpers import attribute_failure  # will fail import until impl


def test_attribute_correct_when_top1_in_gold():
    result = attribute_failure(
        gold_codes=["0001.0002.0003.0004"],
        dense_top50_codes=["0001.0002.0003.0004", "0005.0006.0007.0008"],
        reranked_top10_codes=["0001.0002.0003.0004"],
        final_top1_code="0001.0002.0003.0004",
    )
    assert result == "correct"


def test_attribute_llm_final_when_gold_in_top10_but_top1_wrong():
    result = attribute_failure(
        gold_codes=["0001.0002.0003.0004"],
        dense_top50_codes=["0001.0002.0003.0004"],
        reranked_top10_codes=["0005.0006.0007.0008", "0001.0002.0003.0004"],
        final_top1_code="0005.0006.0007.0008",
    )
    assert result == "llm_final"


def test_attribute_reranker_when_gold_in_dense_but_not_in_top10():
    result = attribute_failure(
        gold_codes=["0001.0002.0003.0004"],
        dense_top50_codes=["0001.0002.0003.0004", "0009.0009.0009.0009"],
        reranked_top10_codes=["0005.0006.0007.0008", "0009.0009.0009.0009"],
        final_top1_code="0005.0006.0007.0008",
    )
    assert result == "reranker"


def test_attribute_retrieval_recall_when_gold_not_in_dense():
    result = attribute_failure(
        gold_codes=["0001.0002.0003.0004"],
        dense_top50_codes=["0005.0006.0007.0008", "0009.0009.0009.0009"],
        reranked_top10_codes=["0005.0006.0007.0008"],
        final_top1_code="0005.0006.0007.0008",
    )
    assert result == "retrieval_recall"


def test_attribute_no_gold_when_gold_codes_empty():
    result = attribute_failure(
        gold_codes=[],
        dense_top50_codes=["0001"],
        reranked_top10_codes=["0001"],
        final_top1_code="0001",
    )
    assert result == "no_gold"


def test_attribute_multi_gold_correct():
    # Кейс с несколькими допустимыми кодами (multi-question appeal)
    result = attribute_failure(
        gold_codes=["0001.0002.0003.0004", "0005.0006.0007.0008"],
        dense_top50_codes=["0005.0006.0007.0008"],
        reranked_top10_codes=["0005.0006.0007.0008"],
        final_top1_code="0005.0006.0007.0008",
    )
    assert result == "correct"
```

- [ ] **Step 3: Запустить и убедиться, что fail с ImportError**

```bash
./venv/Scripts/python.exe -m pytest tests/test_eval_metrics_helpers.py::test_attribute_correct_when_top1_in_gold -v
```
Expected: FAIL — `ImportError: cannot import name 'attribute_failure'`

---

## Task 3: Реализовать `attribute_failure()`

**Files:**
- Modify: `tests/eval_metrics_helpers.py`

- [ ] **Step 1: Добавить функцию в конец файла**

Добавить в `tests/eval_metrics_helpers.py`:

```python
def attribute_failure(
    *,
    gold_codes: Sequence[str],
    dense_top50_codes: Sequence[str],
    reranked_top10_codes: Sequence[str],
    final_top1_code: str | None,
) -> str:
    """Categorize which pipeline stage failed.

    Phase 0 baseline categories (will be extended in Phase 1+ to include
    router_stage1, router_stage2):
      - correct: final top-1 prediction is in gold
      - llm_final: gold in reranked top-10, but LLM picked wrong code
      - reranker: gold in dense top-50, but reranker dropped it from top-10
      - retrieval_recall: gold not even in dense top-50
      - no_gold: gold_codes is empty (eval skip)
    """
    if not gold_codes:
        return "no_gold"

    gold_set = set(gold_codes)
    if final_top1_code in gold_set:
        return "correct"
    if any(code in gold_set for code in reranked_top10_codes):
        return "llm_final"
    if any(code in gold_set for code in dense_top50_codes):
        return "reranker"
    return "retrieval_recall"
```

- [ ] **Step 2: Запустить тесты — должны пройти**

```bash
./venv/Scripts/python.exe -m pytest tests/test_eval_metrics_helpers.py -v -k attribute
```
Expected: 6 passed

- [ ] **Step 3: Commit**

```bash
git add tests/eval_metrics_helpers.py tests/test_eval_metrics_helpers.py
git commit -m "feat(eval): add attribute_failure helper for Phase 0 baseline"
```

---

## Task 4: Тесты для `compute_per_stage_metrics()`

**Files:**
- Modify: `tests/test_eval_metrics_helpers.py`

- [ ] **Step 1: Написать failing-тесты**

Добавить в `tests/test_eval_metrics_helpers.py`:

```python
# Tests for compute_per_stage_metrics

from eval_metrics_helpers import compute_per_stage_metrics  # will fail until impl


def test_compute_per_stage_metrics_empty():
    summary = compute_per_stage_metrics(per_case_rows=[])
    assert summary["total"] == 0
    assert summary["correct"] == 0
    assert summary["failure_breakdown"] == {}


def test_compute_per_stage_metrics_mixed():
    rows = [
        {"failure_attribution": "correct"},
        {"failure_attribution": "correct"},
        {"failure_attribution": "llm_final"},
        {"failure_attribution": "reranker"},
        {"failure_attribution": "retrieval_recall"},
    ]
    summary = compute_per_stage_metrics(per_case_rows=rows)
    assert summary["total"] == 5
    assert summary["correct"] == 2
    assert summary["top1_accuracy_pct"] == 40.0
    assert summary["failure_breakdown"] == {
        "llm_final": 1,
        "reranker": 1,
        "retrieval_recall": 1,
    }


def test_compute_per_stage_metrics_with_top_k_metrics():
    rows = [
        {"failure_attribution": "correct", "dense_recall_at_10": True, "dense_recall_at_50": True, "reranked_recall_at_10": True},
        {"failure_attribution": "llm_final", "dense_recall_at_10": True, "dense_recall_at_50": True, "reranked_recall_at_10": True},
        {"failure_attribution": "reranker", "dense_recall_at_10": True, "dense_recall_at_50": True, "reranked_recall_at_10": False},
        {"failure_attribution": "retrieval_recall", "dense_recall_at_10": False, "dense_recall_at_50": False, "reranked_recall_at_10": False},
    ]
    summary = compute_per_stage_metrics(per_case_rows=rows)
    assert summary["dense_recall_at_10_pct"] == 75.0
    assert summary["dense_recall_at_50_pct"] == 75.0
    assert summary["reranked_recall_at_10_pct"] == 50.0


def test_compute_per_stage_metrics_skips_no_gold():
    rows = [
        {"failure_attribution": "correct"},
        {"failure_attribution": "no_gold"},
    ]
    summary = compute_per_stage_metrics(per_case_rows=rows)
    # "no_gold" rows excluded from accuracy denominator
    assert summary["total"] == 2
    assert summary["evaluable"] == 1
    assert summary["correct"] == 1
    assert summary["top1_accuracy_pct"] == 100.0
```

- [ ] **Step 2: Запустить и убедиться в fail**

```bash
./venv/Scripts/python.exe -m pytest tests/test_eval_metrics_helpers.py -v -k compute_per_stage
```
Expected: FAIL — ImportError

---

## Task 5: Реализовать `compute_per_stage_metrics()`

**Files:**
- Modify: `tests/eval_metrics_helpers.py`

- [ ] **Step 1: Добавить функцию**

Добавить в `tests/eval_metrics_helpers.py`:

```python
def compute_per_stage_metrics(*, per_case_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-case eval rows into a summary dict.

    Args:
        per_case_rows: list of dicts each containing at least 'failure_attribution'
                       and optionally boolean stage flags (dense_recall_at_K, etc.)

    Returns:
        Dict with: total, evaluable (excludes no_gold), correct, top1_accuracy_pct,
        failure_breakdown (dict by category), and per-stage recall metrics if present.
    """
    rows = list(per_case_rows)
    total = len(rows)
    evaluable_rows = [r for r in rows if r.get("failure_attribution") != "no_gold"]
    evaluable = len(evaluable_rows)
    correct = sum(1 for r in evaluable_rows if r.get("failure_attribution") == "correct")

    failure_breakdown: dict[str, int] = {}
    for r in evaluable_rows:
        attr = r.get("failure_attribution")
        if attr and attr != "correct":
            failure_breakdown[attr] = failure_breakdown.get(attr, 0) + 1

    summary: dict[str, Any] = {
        "total": total,
        "evaluable": evaluable,
        "correct": correct,
        "top1_accuracy_pct": round(100.0 * correct / evaluable, 1) if evaluable else 0.0,
        "failure_breakdown": failure_breakdown,
    }

    # Optional stage recall metrics — compute if present in any row
    for key in ["dense_recall_at_10", "dense_recall_at_50", "reranked_recall_at_10"]:
        flagged = [r for r in evaluable_rows if key in r]
        if flagged:
            true_count = sum(1 for r in flagged if r.get(key) is True)
            summary[f"{key}_pct"] = round(100.0 * true_count / len(flagged), 1)

    return summary
```

- [ ] **Step 2: Запустить тесты**

```bash
./venv/Scripts/python.exe -m pytest tests/test_eval_metrics_helpers.py -v
```
Expected: все тесты pass (включая старые)

- [ ] **Step 3: Commit**

```bash
git add tests/eval_metrics_helpers.py tests/test_eval_metrics_helpers.py
git commit -m "feat(eval): add compute_per_stage_metrics aggregator"
```

---

## Task 6: Skeleton `tests/eval_hierarchical.py` с argparse

**Files:**
- Create: `tests/eval_hierarchical.py`

- [ ] **Step 1: Создать файл со скелетом**

Создать `tests/eval_hierarchical.py`:

```python
"""Hierarchical pipeline evaluation with per-stage failure attribution.

Phase 0: baseline evaluation of the current pipeline (no router LLM yet).
Phase 1+ will extend stage_outputs with router_l1/router_l2.

Usage:
    python tests/eval_hierarchical.py --dataset data/ii25_test.jsonl --stage all
    python tests/eval_hierarchical.py --stage retrieval  # no LLM, fast
    python tests/eval_hierarchical.py --stage reranker   # no LLM
    python tests/eval_hierarchical.py --stage final      # full pipeline with LLM
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from classifier_agent import ClassifierAgent  # noqa: E402
from eval_metrics_helpers import (  # noqa: E402
    attribute_failure,
    compute_per_stage_metrics,
    first_expected_rank,
)


DEFAULT_DATASET = REPO_ROOT / "data" / "ii25_test.jsonl"
EVAL_RESULTS_DIR = REPO_ROOT / "eval_results"
BASELINES_DIR = REPO_ROOT / "data" / "eval_baselines"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hierarchical pipeline evaluation")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Path to JSONL dataset (default: data/ii25_test.jsonl)",
    )
    parser.add_argument(
        "--stage",
        choices=["retrieval", "reranker", "final", "all"],
        default="all",
        help="Which pipeline stage to evaluate (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only first N cases (for smoke testing)",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save summary as baseline snapshot to data/eval_baselines/",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="run",
        help="Tag for output filename (e.g., 'baseline', 'v1.1-reranker-fix')",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(f"[eval_hierarchical] dataset={args.dataset.name} stage={args.stage}")
    # Implementation in next tasks
    raise NotImplementedError("evaluate_record() not yet implemented")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-run — должно упасть с NotImplementedError**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 1 --stage retrieval
```
Expected: NotImplementedError (правильно — мы пока не реализовали core)

- [ ] **Step 3: Commit**

```bash
git add tests/eval_hierarchical.py
git commit -m "feat(eval): add eval_hierarchical.py skeleton with --stage flag"
```

---

## Task 7: Реализовать `evaluate_record()` для stage=retrieval

**Files:**
- Modify: `tests/eval_hierarchical.py`

- [ ] **Step 1: Добавить функцию `evaluate_record_retrieval()`**

Заменить в `tests/eval_hierarchical.py` функцию `main()` и добавить выше неё:

```python
def evaluate_record_retrieval(
    agent: ClassifierAgent, record: dict[str, Any], dense_top_k: int = 50
) -> dict[str, Any]:
    """Run dense retrieval only — no reranker, no LLM. Fast (~50ms/case)."""
    text = record["appeal_text"]
    gold_code = record["assigned_code"]
    gold_codes = [gold_code]  # single-gold dataset; could be list in future

    started = time.time()
    dense_candidates = agent._search_candidates(text, top_k=dense_top_k)
    elapsed = round(time.time() - started, 3)

    dense_codes = [c["code"] for c in dense_candidates]
    dense_top50 = dense_codes[:50]
    rank = first_expected_rank(dense_codes, gold_codes)

    return {
        "case_id": record["id"],
        "gold_code": gold_code,
        "dense_top50_codes": dense_top50,
        "dense_top10_codes": dense_codes[:10],
        "dense_rank": rank,
        "dense_recall_at_10": (rank is not None and rank <= 10),
        "dense_recall_at_50": (rank is not None and rank <= 50),
        "elapsed": elapsed,
        # Stages below are not run in --stage retrieval; mark for attribution
        "reranked_top10_codes": [],
        "final_top1_code": None,
    }
```

- [ ] **Step 2: Обновить `main()`**

Заменить `main()`:

```python
def main() -> int:
    args = parse_args()
    print(f"[eval_hierarchical] dataset={args.dataset.name} stage={args.stage}")

    records = load_jsonl(args.dataset)
    if args.limit:
        records = records[: args.limit]
    print(f"[eval_hierarchical] loaded {len(records)} records")

    print("[eval_hierarchical] initializing ClassifierAgent...")
    agent = ClassifierAgent()

    rows: list[dict[str, Any]] = []
    for i, record in enumerate(records, start=1):
        print(f"  [{i}/{len(records)}] {record['id']}", end=" ")
        if args.stage == "retrieval":
            row = evaluate_record_retrieval(agent, record)
        else:
            raise NotImplementedError(f"stage={args.stage} not yet implemented")
        rows.append(row)
        print(f"rank={row.get('dense_rank')} t={row['elapsed']}s")

    # Phase 0: attribute failures only for stages that ran
    for row in rows:
        row["failure_attribution"] = attribute_failure(
            gold_codes=[row["gold_code"]],
            dense_top50_codes=row.get("dense_top50_codes", []),
            reranked_top10_codes=row.get("reranked_top10_codes", []),
            final_top1_code=row.get("final_top1_code"),
        )

    summary = compute_per_stage_metrics(per_case_rows=rows)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0
```

- [ ] **Step 3: Smoke-test на одном кейсе**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 1 --stage retrieval
```
Expected: вывод dense_rank и summary с dense_recall_at_50_pct

- [ ] **Step 4: Commit**

```bash
git add tests/eval_hierarchical.py
git commit -m "feat(eval): implement --stage retrieval for eval_hierarchical"
```

---

## Task 8: Реализовать stage=reranker

**Files:**
- Modify: `tests/eval_hierarchical.py`

- [ ] **Step 1: Добавить `evaluate_record_reranker()`**

Добавить ниже `evaluate_record_retrieval()`:

```python
def evaluate_record_reranker(
    agent: ClassifierAgent, record: dict[str, Any]
) -> dict[str, Any]:
    """Run dense + lexical + reranker — no LLM."""
    text = record["appeal_text"]
    gold_code = record["assigned_code"]
    gold_codes = [gold_code]

    started = time.time()
    dense_candidates = agent._search_candidates(text, top_k=50)
    lexical_candidates = agent._search_lexical_candidates(text, top_k=30)
    merged = agent._merge_candidate_pools(dense_candidates, lexical_candidates)
    reranked = agent._rerank_candidates(text, merged, top_k=10)
    elapsed = round(time.time() - started, 3)

    dense_codes = [c["code"] for c in dense_candidates]
    reranked_codes = [c["code"] for c in reranked]
    dense_rank = first_expected_rank(dense_codes, gold_codes)
    reranked_rank = first_expected_rank(reranked_codes, gold_codes)

    return {
        "case_id": record["id"],
        "gold_code": gold_code,
        "dense_top50_codes": dense_codes[:50],
        "dense_top10_codes": dense_codes[:10],
        "dense_rank": dense_rank,
        "dense_recall_at_10": (dense_rank is not None and dense_rank <= 10),
        "dense_recall_at_50": (dense_rank is not None and dense_rank <= 50),
        "reranked_top10_codes": reranked_codes,
        "reranked_rank": reranked_rank,
        "reranked_recall_at_10": (reranked_rank is not None and reranked_rank <= 10),
        "elapsed": elapsed,
        "final_top1_code": None,
    }
```

- [ ] **Step 2: Подключить в `main()`**

В `main()` заменить блок:
```python
if args.stage == "retrieval":
    row = evaluate_record_retrieval(agent, record)
else:
    raise NotImplementedError(f"stage={args.stage} not yet implemented")
```
на:
```python
if args.stage == "retrieval":
    row = evaluate_record_retrieval(agent, record)
elif args.stage == "reranker":
    row = evaluate_record_reranker(agent, record)
else:
    raise NotImplementedError(f"stage={args.stage} not yet implemented")
```

- [ ] **Step 3: Smoke-test**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 2 --stage reranker
```
Expected: вывод с reranked_rank, summary с reranked_recall_at_10_pct

- [ ] **Step 4: Commit**

```bash
git add tests/eval_hierarchical.py
git commit -m "feat(eval): implement --stage reranker for eval_hierarchical"
```

---

## Task 9: Реализовать stage=final (full pipeline с LLM)

**Files:**
- Modify: `tests/eval_hierarchical.py`

- [ ] **Step 1: Добавить `evaluate_record_final()`**

Добавить ниже `evaluate_record_reranker()`:

```python
def evaluate_record_final(
    agent: ClassifierAgent, record: dict[str, Any]
) -> dict[str, Any]:
    """Full pipeline including LLM final pick."""
    text = record["appeal_text"]
    gold_code = record["assigned_code"]
    gold_codes = [gold_code]

    started = time.time()

    # Pre-LLM stages (collect for attribution)
    dense_candidates = agent._search_candidates(text, top_k=50)
    lexical_candidates = agent._search_lexical_candidates(text, top_k=30)
    merged = agent._merge_candidate_pools(dense_candidates, lexical_candidates)
    reranked = agent._rerank_candidates(text, merged, top_k=10)

    # LLM final
    try:
        result = agent.classify(text)
        final_codes = [q.code for q in result.questions if q.code]
        final_top1 = final_codes[0] if final_codes else None
        llm_error = None
    except Exception as exc:  # noqa: BLE001
        final_codes = []
        final_top1 = None
        llm_error = str(exc)

    elapsed = round(time.time() - started, 3)

    dense_codes = [c["code"] for c in dense_candidates]
    reranked_codes = [c["code"] for c in reranked]
    dense_rank = first_expected_rank(dense_codes, gold_codes)
    reranked_rank = first_expected_rank(reranked_codes, gold_codes)

    return {
        "case_id": record["id"],
        "gold_code": gold_code,
        "dense_top50_codes": dense_codes[:50],
        "dense_top10_codes": dense_codes[:10],
        "dense_rank": dense_rank,
        "dense_recall_at_10": (dense_rank is not None and dense_rank <= 10),
        "dense_recall_at_50": (dense_rank is not None and dense_rank <= 50),
        "reranked_top10_codes": reranked_codes,
        "reranked_rank": reranked_rank,
        "reranked_recall_at_10": (reranked_rank is not None and reranked_rank <= 10),
        "final_top1_code": final_top1,
        "final_codes": final_codes,
        "llm_error": llm_error,
        "elapsed": elapsed,
    }
```

- [ ] **Step 2: Подключить в `main()`**

Добавить в `main()` branch:
```python
elif args.stage == "final" or args.stage == "all":
    row = evaluate_record_final(agent, record)
```

Финальный блок dispatch в `main()`:
```python
if args.stage == "retrieval":
    row = evaluate_record_retrieval(agent, record)
elif args.stage == "reranker":
    row = evaluate_record_reranker(agent, record)
elif args.stage in ("final", "all"):
    row = evaluate_record_final(agent, record)
else:
    raise NotImplementedError(f"stage={args.stage}")
```

- [ ] **Step 3: Smoke-test на 2 кейсах**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 2 --stage final
```
Expected: вывод с final_top1, summary с top1_accuracy_pct и failure_breakdown

- [ ] **Step 4: Commit**

```bash
git add tests/eval_hierarchical.py
git commit -m "feat(eval): implement --stage final with LLM full pipeline"
```

---

## Task 10: Per-case JSONL writer

**Files:**
- Modify: `tests/eval_hierarchical.py`

- [ ] **Step 1: Добавить `write_per_case_log()`**

Добавить ниже helper-функций:

```python
def write_per_case_log(rows: list[dict[str, Any]], stage: str, tag: str) -> Path:
    """Write per-case rows as JSONL to eval_results/."""
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = EVAL_RESULTS_DIR / f"{timestamp}_{tag}_{stage}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out_path
```

- [ ] **Step 2: Вызвать из `main()`**

После цикла, перед `compute_per_stage_metrics`, добавить:

```python
    per_case_path = write_per_case_log(rows, args.stage, args.tag)
    print(f"[eval_hierarchical] per-case log: {per_case_path}")
```

- [ ] **Step 3: Smoke-test и проверить файл**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 2 --stage retrieval --tag smoke
ls -la eval_results/
head -1 eval_results/*smoke*.jsonl
```
Expected: файл создан, JSON валидный

- [ ] **Step 4: Commit**

```bash
git add tests/eval_hierarchical.py
git commit -m "feat(eval): write per-case JSONL log to eval_results/"
```

---

## Task 11: Markdown summary report

**Files:**
- Modify: `tests/eval_hierarchical.py`

- [ ] **Step 1: Добавить `write_summary_md()`**

Добавить ниже `write_per_case_log()`:

```python
def write_summary_md(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    stage: str,
    tag: str,
    dataset_path: Path,
) -> Path:
    """Write human-readable markdown summary to eval_results/."""
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = EVAL_RESULTS_DIR / f"{timestamp}_{tag}_{stage}_summary.md"

    lines = [
        f"# Eval Hierarchical — {tag} — {stage}",
        "",
        f"**Date:** {datetime.now().isoformat()}",
        f"**Dataset:** `{dataset_path}` ({summary['total']} records)",
        f"**Stage:** `{stage}`",
        "",
        "## Summary metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    if "top1_accuracy_pct" in summary:
        lines.append(f"| Top-1 accuracy | {summary['correct']}/{summary['evaluable']} = {summary['top1_accuracy_pct']}% |")
    for key in ["dense_recall_at_10_pct", "dense_recall_at_50_pct", "reranked_recall_at_10_pct"]:
        if key in summary:
            lines.append(f"| {key.replace('_pct', '').replace('_', ' ')} | {summary[key]}% |")
    lines.append("")

    if summary.get("failure_breakdown"):
        lines.extend([
            "## Failure attribution",
            "",
            "| Category | Count | % of evaluable |",
            "|---|---:|---:|",
        ])
        evaluable = summary["evaluable"] or 1
        for category, count in sorted(summary["failure_breakdown"].items(), key=lambda kv: -kv[1]):
            pct = round(100.0 * count / evaluable, 1)
            lines.append(f"| `{category}` | {count} | {pct}% |")
        lines.append("")

    lines.extend([
        "## Per-case details",
        "",
        "| ID | Gold | Dense rank | Reranked rank | Final top-1 | Attribution |",
        "|---|---|---:|---:|---|---|",
    ])
    for row in rows:
        lines.append(
            f"| `{row['case_id']}` | `{row['gold_code']}` | "
            f"{row.get('dense_rank') or '−'} | "
            f"{row.get('reranked_rank') or '−'} | "
            f"`{row.get('final_top1_code') or '−'}` | "
            f"`{row.get('failure_attribution', '−')}` |"
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
```

- [ ] **Step 2: Вызвать из `main()`**

После `print(json.dumps(summary, ...))`:

```python
    md_path = write_summary_md(rows, summary, args.stage, args.tag, args.dataset)
    print(f"[eval_hierarchical] summary md: {md_path}")
```

- [ ] **Step 3: Smoke-test и проверить markdown**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 3 --stage reranker --tag smoke
cat eval_results/*smoke_reranker_summary.md
```
Expected: валидный markdown с таблицами

- [ ] **Step 4: Commit**

```bash
git add tests/eval_hierarchical.py
git commit -m "feat(eval): write markdown summary report to eval_results/"
```

---

## Task 12: Baseline snapshot saver

**Files:**
- Modify: `tests/eval_hierarchical.py`

- [ ] **Step 1: Добавить `save_baseline()`**

Добавить ниже `write_summary_md()`:

```python
def save_baseline(summary: dict[str, Any], stage: str, tag: str, dataset_path: Path) -> Path:
    """Save baseline snapshot (tracked in git) for future diff comparisons."""
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = BASELINES_DIR / f"{date_str}_{tag}_{stage}.json"
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "dataset": str(dataset_path.relative_to(REPO_ROOT)),
        "stage": stage,
        "tag": tag,
        "summary": summary,
    }
    out_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
```

- [ ] **Step 2: Вызвать из `main()` если `--save-baseline`**

После `write_summary_md(...)`:

```python
    if args.save_baseline:
        baseline_path = save_baseline(summary, args.stage, args.tag, args.dataset)
        print(f"[eval_hierarchical] baseline saved: {baseline_path}")
```

- [ ] **Step 3: Smoke-test**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 3 --stage retrieval --tag smoke --save-baseline
ls data/eval_baselines/
cat data/eval_baselines/*smoke*.json
```
Expected: baseline file создан с валидным JSON

- [ ] **Step 4: Commit**

```bash
git add tests/eval_hierarchical.py
git commit -m "feat(eval): save baseline snapshots to data/eval_baselines/"
```

---

## Task 13: Финальный run — baseline на ii25_test (full pipeline)

**Files:**
- Create: `eval_results/<timestamp>_baseline_all_summary.md`
- Create: `data/eval_baselines/2026-05-27_baseline_all.json`

- [ ] **Step 1: Прогнать full pipeline на всём ii25_test**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage all --tag baseline --save-baseline
```
Expected:
- Время: ~5-10 минут (56 кейсов × ~5-10 сек на LLM call)
- Output файлы: per-case JSONL + summary MD + baseline JSON

- [ ] **Step 2: Проверить, что baseline-метрики разумные**

Открыть свежий `eval_results/*baseline_all_summary.md` и убедиться:
- `Top-1 accuracy` в диапазоне 20-30% (ожидаемое ~25% по HANDOFF.md)
- `dense_recall_at_50_pct` ~70% (ожидаемое 69.6%)
- `reranked_recall_at_10_pct` ~12-15%
- `failure_breakdown` показывает доминирующую категорию (вероятно `retrieval_recall` или `reranker`)

Если метрики radикально отличаются — debug перед коммитом.

- [ ] **Step 3: Commit baseline snapshot**

```bash
git add data/eval_baselines/2026-05-27_baseline_all.json eval_results/.gitkeep
git commit -m "eval: baseline snapshot 2026-05-27 (full pipeline on ii25_test)"
```

Файлы в `eval_results/` НЕ коммитим (они в .gitignore), только baseline в `data/eval_baselines/`.

---

## Task 14: Smoke-eval всех 3 не-LLM режимов (быстрый sanity)

**Files:**
- Read: `eval_results/`

- [ ] **Step 1: Прогнать retrieval-only baseline (быстро)**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage retrieval --tag baseline --save-baseline
```
Expected: ~30 сек, dense_recall_at_50 ~70%

- [ ] **Step 2: Прогнать reranker baseline**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage reranker --tag baseline --save-baseline
```
Expected: ~1-2 мин, reranked_recall_at_10 ~12-15%

- [ ] **Step 3: Commit все 3 baseline JSON**

```bash
git add data/eval_baselines/2026-05-27_baseline_retrieval.json data/eval_baselines/2026-05-27_baseline_reranker.json
git commit -m "eval: baseline snapshots — retrieval and reranker stages"
```

---

## Task 15: Обновить HANDOFF.md с usage инструкциями

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Добавить секцию про eval framework**

Добавить в `HANDOFF.md` (после блока «Запуск», перед «Ключевые файлы»):

```markdown
## Eval Framework (Phase 0 — 2026-05-27)

Новый orchestrator `tests/eval_hierarchical.py` для per-stage метрик с failure attribution.

### Запуск

```bash
# Retrieval only (быстро, ~30 сек)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage retrieval

# Reranker (без LLM, ~1-2 мин)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage reranker

# Полный pipeline с LLM (~5-10 мин)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage all

# Сохранить baseline snapshot (для diff'ов)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage all --tag v1.1-router --save-baseline
```

### Output

- `eval_results/<timestamp>_<tag>_<stage>.jsonl` — per-case детали (gitignored)
- `eval_results/<timestamp>_<tag>_<stage>_summary.md` — markdown отчёт (gitignored)
- `data/eval_baselines/<date>_<tag>_<stage>.json` — baseline snapshot (в git)

### Failure attribution categories (Phase 0)

- `correct` — Top-1 ok
- `llm_final` — gold в reranked top-10, но LLM выбрала другой код
- `reranker` — gold в dense top-50, но reranker уронил его из top-10
- `retrieval_recall` — gold вообще не в dense top-50

В Phase 1+ добавятся `router_stage1`, `router_stage2`.

### Baseline (2026-05-27)

См. `data/eval_baselines/2026-05-27_baseline_*.json`. Любая последующая фаза должна сравниваться с этим snapshot'ом.
```

- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md
git commit -m "docs: add eval framework usage to HANDOFF.md"
```

---

## Acceptance Criteria

Phase 0 считается завершённым, когда:

- [ ] Все 5 тестов `attribute_failure` и 4 теста `compute_per_stage_metrics` проходят
- [ ] `tests/eval_hierarchical.py --stage retrieval --limit 1` работает без ошибок
- [ ] `tests/eval_hierarchical.py --stage reranker --limit 1` работает
- [ ] `tests/eval_hierarchical.py --stage all --limit 3` работает (включая LLM)
- [ ] Baseline snapshot создан в `data/eval_baselines/2026-05-27_baseline_all.json`
- [ ] Все 3 baseline snapshots (retrieval, reranker, all) в git
- [ ] `HANDOFF.md` обновлён с usage инструкциями
- [ ] Top-1 accuracy в baseline в диапазоне 20-30% (sanity)

---

## Что НЕ входит в Phase 0 (для будущих фаз)

- Router stage attribution (`router_stage1`, `router_stage2`) — Phase 1
- Diff vs previous baseline — добавим после первого изменения метрик (Phase 1+)
- Per-L1/L2/L3 prefix accuracy в summary — добавим в Phase 1 при наличии router'а
- Latency p50/p95 — пока не приоритет
- Token usage tracking — нет

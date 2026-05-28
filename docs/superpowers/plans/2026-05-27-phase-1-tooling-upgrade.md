# Phase 1 — Tooling Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять качество retrieval/reranker/structured-output без архитектурных изменений — точечный апгрейд 3 компонентов до SOTA 2024-2026 (BGE-M3 + BGE-Reranker-v2-m3 + native vLLM JSON schema) и замер на ii25_test → Checkpoint 1 decision.

**Architecture:** Прозрачные swap'ы (e5-base → BGE-M3, mmarco-CE → BGE-Reranker-v2-m3) через ENV-переменные. Constrained decoding через native `response_format={"type": "json_schema"}` в Ario API (vLLM-backend поддерживает с 0.8.5). JSON schemas вынесены в новый `src/llm_schemas.py`. Smoke-test после каждого компонента; полный baseline replay в конце.

**Tech Stack:** Python 3.11, sentence-transformers (BGE-M3 / BGE-Reranker-v2-m3), httpx (Ario API), vLLM-compatible `response_format`, существующий `tests/eval_hierarchical.py`.

**Связь со spec:** реализует [Section 13 (Phase 1 Tooling Upgrade)](../specs/2026-05-27-hierarchical-classification-design.md#13-phase-1--tooling-upgrade-детали) и [Checkpoint 1 decision logic](../specs/2026-05-27-hierarchical-classification-design.md#9-rollout-plan-v2--после-sota-tooling-research).

---

## Pre-flight

Phase 0 (eval framework) уже завершена и зафиксирована в ветке `phase-0-eval-framework`. Phase 1 идёт в **отдельной ветке от main** (не от phase-0), потому что мы хотим иметь возможность сравнивать с Phase 0 baseline и при необходимости откатиться.

Baselines из Phase 0 (cohabit в `data/eval_baselines/`):
- `2026-05-27_baseline_retrieval.json` — dense_recall@10=41.1%, recall@50=69.6%
- `2026-05-27_baseline_reranker.json` — reranked_recall@10=12.5%
- `2026-05-27_baseline_all.json` — Top-1=7.1%

---

## File Structure

| Файл | Назначение | Создаём/Меняем |
|---|---|---|
| `.env` | EMBEDDING_MODEL, CROSS_ENCODER_MODEL, VECTOR_DB_DIR | Меняем |
| `src/config.py` | Возможно обновить дефолты (минимально) | Возможно меняем |
| `data/vector_db_bge_m3/` | Новая DB после BGE-M3 пересборки | **Создаём** |
| `src/llm_schemas.py` | 4 JSON-схемы для LLM-вызовов | **Создаём** |
| `tests/test_llm_schemas.py` | Тесты, что схемы валидны и пример проходит | **Создаём** |
| `src/classifier_agent.py` | Добавить `response_format={"type": "json_schema", ...}` в Ario httpx-вызовы (lines ~1054-1084, ~1300-1360), убрать ручной wrapper-strip | Меняем |
| `scripts/smoke_bge_m3.py` | Smoke-test BGE-M3 loading + embedding | **Создаём** |
| `scripts/smoke_bge_reranker.py` | Smoke-test BGE-Reranker-v2-m3 | **Создаём** |
| `scripts/smoke_constrained_decoding.py` | Smoke-test response_format на Ario | **Создаём** |
| `docs/checkpoint1_decision.md` | Решение по дальнейшим фазам | **Создаём** в Task 10 |

**Принцип:** smoke-скрипты — короткие (10-30 строк), их задача показать что компонент работает, а не покрыть тестами. Эти скрипты остаются в репо для будущего debugging.

---

## Task 1: Создать feature branch `phase-1-tooling-upgrade` от main

**Files:** git operations only

- [ ] **Step 1: Убедиться что на main**

```bash
git checkout main
git status --short
```

Expected: на `main`, можно иметь незакоммиченные изменения в data/* — они не блокируют.

- [ ] **Step 2: Создать feature branch**

```bash
git checkout -b phase-1-tooling-upgrade
git status --short
```

Expected: switched to new branch `phase-1-tooling-upgrade`.

- [ ] **Step 3: Verify Phase 0 baseline files доступны**

```bash
ls data/eval_baselines/
```

Expected: видны 3 файла `2026-05-27_baseline_{retrieval,reranker,all}.json`. Если нет — нужно сначала merge'нуть phase-0-eval-framework в main или процитировать его commit.

Если файлов нет — STOP и спроси.

---

## Task 2: Smoke-test BGE-M3 модель (проверить, что грузится на CPU)

**Files:**
- Create: `scripts/smoke_bge_m3.py`

- [ ] **Step 1: Создать smoke-скрипт**

Создать `scripts/smoke_bge_m3.py`:

```python
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
```

- [ ] **Step 2: Запустить (первый раз — скачивание ~2.4GB, может занять 5-15 мин)**

```bash
./venv/Scripts/python.exe scripts/smoke_bge_m3.py
```

Expected:
- Скачивание модели (один раз)
- Print "OK: BGE-M3 loads, produces 1024-dim embeddings"
- Время encoding 2 текстов на CPU: < 5 сек

**Critical:** BGE-M3 имеет dim=1024 (vs e5-base dim=768). Это значит vector DB пересобирать обязательно — старые .npy несовместимы.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_bge_m3.py
git commit -m "feat(tooling): add smoke-test script for BGE-M3 embedding model"
```

---

## Task 3: Пересобрать vector_db с BGE-M3

**Files:**
- Modify: `.env` (через ENV override, временно)
- Use: `src/build_vectordb.py` (без изменений — он читает EMBEDDING_MODEL из config)
- Output: `data/vector_db_bge_m3/embeddings.npy` + `metadata.json`

- [ ] **Step 1: Проверить .env file существует**

```bash
ls .env 2>&1 && head -20 .env 2>&1 || echo "NO .env"
```

Expected: видим текущий .env с `EMBEDDING_MODEL=...` и `VECTOR_DB_DIR=...`

- [ ] **Step 2: Бэкапнуть текущий .env**

```bash
cp .env .env.backup.phase0
```

- [ ] **Step 3: Подменить EMBEDDING_MODEL и VECTOR_DB_DIR в .env**

Открыть `.env` и:
1. Найти строку `EMBEDDING_MODEL=...` (или добавить если нет) — заменить на:
   ```
   EMBEDDING_MODEL=BAAI/bge-m3
   ```
2. Найти `VECTOR_DB_DIR=...` — заменить на:
   ```
   VECTOR_DB_DIR=data/vector_db_bge_m3
   ```
3. Найти `ENABLE_EMBEDDING_ADAPTER=...` — выставить:
   ```
   ENABLE_EMBEDDING_ADAPTER=false
   ```
   (BGE-M3 уже сильна, адаптер v3 несовместим — он обучен на e5-base 768d)

- [ ] **Step 4: Запустить build_vectordb.py**

```bash
./venv/Scripts/python.exe src/build_vectordb.py
```

Expected:
- Печатает "Загрузка модели эмбеддингов: BAAI/bge-m3..."
- Векторизация 2108 записей, батч 128 — ~10-30 минут на CPU
- Сохраняет `data/vector_db_bge_m3/embeddings.npy` (shape: 2108 × 1024)
- Сохраняет `data/vector_db_bge_m3/metadata.json`

Если падает с OOM — уменьшить BATCH_SIZE в `src/build_vectordb.py` с 128 до 32 (одна правка строки 24).

- [ ] **Step 5: Verify shape новой DB**

```bash
./venv/Scripts/python.exe -c "
import numpy as np
embs = np.load('data/vector_db_bge_m3/embeddings.npy')
print(f'Shape: {embs.shape}')
print(f'Dtype: {embs.dtype}')
print(f'Normalized: {np.abs(np.linalg.norm(embs[0]) - 1.0) < 0.01}')
assert embs.shape == (2108, 1024), f'Expected (2108, 1024), got {embs.shape}'
print('OK')
"
```

Expected: shape (2108, 1024), normalized True, OK.

- [ ] **Step 6: Commit (только .env-пример и metadata, не .env и не .npy)**

Сначала проверь, что в .gitignore:
```bash
grep -E "vector_db|\.env$" .gitignore
```

Если `data/vector_db_*` не игнорится — добавить в .gitignore:
```
data/vector_db_bge_m3/
```

Затем commit:
```bash
git add .gitignore
git commit -m "chore: gitignore vector_db_bge_m3 (rebuilt locally)"
```

---

## Task 4: Smoke retrieval с новой DB → сравнить с Phase 0

**Files:**
- Use: `tests/eval_hierarchical.py` (без изменений)
- Output: `eval_results/<ts>_v0.4.bge-m3_retrieval.*` + `data/eval_baselines/2026-05-27_v0.4.bge-m3_retrieval.json`

- [ ] **Step 1: Прогнать --stage retrieval на ii25_test**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage retrieval --tag v0.4.bge-m3 --save-baseline
```

Expected:
- 56 кейсов обработано
- SUMMARY с `dense_recall_at_10_pct` и `dense_recall_at_50_pct`
- Файл `data/eval_baselines/2026-05-27_v0.4.bge-m3_retrieval.json` создан

- [ ] **Step 2: Diff vs Phase 0 baseline**

```bash
./venv/Scripts/python.exe -c "
import json
old = json.load(open('data/eval_baselines/2026-05-27_baseline_retrieval.json'))
new = json.load(open('data/eval_baselines/2026-05-27_v0.4.bge-m3_retrieval.json'))

print('Phase 0 (e5-base):     ', json.dumps(old['summary'], indent=2, ensure_ascii=False))
print()
print('Phase 1 (BGE-M3):      ', json.dumps(new['summary'], indent=2, ensure_ascii=False))
print()
for k in ['dense_recall_at_10_pct', 'dense_recall_at_50_pct']:
    if k in new['summary'] and k in old['summary']:
        delta = new['summary'][k] - old['summary'][k]
        sign = '+' if delta >= 0 else ''
        print(f'  {k}: {old[\"summary\"][k]}% -> {new[\"summary\"][k]}% ({sign}{delta:.1f}pp)')
"
```

Expected: видны абсолютные цифры и delta. Ожидаемая delta: +5-10pp на recall@10, +10-15pp на recall@50.

**Если delta < +2pp** (или хуже Phase 0) — STOP, переходим в DONE_WITH_CONCERNS:
- Возможные причины: BGE-M3 без правильного prefix encoding ("passage:" / "query:"), problem с normalize, что-то ещё
- Проверить, что `build_vectordb.py` использует `"passage: "` prefix (line 61), а eval использует `"query: "` (через ClassifierAgent → SentenceTransformer.encode по запросу)
- При проблеме — починить в Task 4.5 (не запланирована, но добавится при необходимости)

- [ ] **Step 3: Commit baseline**

```bash
git add data/eval_baselines/2026-05-27_v0.4.bge-m3_retrieval.json
git commit -m "eval: BGE-M3 retrieval baseline on ii25_test"
```

---

## Task 5: Smoke-test BGE-Reranker-v2-m3 модели

**Files:**
- Create: `scripts/smoke_bge_reranker.py`

- [ ] **Step 1: Создать smoke-скрипт**

Создать `scripts/smoke_bge_reranker.py`:

```python
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
```

- [ ] **Step 2: Запустить (первый раз качает ~2GB)**

```bash
./venv/Scripts/python.exe scripts/smoke_bge_reranker.py
```

Expected: `OK: BGE-Reranker-v2-m3 loads, ranks plausibly.`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_bge_reranker.py
git commit -m "feat(tooling): add smoke-test script for BGE-Reranker-v2-m3"
```

---

## Task 6: Swap CE model в .env и реранкер baseline

**Files:**
- Modify: `.env` (CROSS_ENCODER_MODEL + ENABLE_CROSS_ENCODER_RERANKER)

- [ ] **Step 1: Обновить .env**

В `.env`:
1. Найти `CROSS_ENCODER_MODEL=...` (или добавить) — заменить на:
   ```
   CROSS_ENCODER_MODEL=BAAI/bge-reranker-v2-m3
   ```
2. Найти `ENABLE_CROSS_ENCODER_RERANKER=...` — выставить:
   ```
   ENABLE_CROSS_ENCODER_RERANKER=true
   ```

- [ ] **Step 2: Запустить --stage reranker baseline на ii25_test**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage reranker --tag v0.4.bge-rerank --save-baseline
```

Expected: 56 кейсов, новый `reranked_recall_at_10_pct` в SUMMARY.

- [ ] **Step 3: Diff vs Phase 0 reranker baseline**

```bash
./venv/Scripts/python.exe -c "
import json
old = json.load(open('data/eval_baselines/2026-05-27_baseline_reranker.json'))
new = json.load(open('data/eval_baselines/2026-05-27_v0.4.bge-rerank_reranker.json'))

for k in ['dense_recall_at_10_pct', 'dense_recall_at_50_pct', 'reranked_recall_at_10_pct']:
    if k in new['summary'] and k in old['summary']:
        delta = new['summary'][k] - old['summary'][k]
        sign = '+' if delta >= 0 else ''
        print(f'{k}: {old[\"summary\"][k]}% -> {new[\"summary\"][k]}% ({sign}{delta:.1f}pp)')
"
```

Expected: `reranked_recall_at_10_pct` должен значимо вырасти (+15-30pp ожидается). Dense метрики могут немного измениться из-за BGE-M3 (это OK).

**Если reranker_recall@10 не улучшилось ≥ +10pp** — STOP, проверить:
1. Reranker реально подключился? (см. логи stdout — должно быть "Загрузка CrossEncoder: BAAI/bge-reranker-v2-m3")
2. Max_length достаточен? (BGE-Reranker-v2-m3 поддерживает 8192 токенов, но в config.py может быть жёстко 512)

- [ ] **Step 4: Commit baseline**

```bash
git add data/eval_baselines/2026-05-27_v0.4.bge-rerank_reranker.json
git commit -m "eval: BGE-Reranker-v2-m3 reranker baseline on ii25_test"
```

---

## Task 7: Создать JSON schemas для constrained decoding

**Files:**
- Create: `src/llm_schemas.py`
- Create: `tests/test_llm_schemas.py`

- [ ] **Step 1: Создать тесты (TDD)**

Создать `tests/test_llm_schemas.py`:

```python
"""Tests for src/llm_schemas.py — validate schema correctness."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json
import jsonschema  # may need: pip install jsonschema (already installed via pytest deps usually)
import pytest

from llm_schemas import (
    ROUTER_L1_SCHEMA,
    ROUTER_L2_SCHEMA,
    FINAL_PICK_SCHEMA,
    PTO_CLEANUP_SCHEMA,
)


def test_router_l1_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(ROUTER_L1_SCHEMA)


def test_router_l1_accepts_valid_response():
    valid = {
        "l1_ranked": [
            {"code": "0003", "conf": 0.7},
            {"code": "0005", "conf": 0.2},
        ],
        "reasoning": "обращение про благоустройство",
    }
    jsonschema.validate(valid, ROUTER_L1_SCHEMA)


def test_router_l1_rejects_missing_required_field():
    invalid = {"reasoning": "test"}  # missing l1_ranked
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, ROUTER_L1_SCHEMA)


def test_router_l2_accepts_valid_response():
    valid = {
        "l2_codes": ["0096", "0097"],
        "reasoning": "новостройка и благоустройство",
    }
    jsonschema.validate(valid, ROUTER_L2_SCHEMA)


def test_final_pick_accepts_valid_response():
    valid = {
        "code": "0003.0009.0097.0689",
        "confidence": 0.85,
        "reasoning": "точно благоустройство дворов",
    }
    jsonschema.validate(valid, FINAL_PICK_SCHEMA)


def test_final_pick_rejects_invalid_code_format():
    invalid = {
        "code": "not-a-code",
        "confidence": 0.85,
        "reasoning": "test",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, FINAL_PICK_SCHEMA)


def test_pto_cleanup_accepts_valid_response():
    valid = {
        "primary_code": "0001.0002.0027.0151",
        "primary_confidence": 0.85,
        "secondary_codes": [
            {"code": "0001.0001.0011.0038", "confidence": 0.6},
        ],
        "rejected_assigned": True,
        "reasoning": "обращение про работу госорганов",
    }
    jsonschema.validate(valid, PTO_CLEANUP_SCHEMA)


def test_pto_cleanup_allows_empty_secondary():
    valid = {
        "primary_code": "0001.0002.0027.0151",
        "primary_confidence": 0.85,
        "secondary_codes": [],
        "rejected_assigned": False,
        "reasoning": "точная категория",
    }
    jsonschema.validate(valid, PTO_CLEANUP_SCHEMA)
```

- [ ] **Step 2: Запустить тесты — должны fail с ImportError**

```bash
./venv/Scripts/python.exe -m pytest tests/test_llm_schemas.py -v
```

Expected: 8 failures с `ImportError: cannot import name 'ROUTER_L1_SCHEMA' from 'llm_schemas'`.

- [ ] **Step 3: Создать `src/llm_schemas.py`**

```python
"""JSON schemas for constrained decoding via vLLM response_format.

Use these schemas with Ario/vLLM API:
    response_format = {"type": "json_schema", "json_schema": {"name": "...", "schema": SCHEMA, "strict": True}}

Schemas guarantee LLM output structure — no parsing of malformed JSON needed.
"""

from __future__ import annotations

# Code pattern: 4 dot-separated 4-digit groups (e.g., "0003.0009.0097.0689")
# Optional 5th group for L5 codes
CODE_PATTERN = r"^[0-9]{4}(\.[0-9]{4}){3,4}$"

# L1 code: 4-digit single group (e.g., "0003")
L1_CODE_PATTERN = r"^[0-9]{4}$"

# L2 code: 4-digit group OR full 0001.0001 form
L2_CODE_PATTERN = r"^[0-9]{4}(\.[0-9]{4})?$"


ROUTER_L1_SCHEMA = {
    "type": "object",
    "properties": {
        "l1_ranked": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "pattern": L1_CODE_PATTERN},
                    "conf": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["code", "conf"],
                "additionalProperties": False,
            },
        },
        "reasoning": {"type": "string", "maxLength": 500},
    },
    "required": ["l1_ranked"],
    "additionalProperties": False,
}


ROUTER_L2_SCHEMA = {
    "type": "object",
    "properties": {
        "l2_codes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {"type": "string", "pattern": L2_CODE_PATTERN},
        },
        "reasoning": {"type": "string", "maxLength": 500},
    },
    "required": ["l2_codes"],
    "additionalProperties": False,
}


FINAL_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "pattern": CODE_PATTERN},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "maxLength": 1000},
    },
    "required": ["code", "confidence"],
    "additionalProperties": False,
}


PTO_CLEANUP_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_code": {"type": "string", "pattern": CODE_PATTERN},
        "primary_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "secondary_codes": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "pattern": CODE_PATTERN},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["code", "confidence"],
                "additionalProperties": False,
            },
        },
        "rejected_assigned": {"type": "boolean"},
        "reasoning": {"type": "string", "maxLength": 1000},
    },
    "required": ["primary_code", "primary_confidence", "secondary_codes", "rejected_assigned"],
    "additionalProperties": False,
}
```

- [ ] **Step 4: Запустить тесты — должны пройти**

```bash
./venv/Scripts/python.exe -m pytest tests/test_llm_schemas.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Verify jsonschema installed**

```bash
./venv/Scripts/python.exe -c "import jsonschema; print(jsonschema.__version__)"
```

Если ImportError — установить: `./venv/Scripts/pip.exe install jsonschema`, добавить в `requirements.txt`:
```
jsonschema>=4.0
```

- [ ] **Step 6: Commit**

```bash
git add src/llm_schemas.py tests/test_llm_schemas.py
# Если изменили requirements.txt:
git add requirements.txt
git commit -m "feat(llm): add JSON schemas for constrained decoding"
```

---

## Task 8: Smoke-test constrained decoding на Ario

**Files:**
- Create: `scripts/smoke_constrained_decoding.py`

- [ ] **Step 1: Создать smoke-скрипт**

Создать `scripts/smoke_constrained_decoding.py`:

```python
"""Smoke-test constrained decoding via Ario response_format=json_schema.

Tests two scenarios:
1. Simple FINAL_PICK schema — should return valid JSON matching schema
2. Same call WITHOUT response_format (control) — for comparison
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import jsonschema

from config import ARIO_API_KEY, ARIO_BASE_URL, ARIO_MODEL
from llm_schemas import FINAL_PICK_SCHEMA


SYSTEM = """Ты классификатор обращений граждан. По тексту обращения выбери ОДИН код из 4-уровневого классификатора (формат XXXX.XXXX.XXXX.XXXX). Верни только JSON."""

USER = """Текст обращения: Жалоба на бездействие управляющей компании по уборке двора.

Кандидаты:
- 0005.0005.0056.1162 — Управление многоквартирными домами / Бездействие УК
- 0005.0005.0055.1130 — Жилищный фонд / Капитальный ремонт
- 0002.0007.0074.0300 — Льготы инвалидам

Выбери лучший код."""


def call_ario_with_schema(schema):
    """Call Ario with response_format=json_schema."""
    client = httpx.Client(
        base_url=ARIO_BASE_URL,
        headers={"Authorization": f"Bearer {ARIO_API_KEY}"},
        timeout=60,
    )
    try:
        r = client.post(
            "/chat/completions",
            json={
                "model": ARIO_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": USER},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "final_pick",
                        "schema": schema,
                        "strict": True,
                    },
                },
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    finally:
        client.close()


def call_ario_plain():
    """Call Ario without response_format (control)."""
    client = httpx.Client(
        base_url=ARIO_BASE_URL,
        headers={"Authorization": f"Bearer {ARIO_API_KEY}"},
        timeout=60,
    )
    try:
        r = client.post(
            "/chat/completions",
            json={
                "model": ARIO_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM + " Формат: {\"code\":\"...\",\"confidence\":0.9,\"reasoning\":\"...\"}"},
                    {"role": "user", "content": USER},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    finally:
        client.close()


print("=" * 60)
print("Test 1: WITH response_format=json_schema")
print("=" * 60)
try:
    raw = call_ario_with_schema(FINAL_PICK_SCHEMA)
    print(f"Raw response:\n{raw}\n")
    parsed = json.loads(raw)
    jsonschema.validate(parsed, FINAL_PICK_SCHEMA)
    print(f"VALID JSON matching schema. Code: {parsed['code']}, conf: {parsed['confidence']}")
    success_with_schema = True
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    success_with_schema = False

print("\n" + "=" * 60)
print("Test 2: WITHOUT response_format (control)")
print("=" * 60)
try:
    raw = call_ario_plain()
    print(f"Raw response:\n{raw}\n")
    # Strip wrappers
    for prefix in ("```json", "```"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    parsed = json.loads(raw)
    try:
        jsonschema.validate(parsed, FINAL_PICK_SCHEMA)
        print(f"Coincidentally valid. Code: {parsed.get('code')}")
        plain_valid = True
    except jsonschema.ValidationError as e:
        print(f"Plain mode parsed but doesn't match schema strictly: {e.message}")
        plain_valid = False
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    plain_valid = False

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"With response_format: {'OK' if success_with_schema else 'FAIL'}")
print(f"Without response_format: {'lucky' if plain_valid else 'invalid'}")

if not success_with_schema:
    print("\nNOTE: vLLM/Qwen3 may have a known bug (#18819). Try setting enable_thinking=True or use fallback grammar-in-prompt.")
    sys.exit(1)
```

- [ ] **Step 2: Запустить (1 LLM-вызов с Ario, ~5-15 сек)**

```bash
./venv/Scripts/python.exe scripts/smoke_constrained_decoding.py
```

Expected:
- Test 1: print "VALID JSON matching schema" — schema enforced
- Test 2: либо "lucky" если повезло, либо warning что schema не match

**Если Test 1 fails (Qwen3 bug):**
- Не блокирующий — переходим к Task 9 в fallback mode
- В Task 9 либо используем response_format опционально, либо чистим JSON wrappers как сейчас
- Записать в `docs/checkpoint1_decision.md` (в Task 10) что constrained decoding не работает

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_constrained_decoding.py
git commit -m "feat(tooling): add smoke-test for vLLM response_format=json_schema on Ario"
```

---

## Task 9: Интегрировать response_format в Ario httpx-вызовы classifier_agent.py

**Files:**
- Modify: `src/classifier_agent.py` (4 места: query_expansion ~lines 1054-1084, plus 3 other Ario httpx calls — мы их найдём через Grep)

**Подход:** Создать helper-функцию `_ario_call(messages, schema=None)` и заменить inline httpx-вызовы. Если Task 8 показал, что constrained decoding не работает (Qwen3 bug), `schema=None` сохраняет старое поведение (без response_format, с wrapper-stripping).

- [ ] **Step 1: Найти все Ario httpx call sites**

```bash
grep -n "ARIO_BASE_URL" src/classifier_agent.py
```

Expected: ~4 строки (`base_url=ARIO_BASE_URL`).

- [ ] **Step 2: Прочитать контекст вокруг каждой строки**

```bash
for line in $(grep -n "ARIO_BASE_URL" src/classifier_agent.py | cut -d: -f1); do
  echo "=== Line $line ==="
  sed -n "$((line-5)),$((line+30))p" src/classifier_agent.py
done | head -200
```

(Дальше — ручная адаптация. Структура каждого вызова похожа: httpx.Client → post → r.json()["choices"][0]["message"]["content"].)

- [ ] **Step 3: Добавить helper `_ario_call` в classifier_agent.py**

Найти класс `ClassifierAgent` и добавить метод (можно рядом с `_resolve_llm` или в конце класса):

```python
    def _ario_call(
        self,
        *,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.0,
        schema: dict | None = None,
    ) -> str:
        """Centralized Ario httpx call.

        If schema is provided, uses native vLLM response_format=json_schema (constrained
        decoding). Otherwise calls plain and the caller is responsible for parsing/stripping
        ```json wrappers from the raw response.

        Returns raw string content from choices[0].message.content.
        """
        import httpx
        from config import ARIO_API_KEY, ARIO_BASE_URL, ARIO_MODEL

        payload: dict = {
            "model": model or ARIO_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "constrained_output", "schema": schema, "strict": True},
            }

        client = httpx.Client(
            base_url=ARIO_BASE_URL,
            headers={"Authorization": f"Bearer {ARIO_API_KEY}"},
            timeout=60,
        )
        try:
            r = client.post("/chat/completions", json=payload)
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip()
            # Strip ```json wrappers if NOT constrained (safety net for plain mode)
            if schema is None:
                for prefix in ("```json", "```"):
                    if raw.startswith(prefix):
                        raw = raw[len(prefix):].strip()
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            return raw
        finally:
            client.close()
```

- [ ] **Step 4: Заменить inline Ario httpx-вызовы на `_ario_call`**

Для каждого `ARIO_BASE_URL` site:

**Site 1: Query Expansion (~line 1054-1084).** Заменить блок целиком:

ДО (примерно):
```python
elif provider == "ario":
    import httpx
    client = httpx.Client(
        base_url=ARIO_BASE_URL,
        headers={"Authorization": f"Bearer {ARIO_API_KEY}"},
        timeout=60,
    )
    try:
        r = client.post("/chat/completions", json={...})
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        for prefix in ("```json", "```"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        return raw
    finally:
        client.close()
```

ПОСЛЕ:
```python
elif provider == "ario":
    return self._ario_call(
        messages=[
            {"role": "system", "content": QUERY_EXPANSION_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        max_tokens=120,
        schema=None,  # query expansion outputs prose, not JSON
    )
```

**Site 2: Multi-Query Expansion (~line 870-920).** Аналогично — для multi-query expansion. Если выход JSON-like — указать schema (но в текущем коде, скорее всего, это тоже prose).

**Sites 3, 4: Section Routing / final classification.** Эти уже используют JSON output. Заменить так же, но с `schema=ROUTER_L2_SCHEMA` или `FINAL_PICK_SCHEMA` (нужно подобрать по контексту).

**ВАЖНО:** если в Task 8 constrained decoding работает (Test 1 OK) — передаём `schema=...`. Если не работает (Qwen3 bug) — оставляем `schema=None` везде, и старое поведение сохранится.

Из-за объёма ручных правок (4 sites × ~30 строк) — это самый дорогой step. Заложить ~30 мин на аккуратные правки.

- [ ] **Step 5: Smoke test classifier на 2 кейсах**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --limit 2 --stage final --tag smoke
```

Expected: 2 кейса проходят, нет exception'ов. Top-1 может быть 0 (мало кейсов) — не критично, важно отсутствие crash'ей.

- [ ] **Step 6: Commit**

```bash
git add src/classifier_agent.py
git commit -m "refactor(llm): centralize Ario httpx calls in _ario_call helper with optional schema"
```

---

## Task 10: Final baseline replay + Checkpoint 1 decision document

**Files:**
- Use: `tests/eval_hierarchical.py` (без изменений)
- Create: `docs/checkpoint1_decision.md`
- Output: `data/eval_baselines/2026-05-27_v0.4.tooling_*.json` (3 файла)

- [ ] **Step 1: Прогнать все 3 baseline stages с тегом v0.4.tooling**

```bash
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage retrieval --tag v0.4.tooling --save-baseline
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage reranker --tag v0.4.tooling --save-baseline
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage all --tag v0.4.tooling --save-baseline
```

Expected: 3 baseline файла в `data/eval_baselines/`. Последний (`all`) занимает ~30-40 мин с LLM.

- [ ] **Step 2: Сформировать diff отчёт**

```bash
./venv/Scripts/python.exe -c "
import json
import os
from pathlib import Path

base_dir = Path('data/eval_baselines')

stages = ['retrieval', 'reranker', 'all']
print('# Phase 0 → Phase 1 metrics diff\n')
print('| Stage | Metric | Phase 0 | Phase 1 | Delta |')
print('|---|---|---:|---:|---:|')

for stage in stages:
    old_path = base_dir / f'2026-05-27_baseline_{stage}.json'
    new_path = base_dir / f'2026-05-27_v0.4.tooling_{stage}.json'
    if not old_path.exists() or not new_path.exists():
        print(f'| {stage} | MISSING FILES | - | - | - |')
        continue
    old = json.load(open(old_path))['summary']
    new = json.load(open(new_path))['summary']
    for metric in ['top1_accuracy_pct', 'dense_recall_at_10_pct', 'dense_recall_at_50_pct', 'reranked_recall_at_10_pct']:
        if metric in old and metric in new:
            delta = new[metric] - old[metric]
            sign = '+' if delta >= 0 else ''
            print(f'| {stage} | {metric} | {old[metric]}% | {new[metric]}% | {sign}{delta:.1f}pp |')
" > /tmp/checkpoint1_diff.md
cat /tmp/checkpoint1_diff.md
```

- [ ] **Step 3: Создать `docs/checkpoint1_decision.md`**

Шаблон (заполни реальными числами из diff):

```markdown
# Checkpoint 1 Decision — 2026-05-27

**Phase 0 baseline:** Top-1 = 7.1% (4/56), dense_recall@50 = 69.6%, reranked_recall@10 = 12.5%
**Phase 1 baseline (после tooling upgrade):** см. `data/eval_baselines/2026-05-27_v0.4.tooling_*.json`

## Diff

<paste markdown table from Step 2 here>

## Failure attribution shift

<сравнить failure_breakdown из Phase 0 baseline и Phase 1 baseline.
Если retrieval_recall сильно упал — BGE-M3 решает retrieval. Если reranker сильно упал — BGE-Reranker-v2-m3 работает.>

## Decision

Согласно [spec § 9 Checkpoint 1](../superpowers/specs/2026-05-27-hierarchical-classification-design.md#checkpoint-1-decision-logic):

- Top-1 после Phase 1 = **<X>%**
- Решающая ветка: **<breakthrough / strong gain / partial gain>**

### Дальнейшие фазы

<заполнить согласно ветке:
- breakthrough (≥35%): Phase 2 (router) + Phase 4 (reranker fix) + Phase 7 (integration). Skip Phase 3/5/6.
- strong gain (20-35%): Все Phases 2-7, но 5/6 conditional.
- partial gain (<20%): Full original spec.>

### Conditional triggers (применимы)

- dense_recall@10 = **<X>%** → <skip Phase 6 если ≥ 75%>
- reranked_recall@10 = **<X>%** → <skip Phase 5 если ≥ 70%>

### Recommended next plan

**Plan 3:** <название следующего плана исходя из решения>

## Constrained decoding status

- [ ] Работает на Ario (Test 1 PASSED в Task 8)
- [ ] Не работает (Qwen3 bug #18819) — используется fallback mode

## Notes

<любые наблюдения, неожиданные результаты, риски>
```

- [ ] **Step 4: Commit baselines + decision**

```bash
git add data/eval_baselines/2026-05-27_v0.4.tooling_retrieval.json
git add data/eval_baselines/2026-05-27_v0.4.tooling_reranker.json
git add data/eval_baselines/2026-05-27_v0.4.tooling_all.json
git add docs/checkpoint1_decision.md
git commit -m "$(cat <<'EOF'
eval: Phase 1 (tooling upgrade) baselines + Checkpoint 1 decision

Three baseline snapshots with v0.4.tooling tag (BGE-M3 + BGE-Reranker-v2-m3
+ constrained decoding). Decision document compares against Phase 0 baseline
and selects the branching path per spec Section 9 Checkpoint 1.
EOF
)"
```

---

## Acceptance Criteria

Phase 1 считается завершённой, когда:

- [ ] BGE-M3 vector_db пересобрана и работает (`data/vector_db_bge_m3/embeddings.npy` shape (2108, 1024))
- [ ] `--stage retrieval` с BGE-M3 даёт recall@10 ≥ 45% (минимум +4pp vs Phase 0 baseline 41.1%) — иначе rollback
- [ ] BGE-Reranker-v2-m3 интегрирован, `--stage reranker` даёт reranked_recall@10 ≥ 25% (минимум +12pp vs Phase 0 baseline 12.5%) — иначе rollback
- [ ] 8 unit tests для `llm_schemas.py` проходят
- [ ] Constrained decoding либо работает (smoke OK), либо документирован fallback
- [ ] 3 новых baseline snapshots в git (`data/eval_baselines/2026-05-27_v0.4.tooling_*.json`)
- [ ] `docs/checkpoint1_decision.md` написан с явным решением о дальнейших фазах

---

## Что НЕ входит в Phase 1 (для будущих фаз)

- Тренировка CE на наших данных — Phase 5 (или отменится)
- LoRA fine-tune на BGE-M3 — Phase 6 (или отменится)
- LLM-router (Stage 1+2) — Phase 2
- PTO cleanup — Phase 3
- Aggregate score fix в reranker.py — Phase 4
- Soft whitelist boost — Phase 4

---

## Rollback plan

Если Phase 1 ухудшает baseline (Top-1 падает ≥ 3pp), сделать revert:

```bash
git checkout main
git branch -D phase-1-tooling-upgrade  # discard
# Восстановить .env из backup:
cp .env.backup.phase0 .env
```

Если только один из компонентов плохой — revert только relevant commits через `git revert`.

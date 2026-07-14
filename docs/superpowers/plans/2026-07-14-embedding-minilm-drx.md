# MiniLM Embedding Variant (DRX platform-model check) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On branch `feat/embedding-minilm-drx`, run a parallel Docker service (port 8011) that uses `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` for embeddings, with its own rebuilt vector DB, alongside the untouched e5 service (port 8010).

**Architecture:** Config-only model swap — the code already branches embedding prefixes on `"e5" in EMBEDDING_MODEL`, so MiniLM loads as a plain SentenceTransformer. A separate vector DB (`data/vector_db_minilm`, 2108×384, pure dense, no adapter) is built with `build_vectordb.py`. A second compose service (`classifier-minilm`) reuses the same image, overriding model/DB via `.env.minilm`. One small fail-fast guard rejects an embedding-dimension mismatch with a clear message.

**Tech Stack:** Python 3.11, sentence-transformers, numpy, FastAPI/uvicorn, Docker Compose, pytest.

---

## File Structure

- **Create** `.env.minilm.example` — committed template of the 3 override vars.
- **Create** `.env.minilm` — local, gitignored; actual overrides used by compose.
- **Create** `data/vector_db_minilm/` — local, gitignored; `embeddings.npy` (2108×384) + `metadata.json`.
- **Create** `data/appeals_log_minilm.jsonl`, `data/request_log_minilm.jsonl` — empty, gitignored; per-variant runtime logs.
- **Create** `tests/test_embedding_dim_guard.py` — unit test for the dimension guard.
- **Modify** `.gitignore` — add the three new local paths.
- **Modify** `src/classifier_agent.py` — add `assert_embedding_dim()` helper + call it before the similarity matmul (line ~839).
- **Modify** `docker-compose.yml` — add the `classifier-minilm` service.

---

## Task 1: Create the branch

**Files:** none (git only)

- [ ] **Step 1: Create and switch to the branch from main**

Run:
```bash
git checkout main
git pull --ff-only 2>/dev/null; git checkout -b feat/embedding-minilm-drx
```
Expected: `Switched to a new branch 'feat/embedding-minilm-drx'`

- [ ] **Step 2: Confirm base commit**

Run: `git log --oneline -1`
Expected: HEAD at `927a9cf` (or later) — the current `main` tip.

---

## Task 2: gitignore + env template

**Files:**
- Modify: `.gitignore`
- Create: `.env.minilm.example`

- [ ] **Step 1: Append ignore entries to `.gitignore`**

Add these lines at the end of `.gitignore`:
```
# --- MiniLM DRX variant (feat/embedding-minilm-drx) ---
.env.minilm
data/vector_db_minilm/
data/appeals_log_minilm.jsonl
data/request_log_minilm.jsonl
```

- [ ] **Step 2: Create `.env.minilm.example`**

Create `.env.minilm.example` with exactly:
```
# Overrides for the MiniLM DRX-platform variant.
# Copy to .env.minilm (gitignored). docker compose loads [.env, .env.minilm];
# these three keys override the values inherited from .env.
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
VECTOR_DB_DIR=data/vector_db_minilm
ENABLE_EMBEDDING_ADAPTER=false
```

- [ ] **Step 3: Create the real `.env.minilm` from the template**

Run: `cp .env.minilm.example .env.minilm`
Expected: file exists; `git status` shows it as ignored (not listed).

- [ ] **Step 4: Verify `.env.minilm` is ignored and example is tracked**

Run: `git status --short .env.minilm .env.minilm.example`
Expected: only `?? .env.minilm.example` appears; `.env.minilm` is absent (ignored).

- [ ] **Step 5: Commit**

```bash
git add .gitignore .env.minilm.example
git commit -m "chore(minilm): gitignore variant artifacts + env template"
```

---

## Task 3: Embedding-dimension fail-fast guard (TDD)

**Files:**
- Test: `tests/test_embedding_dim_guard.py`
- Modify: `src/classifier_agent.py` (add helper near top-level; call before matmul at ~line 839)

- [ ] **Step 1: Write the failing test**

Create `tests/test_embedding_dim_guard.py`:
```python
"""Guard: query embedding dim must match the vector-DB matrix dim."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier_agent import assert_embedding_dim


def test_matching_dims_ok():
    db = np.zeros((5, 384), dtype=np.float32)
    query = np.zeros(384, dtype=np.float32)
    # Should not raise.
    assert_embedding_dim(db, query)


def test_mismatch_raises_valueerror_with_dims():
    db = np.zeros((2108, 384), dtype=np.float32)
    query = np.zeros(768, dtype=np.float32)
    with pytest.raises(ValueError) as exc:
        assert_embedding_dim(db, query)
    msg = str(exc.value)
    assert "384" in msg and "768" in msg
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_embedding_dim_guard.py -v`
Expected: FAIL — `ImportError: cannot import name 'assert_embedding_dim'`.

- [ ] **Step 3: Add the helper to `src/classifier_agent.py`**

Add this module-level function (place it just above the `ClassifierAgent` class definition):
```python
def assert_embedding_dim(db_matrix: "np.ndarray", query_vec: "np.ndarray") -> None:
    """Fail fast with a clear message if the query embedding dimension does not
    match the vector-DB matrix. Prevents a cryptic numpy shape error and catches
    a model/DB mismatch (e.g. adapter left on, or wrong VECTOR_DB_DIR)."""
    db_dim = db_matrix.shape[1]
    q_dim = query_vec.shape[0]
    if db_dim != q_dim:
        raise ValueError(
            f"Embedding dim mismatch: vector DB has {db_dim}-dim vectors but the "
            f"query embedding is {q_dim}-dim. Check EMBEDDING_MODEL vs VECTOR_DB_DIR "
            f"(and ENABLE_EMBEDDING_ADAPTER)."
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/test_embedding_dim_guard.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire the guard into the search path**

In `src/classifier_agent.py`, find the line (~839):
```python
        similarities = self.embeddings @ query_vec
```
Replace it with:
```python
        assert_embedding_dim(self.embeddings, query_vec)
        similarities = self.embeddings @ query_vec
```

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass (same count as before + 2 new).

- [ ] **Step 7: Commit**

```bash
git add tests/test_embedding_dim_guard.py src/classifier_agent.py
git commit -m "feat(minilm): fail-fast guard on embedding-dim mismatch"
```

---

## Task 4: Build the MiniLM vector DB

**Files:**
- Create (generated): `data/vector_db_minilm/embeddings.npy`, `data/vector_db_minilm/metadata.json`

Note: `config.py` calls `load_dotenv(".env")` without `override=`, so shell env vars take
precedence over `.env`. Setting them before the build produces the MiniLM DB without
touching the production `.env`. This also downloads MiniLM (~120 MB) into the host HF cache,
which the container reuses via the existing `:ro` mount.

- [ ] **Step 1: Build the DB with MiniLM env overrides (PowerShell)**

Run (PowerShell, from project root):
```powershell
$env:EMBEDDING_MODEL="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
$env:VECTOR_DB_DIR="data/vector_db_minilm"
$env:ENABLE_EMBEDDING_ADAPTER="false"
venv\Scripts\python.exe src\build_vectordb.py
Remove-Item Env:EMBEDDING_MODEL,Env:VECTOR_DB_DIR,Env:ENABLE_EMBEDDING_ADAPTER
```
Expected: log shows `Загрузка модели эмбеддингов: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, a line `(без 'passage: ' префикса …)`, and `База построена! 2108 записей в data/vector_db_minilm`.

- [ ] **Step 2: Verify shape and metadata count**

Run:
```bash
venv/Scripts/python.exe -c "import numpy as np, json; a=np.load('data/vector_db_minilm/embeddings.npy'); m=json.load(open('data/vector_db_minilm/metadata.json',encoding='utf-8')); print('shape', a.shape, 'meta', len(m))"
```
Expected: `shape (2108, 384) meta 2108`.

- [ ] **Step 3: Confirm no adapter file was written**

Run: `ls data/vector_db_minilm/`
Expected: only `embeddings.npy` and `metadata.json` (no `adapter_*.json`).

- [ ] **Step 4: Confirm the DB is gitignored (not staged)**

Run: `git status --short data/vector_db_minilm/`
Expected: no output (ignored).

No commit — the DB is a generated, gitignored artifact.

---

## Task 5: Add the `classifier-minilm` compose service

**Files:**
- Modify: `docker-compose.yml`
- Create (empty): `data/appeals_log_minilm.jsonl`, `data/request_log_minilm.jsonl`

- [ ] **Step 1: Create the empty per-variant log files (PowerShell)**

Run:
```powershell
if (-not (Test-Path data/appeals_log_minilm.jsonl)) { New-Item -ItemType File data/appeals_log_minilm.jsonl }
if (-not (Test-Path data/request_log_minilm.jsonl)) { New-Item -ItemType File data/request_log_minilm.jsonl }
```
Expected: both files exist and are empty.

- [ ] **Step 2: Add the service to `docker-compose.yml`**

Under `services:`, after the existing `classifier:` block, add a sibling service (same indentation as `classifier:`):
```yaml
  classifier-minilm:
    build: .
    ports:
      # host 8011 (MiniLM DRX variant) -> внутренний 8005 приложения
      - "8011:8005"
    volumes:
      # отдельные лог-файлы, чтобы не конфликтовать с e5-контейнером при конкурентном append
      - ./data/appeals_log_minilm.jsonl:/app/data/appeals_log.jsonl
      - ./data/request_log_minilm.jsonl:/app/data/request_log.jsonl
      - ./data/historical_verified.jsonl:/app/data/historical_verified.jsonl:ro
      - ./data/classifier_annotations.json:/app/data/classifier_annotations.json:ro
      - ./data/vector_db_minilm:/app/data/vector_db_minilm:ro
      - ./models:/app/models
      - C:/Users/Korotaev_NO/.cache/huggingface:/root/.cache/huggingface:ro
      - ./data/allowed_codes_top69.json:/app/data/allowed_codes_top69.json:ro
    env_file:
      - .env
      - .env.minilm
    restart: unless-stopped
```

- [ ] **Step 3: Validate compose config**

Run: `docker compose config --services`
Expected: lists both `classifier` and `classifier-minilm`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(minilm): add parallel classifier-minilm service on port 8011"
```

---

## Task 6: Bring up and smoke-test on 8011

**Files:** none (runtime verification)

- [ ] **Step 1: Start both services (reuses the existing image)**

Run: `docker compose up -d`
Expected: `classifier` and `classifier-minilm` both `Started`. `classifier-minilm` uses the
already-built image (no rebuild). If it rebuilds, that is fine — the app code is identical.

- [ ] **Step 2: Verify both containers and ports**

Run: `docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"`
Expected: `classifier` → `0.0.0.0:8010->8005`, `classifier-minilm` → `0.0.0.0:8011->8005`, both `Up`.

- [ ] **Step 3: Poll /health on 8011 until ready**

Run:
```bash
i=0; until curl -s -m 5 http://127.0.0.1:8011/health 2>/dev/null | grep -q '"status"'; do i=$((i+1)); [ $i -ge 40 ] && { echo TIMEOUT; docker compose logs --tail 30 classifier-minilm; break; }; sleep 3; done; curl -s http://127.0.0.1:8011/health
```
Expected: `{"status":"ok","agent_ready":true,"classifier_entries":2108}`.

- [ ] **Step 4: Smoke `/classify` on 8011**

Run:
```bash
venv/Scripts/python.exe -c "import httpx,sys; sys.stdout.reconfigure(encoding='utf-8'); d=httpx.post('http://127.0.0.1:8011/classify',json={'appeal_text':'Прошу отремонтировать дорогу по улице Ленина, ямы после зимы.'},timeout=90).json(); q=d['questions'][0]; print('code',q.get('code'),'|',q.get('code_name') or q.get('name'),'| conf',q.get('confidence'),'| provider',d.get('llm_provider'))"
```
Expected: HTTP 200; a road-repair-related code, `provider ario`. (Exact code may differ from e5 — that is the point of the comparison.)

- [ ] **Step 5: Smoke the OData integration endpoint on 8011**

Run:
```bash
venv/Scripts/python.exe -c "import httpx,json,sys; sys.stdout.reconfigure(encoding='utf-8'); r=httpx.post('http://127.0.0.1:8011/integration/classify-document',json={'document_id':15920},timeout=150); print('HTTP',r.status_code); print(json.dumps(r.json(),ensure_ascii=False,indent=2)[:1200])"
```
Expected: HTTP 200; JSON with `applicant_fio` (full FIO), `summary`, and at least one `questions[]` entry with a `code`.

- [ ] **Step 6: Confirm the e5 service (8010) still healthy (no regression)**

Run: `curl -s http://127.0.0.1:8010/health`
Expected: `{"status":"ok","agent_ready":true,"classifier_entries":2108}`.

No commit — this task only verifies runtime behavior.

---

## Task 7: Document the variant in HANDOFF

**Files:**
- Modify: `HANDOFF.md` (append a short section)

- [ ] **Step 1: Append a MiniLM-variant section to `HANDOFF.md`**

Add at the end of `HANDOFF.md`:
```markdown
## MiniLM DRX-platform variant (branch `feat/embedding-minilm-drx`)

Parallel prototype using `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
(384-dim) to check the DRX platform embedding model.

- Run: `docker compose up -d` → e5 on **:8010**, MiniLM on **:8011** (both live).
- Rebuild the MiniLM DB (if needed): set `EMBEDDING_MODEL`/`VECTOR_DB_DIR`/`ENABLE_EMBEDDING_ADAPTER`
  env vars and run `python src/build_vectordb.py` (see `.env.minilm.example`).
- Pure dense, no adapter (the e5 768→768 adapter does not apply to 384-dim MiniLM).
- Smoke verified: `/health`, `/classify`, `/integration/classify-document {15920}` on :8011.
```

- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md
git commit -m "docs(minilm): document the parallel MiniLM variant in HANDOFF"
```

---

## Definition of Done

- Branch `feat/embedding-minilm-drx` contains: gitignore + env template, dim guard + test, compose service, HANDOFF note.
- `docker compose up -d` runs both services; MiniLM on :8011 passes `/health`, `/classify`, and OData `{15920}`; e5 on :8010 unaffected.
- Full pytest suite green (including the new dim-guard test).
- No production artifacts changed (`.env`, `vector_db_adapted_v3`, e5 service).

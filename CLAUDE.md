# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI agent that classifies Russian citizen appeals (обращения граждан) according to Federal Law 59-FZ and the Общероссийский классификатор вопросов обращений граждан (v4, 2108 entries). Intended for integration with Directum RX 25.3.

## Commands

All commands run from the **project root**, not from `src/`.

```bash
# First-time setup: build the vector database (one-time, ~5-15 min)
python src/build_vectordb.py

# Run classifier data integrity tests
python -m pytest tests/

# Run a single test
python -m pytest tests/test_search.py::test_flat_entries_count

# Run 4 built-in classification test appeals
python src/test_agent.py

# Interactive operator mode (verification + fine-tuning trigger)
python src/operator_cli.py

# Fine-tune embedding model on verified data
python src/finetune_model.py

# Start API server
uvicorn src.api_server:app --host 0.0.0.0 --port 8000 --reload
```

**Python version: 3.11 required.** Python 3.12+ breaks ML dependencies; 3.14 is incompatible with `sentence-transformers`.

## Architecture

### Two-stage classification pipeline

```
Appeal text
    │
    ▼
[Stage 1] Semantic vector search (numpy)
    - multilingual-e5-base encodes query as 768-dim vector
    - Cosine similarity: embeddings_matrix @ query_vector
    - Returns top-10 candidates from 2108 classifier entries
    │
    ▼
[Stage 2] LLM reasoning (Groq API)
    - llama-3.3-70b-versatile receives appeal text + 10 candidates
    - Identifies: appeal type (59-FZ), all questions in appeal, best code per question
    - Returns strict JSON with confidence scores and reasoning
    │
    ▼
ClassificationResult → operator card + log entry
```

### Key files

- **`src/config.py`** — all constants from `.env`. Single source of truth for paths and model names.
- **`src/classifier_agent.py`** — `ClassifierAgent` class with `classify(text) → ClassificationResult`. The `ClassificationResult` dataclass includes `log_id` for linking to the verification log.
- **`src/appeals_logger.py`** — append-only JSONL log (`data/appeals_log.jsonl`). Each entry stores appeal text, top-10 candidates with similarity scores, agent's chosen codes, and verification status (`pending/confirmed/corrected/rejected`).
- **`src/operator_cli.py`** — interactive CLI loop: classify → show result → operator picks [1/2/3] → log verification → check fine-tuning threshold.
- **`src/finetune_model.py`** — reads verified log entries, builds `(appeal_text, classifier_entry_name)` pairs, fine-tunes with `MultipleNegativesRankingLoss`, evaluates recall@5, saves to `models/e5-finetuned-vN/`.
- **`src/build_vectordb.py`** — one-time script. Reads `data/classifier_flat.json`, vectorizes all 2108 entries with `passage:` prefix (e5 convention), saves `data/vector_db/embeddings.npy` + `data/vector_db/metadata.json`.
- **`src/api_server.py`** — FastAPI REST API. `/health` returns agent status and classifier entry count. `/classify` — main classification endpoint.

### Vector DB storage

ChromaDB was rejected due to Windows/Python 3.11 incompatibility. The DB is two plain files:
- `embeddings.npy` — shape (2108, 768), float32
- `metadata.json` — list of `{code, name, level, parent_code, full_path, search_text}`

**Rebuild required** when `EMBEDDING_MODEL` changes or classifier data updates.

### Classifier data structure

`data/classifier_flat.json` — 2108 entries, 4-level hierarchy with codes like `XXXX.XXXX.XXXX.XXXX`. Level 4 entries are the classification targets. `search_text` field is what gets embedded (combines name + path context).

### Fine-tuning loop

Operator verifications in `data/appeals_log.jsonl` accumulate training signal:
- `confirmed` → positive pair: `(appeal, agent_code_name)`
- `corrected` → positive: `(appeal, operator_code_name)` + negative: `(appeal, agent_code_name)`

Trigger threshold: `FINETUNE_THRESHOLD` (default 50, set in `.env`). After fine-tuning, update `EMBEDDING_MODEL` in `.env` to the output path and re-run `build_vectordb.py`.

## Configuration (`.env`)

```
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
EMBEDDING_MODEL=intfloat/multilingual-e5-base   # or path to fine-tuned model
TOP_K_CANDIDATES=10
MIN_CONFIDENCE=0.65      # below this → needs_verification=True
FINETUNE_THRESHOLD=50    # verified entries needed to trigger fine-tuning
```

## Important constraints

- Scripts import from `src/` using `sys.path.insert(0, 'src')` — always run from project root.
- `numpy<2.0` is pinned (`numpy>=1.26.0,<2.0`) — numpy 2.x removes `np.float_` used by older dependencies.
- `data/vector_db/` and `models/` are excluded from Git (see `.gitignore`); `data/appeals_log.jsonl` is tracked.
- `setup.ps1` is ASCII-only — Cyrillic strings cause PowerShell parse errors on Windows with CP1251 encoding.

## GitHub

- Repository: https://github.com/nikitakorotaevnikita-sudo/OG
- Branch: `master`

## Session Start Protocol

At the **beginning of each session**, Claude Code MUST:

1. **Read Hub instructions** — `C:/Users/Korotaev_NO/Desktop/Obsidian vault/AI Agent Hub/instructions/core.md`
2. **Read Hub session-start** — `C:/Users/Korotaev_NO/Desktop/Obsidian vault/AI Agent Hub/instructions/session-start.md`
3. **Read project Obsidian notes** — check for updates in:
   - `C:/Users/Korotaev_NO/Desktop/Obsidian vault/Прототипы/ИИ Агенты/agent_appeals/`
   - Look for files modified today and recent planning files
4. **Read relevant tickets** — check `Тикеты/TICKET-*.md` for current state
5. **Give session summary** — before starting work, output:
   ```
   ## Session Summary
   - Last session: [date]
   - What was done: [1-2 sentences]
   - What's next: [next step]
   - Open issues: [any blockers]
   ```

## Hub Instructions

This project follows the AI Agent Hub standards:
- **Language:** Russian for user communication, English for technical docs
- **Stack:** Python 3.11 / FastAPI, Vanilla JS/HTML/CSS, pytest, Docker
- **Principle:** Agent proposes, human approves. юридически значимые действия — через human-in-the-loop.
- **Docs:** General rules in Hub, local info only in project README


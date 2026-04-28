# Multi-Agent Prototype Development System

System for rapid prototype creation based on hypotheses.

## Quick Start

1. Fill in `.hypothesis/00_HYPOTHESIS.md`
2. Run `/pm` — Project Manager launches the full pipeline

**Modes:**
- `/pm` — launches PM, which asks what to do next (MANUAL by default)
- `/pm MODE: AUTO` — PM drives the entire process automatically

**Individual agents (manual launch):**
- `/ba` — Business Analyst (hypothesis analysis → `01_REQUIREMENTS.md`)
- `/architect` — Architect (design → `02_ARCHITECTURE.md`)
- `/dev` — Developer (implementation → `04_IMPLEMENTATION.md`)
- `/qa` — QA (testing + acceptance criteria verification → `05_TEST_RESULTS.md`)

> **How the chain works:** `/pm` launches the `pm` agent (`.claude/agents/pm.md`), which uses the `Task` tool to sequentially spawn `ba → architect → developer → qa`, without pausing between steps. PM makes the final GO/NO-GO decision.

## Mandatory Stack

- **Python 3.10+**
- **UI:** Vanilla JS (plain HTML/CSS/JS, no frameworks)
- **Backend:** FastAPI (required — serves UI and API)
- **Tests:** pytest ≥70% coverage + Playwright E2E
- **Deployment:** Docker + docker-compose
- **LLM:** OpenAI Python SDK only (no direct HTTP requests)
- **Forbidden:** React/Vue/Angular/Next.js/Svelte and any JS frameworks
- **Styles, icons, logos** Directum and Directum Ario: https://www.directum.ru/ui-kit

## Pipeline and Files

```
00_HYPOTHESIS.md → [BA] → 01_REQUIREMENTS.md → [Architect] → 02_ARCHITECTURE.md
→ [Developer] → 04_IMPLEMENTATION.md → [QA] → 05_TEST_RESULTS.md → [PM: GO/NO-GO]
```

`BUILD_LOG.md` is maintained by PM throughout the entire process.

## Reference Code (in `src/`)

The template contains **working reference code** to build upon:

| File | Purpose | How to use |
|------|---------|------------|
| `src/static/style.css` | Design system (CSS variables, components) | As-is, append project styles at end |
| `src/static/index.html` | UI skeleton (loading, cases, chat, feedback) | Adapt content, preserve structure |
| `src/static/app.js` | State, objects, cases, chat, feedback | Adapt state/CASE_NAMES, core as-is |
| `src/static/markdown.js` | Markdown renderer | As-is, don't touch |
| `src/static/sse.js` | SSE stream reader | As-is, don't touch |
| `src/static/prompts.js` | Prompt management, .docx download | Minimal adaptation |
| `src/static/backoffice.*` | Metrics page | Minimal adaptation |
| `src/main.py` | FastAPI skeleton with SSE pattern | Replace stubs with real services |
| `src/services/llm_service.py` | OpenAI SDK streaming | As-is |
| `src/services/metrics_storage.py` | SQLite metrics | As-is, adapt case count |
| `src/services/docx_generator.py` | Word report generator | As-is, adapt document title |
| `src/config.py` | Reads .env | As-is |
| `Dockerfile` | Multi-stage (prod + test) | As-is |

All adaptation points are marked with `TEMPLATE:` and `IMPORTANT:` comments.

## Unified UI/UX Patterns

All prototypes follow a **chat-first two-column layout**:

**Left panel (aside):** logo → object filter (optional) → object list (searchable combobox) → child items → selection indicator

**Right area (main):** header (title + backoffice link, **no logo**) → chat with SSE streaming → feedback (👍/👎) → case buttons bar → input field

**Logo:** placed **only in the left panel**, once. Not duplicated in the right area header. If logo is dark — add `background: white` to `.panel-header`.

**Object list:** always searchable combobox (not native `<select>`). With N > 20 objects `<select>` is unacceptable — no search. Combobox template is already in `index.html` + `app.js`.

**Object sorting:** alphabetical, ascending (`asc`) + `localeCompare('ru')` on client.

**UI strings:** all visible strings — **Russian only**. Translate API technical terms.

**Object API (required endpoints):**
- `GET /api/objects` → `{"objects": [{"id": N, "name": "..."}]}`
- `GET /api/objects/{id}` → `{"context": "...", "items": [{"id": N, "name": "...", "meta": "..."}]}`
- `GET /api/items/{id}` → `{"context": "..."}`

**Case buttons:** `data-mode="object"` active only when an object is selected; `data-mode="item"` — only when an item is selected.

**Prompt management:** Custom and system prompts managed via UI modals and `/api/prompts` API. Implementation in `prompts.js` + `metrics_storage.py`. Details in template code.

**Report download:** `appendDownloadButton(assistantDiv)` after SSE stream. Implementation in `prompts.js` + `docx_generator.py`.

**Backoffice:** `/backoffice` (Basic Auth) — summary, charts, IP tables, case stats, chat feedback

## Critical Rules

- **Real execution**: agents MUST run commands, not simulate
- **Docker-first**: everything verified in Docker
- **Backoffice mandatory**: every prototype includes a metrics page (IP, ratings, frequency)
- **`.env` hands-off**: never create, edit, or overwrite `.env`
- **Single source of truth**: only files from `.hypothesis/`, `src/`, `tests/`
- **Template UI/UX**: preserve UI structure for consistency across projects

## Git Workflow

- "Закоммить и запушить" (commit and push) without clarification = commit to current branch + `git push origin HEAD`
- Feature branches and PRs are created **only on explicit user request**
- Never create branches on your own without explicit agreement

## Test Artifacts (Playwright)

- E2E tests save screenshots to `tmp/screenshots/` (not root, not `tests/`)
- `tmp/` is in `.gitignore`
- **Never add `*.png` globally to `.gitignore`** — this blocks storing images in the repo

## Communication Rules

- Agent instructions: English
- User communication: Russian
- Docstrings: Russian

## Environment Variables

`.env` in project root (don't touch!):
- `OPENAI_API_KEY` — required
- `OPENAI_MODEL` — required
- `OPENAI_SERVER` — optional (custom endpoint)

## Escalation

If an agent is stuck (>5 attempts) → writes a `[BLOCKER]` block in `BUILD_LOG.md` and notifies the user.

If QA finds an architecture problem (not code) → escalation through PM back to Architect.

## Known Pitfalls

Full list documented in `.claude/agents/developer.md`, section "Known Pitfalls". Key ones:

- **JS libraries** must be stored locally (not CDN) — Docker has no internet
- **App location**: always in project ROOT (next to `src/`, `Dockerfile`). `hypothesis-app/` is a separate tool
- **Backoffice scrolling**: `backoffice.html` must override: `html, body { height: auto !important; overflow: auto !important; }`

## Agents

Agent instructions: `.claude/agents/*.md`

| File | Purpose |
|------|---------|
| `pm.md` | Project Manager — orchestrates pipeline, GO/NO-GO decision |
| `ba.md` | Business Analyst — hypothesis analysis |
| `architect.md` | Architect — architecture design |
| `developer.md` | Developer — implementation + known pitfalls |
| `qa.md` | QA Engineer — testing + acceptance criteria verification |
| `design_guide.md` | Directum design system — colors, typography, CSS components. **Read by:** architect, developer |

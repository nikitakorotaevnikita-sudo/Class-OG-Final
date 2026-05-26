# EPIC-08: Повышение точности классификации обращений — Design & Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Повысить точность выбора точного кода классификатора Level 4/5 без потери скорости MVP: сначала увеличить recall правильного кода в кандидатах, затем улучшить ранжирование и контроль LLM-ответа.

**Problem Statement:** Текущий MVP показывает хороший prefix accuracy (90%), но слабый exact Top-1/Top-3 (40%). Это означает, что система обычно попадает в правильную ветку классификатора, но путает близкие leaf-коды. Главный риск: LLM выбирает только из top-10 кандидатов, найденных одним dense retrieval по полному тексту обращения.

**Current Architecture:**

```
appeal_text
  -> SentenceTransformer multilingual-e5-base embedding
  -> numpy cosine top-10 over 2108 classifier entries
  -> LLM final selection
  -> operator verification
  -> fine-tuning embeddings
```

**Target Architecture:**

```
appeal_text
  -> question segmentation
  -> per-question hybrid retrieval
       dense top-N
       lexical/BM25 top-N
       hierarchy-aware boosts
  -> merge + deduplicate candidate pool
  -> rerank shortlist
       cross-encoder if enabled
       fallback heuristic reranker
  -> strict LLM decision over validated candidates
  -> calibrated confidence + verification reason
  -> operator feedback + hard-negative fine-tuning
```

**Research / Practice Basis:**
- E5 embeddings are designed for retrieval and classification-style semantic matching; multilingual E5 supports cross-lingual and multilingual retrieval (`query:` / `passage:` prefixes remain important). Sources: E5 paper https://arxiv.org/abs/2212.03533, multilingual E5 report https://arxiv.org/abs/2402.05672
- Retrieve-and-rerank is a standard architecture: bi-encoder retrieves a broad candidate pool cheaply, cross-encoder reranks a smaller shortlist more accurately. Source: Sentence Transformers Cross-Encoder docs https://sbert.net/examples/cross_encoder/applications/README.html
- The task is structurally hierarchical multi-label text classification: several questions can map to several labels, and labels live in a taxonomy. Sources: survey https://arxiv.org/abs/2307.16265, hierarchy-guided BERT/local-global hierarchy paper https://arxiv.org/abs/2205.02613

**Non-Goals for EPIC-08:**
- Do not replace all logic with an opaque LLM-only classifier.
- Do not train a full supervised 2108-label classifier until enough labeled data exists.
- Do not block the UI while reranking or fine-tuning; all expensive steps must be optional or asynchronous.

---

## File Structure

```
src/
  classifier_agent.py              MODIFY - orchestration, segmentation, strict validation
  retrieval.py                     NEW - dense + lexical retrieval and candidate merging
  reranker.py                      NEW - cross-encoder / heuristic reranking
  hierarchy.py                     NEW - code path utilities, branch/prefix scoring
  confidence.py                    NEW - calibrated confidence calculation
  finetune_model.py                MODIFY - hard negatives for corrected cases
  api_server.py                    MODIFY - debug/eval endpoints if needed
  config.py                        MODIFY - feature flags and top-N params

tests/
  test_retrieval.py                NEW
  test_reranker.py                 NEW
  test_hierarchy.py                NEW
  test_confidence.py               NEW
  test_llm_validation.py           NEW
  eval_accuracy.py                 MODIFY - richer metrics

docs/
  accuracy_report_v2.md            NEW - baseline + post-EPIC comparison
```

---

## Task 1: Evaluation Baseline v2

**Files:**
- Modify: `tests/eval_accuracy.py`
- Create: `docs/accuracy_report_v2.md`
- Optional: Create/extend `tests/fixtures/test_appeals_v2.json`

- [ ] **Step 1: Extend metrics**

Add metrics:
- `retrieval_recall@10`, `retrieval_recall@50`
- `rerank_recall@10`
- `exact_top1`, `exact_top3`, `exact_top5`
- `prefix_level1`, `prefix_level2`, `prefix_level3`
- `question_count_accuracy`
- `needs_verification_rate`
- `invalid_llm_code_rate`
- latency by stage: segmentation, retrieval, rerank, LLM

- [ ] **Step 2: Add stage-level debug output**

For each fixture, save:

```json
{
  "id": "ex01",
  "expected_codes": ["..."],
  "segments": ["..."],
  "dense_candidates": ["..."],
  "lexical_candidates": ["..."],
  "reranked_candidates": ["..."],
  "llm_codes": ["..."],
  "stage_timings": {}
}
```

- [ ] **Step 3: Run current baseline before changes**

```powershell
.\venv\Scripts\python.exe tests\eval_accuracy.py --fixtures tests\fixtures\test_appeals.json
```

- [ ] **Step 4: Save report**

Write baseline to `docs/accuracy_report_v2.md`, including exact failures and where the expected code first appears: dense top-10/top-50, lexical top-50, reranked top-10.

**Acceptance Criteria:**
- We can see whether exact-code errors are retrieval failures, reranking failures, or LLM decision failures.
- Report is reproducible from one command.

---

## Task 2: Question Segmentation Before Retrieval

**Files:**
- Modify: `src/classifier_agent.py`
- Create: `tests/test_question_segmentation.py`

- [ ] **Step 1: Add segmentation result type**

Suggested structure:

```python
@dataclass
class AppealQuestion:
    text: str
    ordinal: int
    evidence: str
```

- [ ] **Step 2: Implement conservative segmenter**

Implement `_split_appeal_questions(appeal_text)`.

Rules:
- If text has explicit markers (`во-первых`, `во-вторых`, `1.`, `2.`, `также`, `кроме того`) split.
- If no strong markers, return one question with full text.
- Limit max segments, e.g. 5.
- Keep original text snippets; do not summarize away important legal terms.

- [ ] **Step 3: Optional LLM segmentation fallback**

Feature flag:

```env
ENABLE_LLM_SEGMENTATION=false
```

Default should stay deterministic. LLM segmentation can be added later for difficult multi-question appeals.

- [ ] **Step 4: Use per-question retrieval**

Instead of searching once over full appeal, search for each segment separately. Pass per-question candidate pools to LLM.

**Acceptance Criteria:**
- Single-question appeals behave as before.
- Multi-question fixture `ex10` creates 3 question segments.
- Each segment has its own retrieval candidates.

---

## Task 3: Hybrid Retrieval

**Files:**
- Create: `src/retrieval.py`
- Modify: `src/classifier_agent.py`
- Modify: `src/config.py`
- Create: `tests/test_retrieval.py`

- [ ] **Step 1: Extract dense retrieval**

Move current numpy cosine logic from `_search_candidates` to `DenseRetriever`.

Suggested config:

```python
DENSE_TOP_K: int = int(os.getenv("DENSE_TOP_K", "50"))
FINAL_CANDIDATE_TOP_K: int = int(os.getenv("FINAL_CANDIDATE_TOP_K", "10"))
```

- [ ] **Step 2: Add lexical retrieval**

Implement simple BM25-like retrieval without heavy infrastructure:
- tokenize Russian text with lowercase + regex words
- index `metadata.search_text`, `name`, `full_path`, `code`
- score by BM25 or TF-IDF-style overlap

Config:

```python
ENABLE_LEXICAL_RETRIEVAL: bool = os.getenv("ENABLE_LEXICAL_RETRIEVAL", "true").lower() == "true"
LEXICAL_TOP_K: int = int(os.getenv("LEXICAL_TOP_K", "50"))
```

- [ ] **Step 3: Merge candidates**

Merge dense and lexical candidates by code:

```python
combined_score = 0.70 * dense_norm + 0.30 * lexical_norm + hierarchy_boost
```

Keep source scores in debug output:

```json
{
  "code": "...",
  "dense_score": 0.81,
  "lexical_score": 0.42,
  "combined_score": 0.69,
  "sources": ["dense", "lexical"]
}
```

- [ ] **Step 4: Preserve existing endpoint**

`/classifier/search` should still work, but return combined candidates and scores.

**Acceptance Criteria:**
- Retrieval returns at least `FINAL_CANDIDATE_TOP_K`.
- Existing dense search behavior is preserved when lexical flag is false.
- `retrieval_recall@50` improves or remains equal on fixture set.

---

## Task 4: Hierarchy-Aware Candidate Handling

**Files:**
- Create: `src/hierarchy.py`
- Modify: `src/retrieval.py`
- Modify: `tests/eval_accuracy.py`
- Create: `tests/test_hierarchy.py`

- [ ] **Step 1: Add code utilities**

Functions:

```python
def code_parts(code: str) -> list[str]
def prefix_at_level(code: str, level: int) -> str
def same_branch(a: str, b: str, level: int) -> bool
def path_distance(a: str, b: str) -> int
```

- [ ] **Step 2: Add branch-aware scoring**

Use classifier metadata:
- prefer deeper leaf codes when a leaf and parent both match
- keep ancestors as context, but do not let broad parent labels dominate leaf labels
- add small boost when lexical and dense agree on same level-2/level-3 branch

- [ ] **Step 3: Add hierarchy metrics**

In evaluation:
- level-1 accuracy
- level-2 accuracy
- path distance from expected
- exact leaf accuracy

**Acceptance Criteria:**
- Evaluation shows whether a failure is “wrong branch” or “right branch, wrong leaf”.
- Candidate list includes hierarchy context for LLM prompt.

---

## Task 5: Reranking Layer

**Files:**
- Create: `src/reranker.py`
- Modify: `src/classifier_agent.py`
- Modify: `src/config.py`
- Create: `tests/test_reranker.py`

- [ ] **Step 1: Implement reranker interface**

```python
class Reranker:
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        ...
```

- [ ] **Step 2: Implement heuristic fallback reranker**

Always available:
- combined retrieval score
- exact keyword overlap
- important term overlap (`тко`, `субсид`, `пенси`, `полици`, `земель`, `детский сад`)
- hierarchy depth preference

- [ ] **Step 3: Add optional CrossEncoder reranker**

Feature flag:

```env
ENABLE_CROSS_ENCODER_RERANKER=false
CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L6-v2
RERANK_TOP_N=50
FINAL_CANDIDATE_TOP_K=10
```

Notes:
- CrossEncoder is optional because Docker image size and CPU latency can grow.
- For Russian/domain-specific data, evaluate before enabling by default.
- If model is unavailable, fall back to heuristic reranker.

- [ ] **Step 4: Include reranker scores in LLM prompt**

Pass:
- code
- name
- full_path
- dense_score
- lexical_score
- reranker_score

**Acceptance Criteria:**
- Reranker can be disabled without changing outputs materially.
- With heuristic reranker, candidate ordering is deterministic.
- CrossEncoder failure does not break classification.

---

## Task 6: Strict LLM Decision Contract

**Files:**
- Modify: `src/classifier_agent.py`
- Create: `tests/test_llm_validation.py`

- [ ] **Step 1: Validate selected codes**

After LLM response:
- JSON must parse.
- `questions` must be non-empty.
- every `selected_code` must exist in classifier metadata.
- every `selected_code` must be in candidates for that question, unless `ALLOW_OUT_OF_CANDIDATE_CODES=true`.

- [ ] **Step 2: Retry with structured correction**

If invalid:
- retry once with an error message: “selected_code must be one of these codes”
- if still invalid, choose top reranked candidate and set `needs_verification=true`

- [ ] **Step 3: Keep LLM from selecting parent if leaf is required**

Prompt addition:
- prefer the most specific applicable code
- if two candidates differ only by hierarchy level, select the deeper code unless the text is too broad

- [ ] **Step 4: Log validation outcome**

Add fields to request log:
- `llm_invalid_json`
- `llm_invalid_code`
- `fallback_used`

**Acceptance Criteria:**
- Invalid LLM code rate is measurable.
- Classification never crashes because LLM selected a missing code.

---

## Task 7: Confidence Calibration

**Files:**
- Create: `src/confidence.py`
- Modify: `src/classifier_agent.py`
- Modify: `tests/eval_accuracy.py`
- Create: `tests/test_confidence.py`

- [ ] **Step 1: Add confidence components**

Calculate:
- `llm_confidence`
- `dense_score`
- `lexical_score`
- `reranker_score`
- `top_margin`: score(top1) - score(top2)
- `source_agreement`: dense/lexical/reranker agree on same branch
- `fallback_penalty`

- [ ] **Step 2: Compute calibrated confidence**

Initial heuristic:

```python
confidence = (
    0.35 * llm_confidence
  + 0.25 * reranker_score_norm
  + 0.15 * dense_score_norm
  + 0.10 * lexical_score_norm
  + 0.10 * margin_norm
  + 0.05 * source_agreement
  - fallback_penalty
)
```

- [ ] **Step 3: Add verification reason**

Response should explain why verification is needed:

```json
"verification_reasons": [
  "low_margin",
  "low_reranker_score",
  "llm_retry_used"
]
```

**Acceptance Criteria:**
- `needs_verification` is based on calibrated confidence, not only LLM self-score.
- Low-margin choices are flagged even if LLM was overconfident.

---

## Task 8: Fine-tuning With Hard Negatives

**Files:**
- Modify: `src/finetune_model.py`
- Modify: `src/appeals_logger.py` if needed
- Create: `tests/test_finetune_examples.py`

- [ ] **Step 1: Explicitly build hard negatives**

For `corrected` entries:
- positive: `(appeal_text, operator_code)`
- hard negative: `(appeal_text, agent_selected_code)`
- additional hard negatives: high-ranked retrieval candidates not chosen by operator

- [ ] **Step 2: Choose loss / data format**

Options:
- Keep `MultipleNegativesRankingLoss` for baseline.
- Add optional `TripletLoss` or `MarginMSELoss` if sentence-transformers version supports it cleanly.

Recommended first implementation:
- preserve current positive-pair training
- add operator-corrected wrong candidates as extra negatives in evaluator and training batches
- log hard-negative count in report

- [ ] **Step 3: Add training report fields**

Add:
- `positive_pairs`
- `hard_negative_pairs`
- `corrected_records`
- `historical_records`
- `recall_before/after`
- `mrr_before/after`

**Acceptance Criteria:**
- Corrected cases teach the model what not to retrieve as top-1.
- Training report exposes whether hard negatives were used.

---

## Task 9: API and UI Debug Support

**Files:**
- Modify: `src/api_server.py`
- Modify: `src/static/app.js`
- Modify: `src/static/index.html`

- [ ] **Step 1: Add optional debug response**

Request:

```json
{
  "appeal_text": "...",
  "debug": true
}
```

Response includes:
- segments
- candidate scores
- reranker scores
- validation retries
- confidence components

- [ ] **Step 2: Add details panel in UI**

Under JSON response:
- show “Диагностика классификации”
- candidate table by question
- scores and reasons

**Acceptance Criteria:**
- Operator/admin can inspect why a code was selected.
- Debug is opt-in and does not clutter normal response.

---

## Task 10: Rollout and Feature Flags

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/FINETUNING.md`

- [ ] **Step 1: Add config flags**

```env
DENSE_TOP_K=50
LEXICAL_TOP_K=50
FINAL_CANDIDATE_TOP_K=10
ENABLE_LEXICAL_RETRIEVAL=true
ENABLE_HEURISTIC_RERANKER=true
ENABLE_CROSS_ENCODER_RERANKER=false
CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L6-v2
ALLOW_OUT_OF_CANDIDATE_CODES=false
MIN_CONFIDENCE=0.65
```

- [ ] **Step 2: Document recommended modes**

Modes:
- MVP-safe: dense + lexical + heuristic reranker
- quality mode: add cross-encoder reranker
- evaluation mode: debug response enabled

- [ ] **Step 3: Update Docker notes**

CrossEncoder may require additional model cache mount and longer startup.

**Acceptance Criteria:**
- Each quality feature can be enabled/disabled independently.
- Docker default remains stable.

---

## Implementation Order

Recommended order:

1. Task 1 — Evaluation Baseline v2
2. Task 2 — Question Segmentation
3. Task 3 — Hybrid Retrieval
4. Task 4 — Hierarchy Utilities
5. Task 5 — Heuristic Reranker first, CrossEncoder optional second
6. Task 6 — Strict LLM Validation
7. Task 7 — Confidence Calibration
8. Task 8 — Hard Negatives
9. Task 9 — Debug UI/API
10. Task 10 — Docs and feature flags

Why this order:
- Measure first.
- Improve recall before LLM prompt work.
- Add validation before relying on new candidate pools.
- Calibrate confidence after more reliable scores exist.
- Fine-tune after logging richer hard-negative data.

---

## Success Metrics

Minimum target on `tests/fixtures/test_appeals.json` and expanded validation set:

| Metric | Current v1 | Target EPIC-08 |
|---|---:|---:|
| Вид обращения accuracy | 90% | >= 90% |
| Level-1/prefix accuracy | 90% | >= 90% |
| Exact Top-1 | 40% | >= 65% |
| Exact Top-3 | 40% | >= 80% |
| Retrieval recall@50 | unknown | >= 90% |
| Invalid LLM code rate | unknown | 0% |
| Needs verification rate | 0% but uncalibrated | 15-35% calibrated |
| Average latency | 5 sec | <= 8 sec default mode |

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| CrossEncoder too slow on CPU | Keep disabled by default; heuristic reranker as baseline |
| BM25 hurts semantic recall | Feature flag + weighted merge + evaluation before enabling |
| LLM still chooses broad parent codes | Strict prompt + hierarchy validation + deeper-code preference |
| More candidates increase LLM token cost | Rerank top-50 to final top-10 before LLM |
| Fine-tuning overfits small verified set | Use validation split, historical data, hard negatives, reports |
| Ground truth too strict / ambiguous | Track path distance and multiple acceptable codes |

---

## Spec Coverage Check

- [x] Multi-question appeals → Task 2
- [x] Candidate recall weakness → Task 3
- [x] Close leaf-code confusion → Tasks 4, 5
- [x] LLM output reliability → Task 6
- [x] Overconfident wrong predictions → Task 7
- [x] Operator corrections as training signal → Task 8
- [x] Explainability/debuggability → Task 9
- [x] Safe rollout → Task 10

**No placeholder scan:** No TBD/TODO placeholders. All tasks have concrete files, steps, flags, and acceptance criteria.


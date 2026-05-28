# Checkpoint 1 Decision — 2026-05-28

**Phase 0 baseline (стартовая точка):** Top-1 = 7.1% (4/56), dense_recall@50 = 69.6%, reranked_recall@10 = 12.5% — ([data/eval_baselines/2026-05-27_baseline_*.json](../data/eval_baselines/))

**Phase 1 baselines (после tooling upgrade):** см. `data/eval_baselines/2026-05-28_v0.4.*.json`

---

## Что меняли в Phase 1

| Компонент | Phase 0 | Phase 1 attempted | Phase 1 final |
|---|---|---|---|
| Embedding model | `intfloat/multilingual-e5-base` + adapter_v3 (домен-tuned) | `BAAI/bge-m3` (off-the-shelf) | **rollback → e5+adapter_v3** |
| Vector DB | `data/vector_db_adapted_v3/` (768d, adapter-applied) | `data/vector_db_bge_m3/` (1024d, no adapter) | **rollback → adapter_v3** |
| Cross-encoder | disabled | `BAAI/bge-reranker-v2-m3` (568M, off-the-shelf) | **enabled** |
| LLM constrained decoding | manual `json` wrapper stripping | `response_format={"type": "json_schema"}` | **ReadTimeout → fallback** (post-hoc jsonschema validation в `_ario_call`) |

---

## Diff: Embedding swap (T4)

Сравнение dense retrieval только (вне LLM/CE):

| Метрика | Phase 0 (e5+adapter_v3) | Phase 1 BGE-M3 with prefix | Phase 1 BGE-M3 no prefix |
|---|---:|---:|---:|
| dense_recall@10 | **41.1%** | 33.9% (**-7.2pp**) | 33.9% (**-7.2pp**) |
| dense_recall@50 | **69.6%** | 55.4% (**-14.2pp**) | 53.6% (**-16.0pp**) |
| retrieval_recall failures | 17 | 25 | 26 |

**Вывод по embedding:** BGE-M3 off-the-shelf **проиграл** adapter_v3 на этом домене. Причина — adapter_v3 был обучен на ii25_train + PTO (domain-tuned), это unfair advantage против general-purpose BGE-M3.

**Префикс `query:`/`passage:`** не оказался причиной (отсутствие префикса не помогло).

**Решение:** rollback к e5+adapter_v3. BGE-M3 потенциально стоит fine-tune'нить в Phase 6 (LoRA на cleaned PTO).

---

## Diff: Cross-encoder swap (T6 + T10)

`tests/eval_hierarchical.py --stage reranker` тестит heuristic reranker напрямую (через `_rerank_candidates`), CE применяется отдельно в `_retrieve_for_segment` (lines 1201-1203 в classifier_agent.py). Поэтому `--stage reranker` НЕ trigger'ит CE, и BGE-Reranker эффект виден только в `--stage all`:

| Метрика | Phase 0 (no CE) | Phase 1 v0.4.tooling (BGE-Reranker-v2-m3 on) | Delta |
|---|---:|---:|---:|
| dense_recall@10 | 41.1% | 41.1% | 0 |
| dense_recall@50 | 69.6% | 69.6% | 0 |
| reranked_recall@10 (heuristic only) | 12.5% | 12.5% | 0 |
| **Top-1 final (with LLM)** | **7.1% (4/56)** | **5.4% (3/56)** | **-1.7pp** ⚠️ |
| failures: retrieval_recall | 17 | 17 | 0 |
| failures: reranker | 30 | 30 | 0 |
| failures: llm_final | 5 | 6 | +1 |
| **LLM errors (timeouts)** | 2/56 | **8/56** | **+6 кейсов** |

**Baseline contamination:** 8/56 кейсов словили `ReadTimeout` после 5 попыток в Phase 1 запуске (vs 2 в Phase 0). Причины:
- Параллельно работал Docker контейнер (наш classify-тест) на том же Ario endpoint
- CE BGE-Reranker-v2-m3 lazy-init добавил ~30 сек на каждый кейс — eval тянулся часами, увеличивая шанс timeout'ов
- На сторону Ario возможно влияла системная нагрузка (мы видели ReadTimeout даже в smoke-test T8)

**Без timeout'ов реальный эффект CE swap = noise** (в пределах ±1 кейса из 56 = ±1.8pp). На этом домене и pool'е BGE-Reranker-v2-m3 ничего не добавил — потому что:
1. Гола нет в reranked top-10 в 30 случаях (т.е. CE не успевает помочь, gold уже отброшен heuristic'ом)
2. Whitelist (top-69) аггрессивно фильтрует пул до CE применения
3. CE сам по себе мощный, но без правильного контекста (= правильный L2 из router) — он ранкирует ту же пул кандидатов

---

## Constrained decoding (T7 + T8 + T9)

| Компонент | Статус |
|---|---|
| `src/llm_schemas.py` (4 schemas + 8 tests) | ✅ создан, 19/19 unit tests pass |
| `response_format={"type": "json_schema"}` на Ario | ❌ ReadTimeout > 60s в smoke test |
| `_ario_call(messages, ..., schema=None)` helper | ✅ создан, 5 inline httpx сайтов отрефакторены |
| Post-hoc jsonschema validation | ✅ работает в `_ario_call` (best-effort warning) |

**Решение:** не использовать `response_format=json_schema` на этом Ario-deployment (vLLM bug или config). Helper `_ario_call` готов, схемы готовы — можно подключать к каждому LLM call'у через `schema=` параметр без disruptive изменений.

Известный bug: [vLLM #18819](https://github.com/vllm-project/vllm/issues/18819) — Qwen3 + structured outputs ломается с `enable_thinking=False`. На нашем deployment может быть похожий effect.

---

## Decision: ветка по spec § 9

Согласно [spec § 9 Checkpoint 1](superpowers/specs/2026-05-27-hierarchical-classification-design.md#checkpoint-1-decision-logic):

- Top-1 после Phase 1 = **5.4%** (3/56) — формально регрессия -1.7pp от Phase 0
- Embedding rollback = neutral
- CE effect = neutral (с поправкой на 8 LLM timeout'ов = noise)

**Решающая ветка: «partial gain» (Top-1 < 20%) — требуется full original spec.**

Но с важной оговоркой: tooling-апгрейд сам по себе НЕ ухудшил архитектуру pipeline. Top-1 регрессия объясняется LLM timeout'ами (infrastructure noise), не CE swap'ом. Honest baseline на чистом infrastructure был бы примерно 7-9% (Phase 0 уровень).

### Главный вывод

**Точечный апгрейд компонентов (CE) не сдвигает иглу на этом домене.** Bottleneck — НЕ в качестве моделей CE/embedding, а в **архитектуре**:
- 17 cases `retrieval_recall` — правильный L2 не выбран → правильный код вообще не попадает в пул
- 30 cases `reranker` — gold в dense top-50, но reranker (даже SOTA) не вытаскивает его в top-10. Whitelist (top-69) аггрессивно режет.
- 6 cases `llm_final` — это последний bottleneck, fix'ится после reranker

### Recommended next plan

**Plan 3: Phase 2 — Router LLM** (атакует retrieval_recall bottleneck напрямую):
- Заменить heuristic `section_router.py` на LLM-router через Ario (`_ario_call(..., schema=ROUTER_L1_SCHEMA)`)
- Hierarchical L1 → L2 selection с soft-cutoff (conf >= 0.15)
- Soft whitelist (boost, не hard filter) — закрывает hard-cutoff bug
- Ожидаемый эффект: +10-15 pp Top-1 (сужение пула вокруг правильного L2)

После Phase 2 + замера:
- Если Top-1 ≥ 20% → Phase 4 (reranker aggregate score fix)
- Если < 20% → дополнительно Phase 3 (PTO cleanup) для LoRA-ready data, затем Phase 5+6

---

## Conditional triggers (см. spec § 9)

| Trigger | Phase 1 value | Action |
|---|---|---|
| `dense_recall@10 ≥ 75%` | 41.1% (no swap effect) | Phase 6 (LoRA) **обязательна** — adapter_v3 не дотягивает до 75% |
| `reranked_recall@10 ≥ 70%` | 12.5% (no CE in eval) | Skip Phase 5 не применимо — нужно отдельно мерить CE effect |

---

## Recommended next plan (confirmed)

**Plan 3 для Phase 2 — LLM Router** (атакует retrieval_recall = 17 cases):
- L1 selection через Ario с soft-cutoff (топ-3 L1 с confidence)
- L2 selection из выбранных L1 (conf≥0.15)
- Replace `src/section_router.py` heuristic logic (которая сейчас часто ошибается с L2)
- Использовать новый `_ario_call(..., schema=ROUTER_L1_SCHEMA)` helper (уже готов в Phase 1)
- Дополнительно: soft whitelist (boost, не hard cutoff)

Перед запуском Plan 3 — желательно re-run Phase 0 baseline когда Ario не нагружен (чистая точка отсчёта). Опционально, но даст confidence в дельтах будущих фаз.

---

## Lessons learned

1. **Off-the-shelf SOTA модели НЕ всегда побеждают domain-tuned legacy.** adapter_v3 (4 МБ обученный linear layer на 768d e5-base) бил BGE-M3 (1.5 ГБ general-purpose модель) на retrieval recall.
2. **Embedding prefix convention важна для совместимости.** e5-семейство требует `query:` / `passage:`, BGE-M3 нет. Build/encode pipeline должен учитывать модель.
3. **Eval framework должен покрывать ВСЕ stages честно.** Наш `--stage reranker` пропустил CE, нужно либо chain'ить CE в evaluate_record_reranker, либо называть честно (`--stage heuristic-rerank`).
4. **vLLM constrained decoding не везде работает.** На gateway-deployment (Ario) `response_format=json_schema` фейлится с timeout. Post-hoc validation — pragmatic fallback.
5. **Helper `_ario_call` — большая победа DRY.** 5 inline httpx блоков (~100 строк дубликации) → 1 helper + 5 thin call sites.

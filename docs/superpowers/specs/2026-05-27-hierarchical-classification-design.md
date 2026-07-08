# Hierarchical Classification Pipeline — Design Spec

**Date:** 2026-05-27 (revised after Phase 0 baseline + SOTA tooling research)
**Project:** Class-OG-Final
**Status:** Approved (Phase 0 complete; Phase 1 tooling upgrade pending)
**Author:** Claude (Opus 4.7) in brainstorming session
**Target:** улучшение Top-1 accuracy с **7.1% (честный Phase 0 baseline)** до ≥45% на ii25_test

**Revision history:**
- 2026-05-27 v1: исходный дизайн (предполагалось Top-1 ~25% baseline, target +20pp)
- 2026-05-27 v2: после Phase 0 baseline (Top-1 = 7.1% реально, target +38pp) — добавлены Phase 1 Tooling Upgrade и Checkpoint 1

---

## 1. Контекст и проблема

AI-агент классифицирует обращения граждан по 4-уровневому Общероссийскому классификатору (2108 leaf-кодов). Текущий pipeline: heuristic L2-router → dense retrieval → CE reranker → LLM final pick.

### Диагноз провалов на ii25_test (56 кейсов)

| Стадия | Текущее | Что происходит |
|---|---:|---|
| Dense retrieval @50 | 69.6% | Адаптер v3 даёт ОК пул |
| Reranker → Top-1 | 3.6% | **Reranker рушит порядок** |
| Reranker → Top-5 | 7.1% | Корректный код выпадает |
| LLM Top-1 (финал) | ~25% | Иногда LLM вытаскивает из top-30 |

**Три провала:**
1. **Router выбирает неверный L2** → правильного кода нет в пуле (≈50% случаев)
2. **Dense recall@30 не докатывает** (TOP_K_CANDIDATES=30 режет правильные коды на ранге 31-50)
3. **CE reranker анти-коррелирует с dense** — использует только `similarity`, игнорируя lexical/hier/LtR (см. [src/reranker.py:104](../../../src/reranker.py))

### Дополнительный фактор — зашумлённые данные

PTO-датасет (6502 записи) имеет один код на мульти-темный текст. Например, в классе `0001.0001.0011.0038` («Закон о Тишине») лежат тексты про прокуратуру, ЖКХ, молодёжный парламент. Файл [data/pto_hard_classes.json](../../../data/pto_hard_classes.json) показывает: даже специализированный supervised multi-label классификатор Ario достигает F1<0.5 на 17+ классах.

---

## 2. Цель и acceptance criteria

**Главная цель:** Top-1 accuracy на ii25_test ≥ 45%, при условии не-регрессии на soft-метриках.

**Подцели по фазам:**
- Router L1 accuracy ≥ 85%, L2 accuracy ≥ 70%
- Dense recall@30 (внутри L2-фильтра) ≥ 80%
- Aggregate reranker top-10 ≥ 70%
- L1-prefix accuracy финала ≥ 80% (текущее 23.2%)

**Holdout:** `data/ii25_test.jsonl` (56 кейсов) — не используется в обучении, только финальный замер.

**Бизнес-домен:** PTO (Directum 360). ii25 — общий бенчмарк.

---

## 3. Архитектура

### 3.1 Высокоуровневая схема

```
┌────────────────────────────────────────────────────────────────┐
│                    OFFLINE (один раз)                           │
├────────────────────────────────────────────────────────────────┤
│  1. PTO Cleanup (Ario LLM)                                      │
│     6502 raw → multi-label re-annotation → cleaned_pto.jsonl    │
│                                                                  │
│  2. LoRA fine-tune e5-base на cleaned_pto + ii25_train          │
│     → models/e5-ru-pto-v1/  (LoRA adapter, ~10-20 MB)           │
│                                                                  │
│  3. Rebuild vector_db с новой моделью                           │
│     → data/vector_db_v4/                                        │
│                                                                  │
│  4. Train CE reranker на (appeal, code) парах                   │
│     → models/cross-encoder-v2/                                  │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                    ONLINE (на каждое обращение)                 │
├────────────────────────────────────────────────────────────────┤
│  Appeal text                                                    │
│    │                                                            │
│    ├─ [Stage 1: Ario] Топ-3 L1 разделов с confidence            │
│    │                                                            │
│    ├─ [Stage 2: Ario] L2 опции (soft-cutoff по L1 conf >= 0.15)│
│    │                                                            │
│    ├─ [Stage 3: Retrieval + Reranker]                           │
│    │    Dense (внутри L2) → BM25 → hier_boost → CE              │
│    │    aggregate = α·dense + β·lex + γ·hier + δ·CE             │
│    │    → top-10 для финала                                     │
│    │                                                            │
│    └─ [Stage 4: Ario] Финальный leaf из top-10                  │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 Компоненты (модули)

| Файл | Назначение | Статус |
|---|---|---|
| `scripts/clean_pto_with_ario.py` | Batch re-annotation PTO через Ario | Новый |
| `src/hierarchical_router.py` | Stage 1+2 LLM-routing | Новый |
| `src/reranker.py` | Aggregate score (не сырой similarity) | Изменён |
| `src/classifier_agent.py` | Интегрирует новый pipeline | Изменён |
| `scripts/finetune_e5_lora.py` | LoRA fine-tune e5 на cleaned data | Новый |
| `scripts/train_ce_v2.py` | Train CE на cleaned pairs + hard negatives | Новый |
| `tests/eval_hierarchical.py` | Eval с per-stage метриками и failure attribution | Новый |

### 3.3 Точки интеграции

- Все LLM-вызовы (Stage 1, 2, 4, PTO cleanup) идут через **Ario** (`ARIO_BASE_URL=https://gpt.ario.directum360.ru/v1`, модель `Qwen/Qwen3-32B-AWQ`). Уже интегрирована в [src/classifier_agent.py:384](../../../src/classifier_agent.py).
- Существующий `appeals_logger.py` сохраняется без изменений
- `api_server.py` меняется минимально (только endpoint вызывает новый pipeline)

---

## 4. Phase Offline-1 — PTO Cleanup через Ario

### Задача
6502 raw PTO записи → cleaned multi-label dataset.

### Алгоритм

```
Для каждой PTO записи:
  1. Pre-process текст:
     - Убрать base64, длинные URL, повторяющиеся email-цепочки
     - Truncate до 3000 символов
     - Сохранить оригинал в appeal_text_raw, чистый в appeal_text_clean
  2. Dense retrieval → top-50 candidate codes (full_path + name)
  3. Ario получает: clean text + 50 кандидатов + current assigned_code (как hint)
  4. Промпт: "Какие коды применимы? Допускается несколько.
              Для каждого: confidence (0-1), reasoning."
  5. Output JSON:
     {
       "primary_code": "...",
       "primary_confidence": 0.85,
       "secondary_codes": [{"code": "...", "confidence": 0.6}, ...],
       "rejected_assigned": true|false,
       "reasoning": "..."
     }
```

### Управление качеством
- **Sample validation:** после первых 100 записей скрипт делает паузу и выводит 20 случайных кейсов в `data/pto_cleanup_sample.md`. Пользователь ревьюит ручным глазом, accept/reject. Если accept rate < 80% — корректируем промпт и перезапускаем с нуля.
- **Confidence gate:** для fine-tuning используем только `primary_conf >= 0.7` (≈70-80% от датасета, оценка)
- **Human-in-the-loop ревью:** записи с `max_conf < 0.5` идут в `data/pto_uncertain.jsonl` и ревьюются через operator_cli (отдельная команда `python src/operator_cli.py --mode review-uncertain`)
- **Чекпоинты:** каждые 100 записей в `data/pto_cleaned.checkpoint.jsonl`, можно прерывать/возобновлять

### Артефакты
- `data/pto_cleaned.jsonl` — основной clean dataset
- `data/pto_uncertain.jsonl` — для ручного ревью
- `data/pto_cleanup_report.md` — статистика: % отвергнутых, распределение conf

### Цена и время
- ~6502 × ~3к токенов prompt + ~500 output ≈ 23M токенов
- Ario внутренний, лимитов нет
- Время: 3-15 часов фоном с чекпоинтами

---

## 5. Online Stage 1+2 — Hierarchical Routing через Ario

### Stage 1 — L1 selection
**Input:** clean text (4000 символов) + 5 L1 описаний с 3-5 примерами каждый из cleaned PTO.
**Output:** топ-3 L1 с confidence: `{"l1_ranked": [{"code": "0003", "conf": 0.7}, ...]}`

### Stage 2 — L2 selection (soft-cutoff)
Пул L2-опций строится из L1 с **conf >= 0.15** (топ-3 типично, но фильтруем низкоуверенные):
- L1 топ-1 (conf 0.7): все его L2 (4-8 опций)
- L1 топ-2 (conf 0.25): добавляем его L2 (только если conf >= 0.2)
- L1 топ-3 (conf 0.05): пропускаем (conf < 0.15)

Итого ~5-25 L2 опций для Stage 2.

Промпт включает **примеры sibling-разграничения** для трудных пар (автоматически строятся из cleaned PTO):
- 0096 (новое строительство) vs 0097 (благоустройство)
- 0055 (жилищные программы) vs 0056 (коммунальные услуги)

### Stage 3 — фильтрация retrieval-пула
```
filtered = [c for c in vector_db if c.code.startswith(any l2_codes prefix)]
if len(filtered) < 30: расширяем sibling L2 из тех же L1
if len(filtered) > 500: dense retrieval только внутри filtered
```

### Fallback логика
- Невалидный JSON / несуществующий код → откат к полному пулу из 2108
- Confidence < 0.4 на обоих stages → расширяем до топ-5 sibling L2

### Метрики для отладки
- `stage1_l1_picked`, `stage1_l1_correct`
- `stage2_l2_picked`, `stage2_l2_correct`
- `stage3_pool_size`, `stage3_gold_in_pool` (булево)

---

## 6. Online Stage 3 — Retrieval + Reranker fix

### Новый pipeline

```
filtered_pool (от Stage 2)
  │
  1. Dense retrieval → dense_top_50 (внутри filtered)
  2. Lexical (BM25): lex_score = bm25(query_tokens, code.search_text)
  3. Hierarchy boost: 1.0 если в L2 filtered, 0.5 если в sibling L2
  4. Берём top-30 по preliminary_score = 0.5·dense + 0.3·lex + 0.2·hier
  5. CE reranker: ce_score = sigmoid(CE.predict([(query, code_full_path), ...]))
  6. Aggregate final = α·dense + β·lex + γ·hier + δ·ce
     (α=0.25, β=0.15, γ=0.15, δ=0.45 как старт; tune через grid search на ii25_train)
  7. Top-10 → Stage 4 (LLM)
```

### Изменения в reranker.py
```python
# БЫЛО (src/reranker.py:104):
heuristic_scores = [float(c.get("similarity", 0.0)) for c in head]

# СТАЛО:
heuristic_scores = [
    0.5 * float(c.get("dense_score", 0.0))
    + 0.3 * float(c.get("lex_score", 0.0))
    + 0.2 * float(c.get("hier_boost", 0.0))
    for c in head
]
```

### Whitelist — soft boost, не hard filter
- `allowed_codes_top69.json` → boost к hier_score (×1.5), не cutoff
- Никаких искусственных `similarity=0.5` direct-fetch кандидатов

### CE training (Phase Offline-4)
- Базовая модель: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (продолжаем от существующей)
- Positives: из `pto_cleaned.jsonl` с `primary_conf >= 0.7`
- Hard negatives: top-5 sibling codes из того же L2
- Random negatives: 10 рандомных из других L1
- Loss: BCE, 3 epochs
- Output: `models/cross-encoder-v2/`

---

## 7. Offline Phase 5 — LoRA fine-tune e5 на CPU

### Параметры (CPU-bound + real-data only)

| Параметр | Значение |
|---|---|
| Метод | LoRA (`peft` library) |
| target_modules | `["query", "key", "value", "dense"]` (sentence-transformers HF слои) |
| Rank | 8 (минимальный для anti-overfit) |
| Alpha | 16 |
| Dropout | 0.1 |
| Batch size | 8 + gradient_accumulation_steps=4 |
| Epochs | 2 с early stopping |
| LR | 2e-5 |
| Weight decay | 0.01 |
| Время CPU | ~8-15 ч |
| Артефакт | base + LoRA adapter (10-20MB) |

### Защита от overfit
- 30% negatives в батче — passages не из 31 PTO класса (рандом из 2108)
- Early stopping по recall@10 на ii25_train holdout (не на PTO val)
- Если LoRA даёт хуже adapter_v3 → откат к adapter_v3

### Search_text расширение в build_vectordb.py
Сейчас `search_text` = name + path. Расширяем формат (изменение в [src/build_vectordb.py](../../../src/build_vectordb.py)):

```python
# БЫЛО: search_text = f"{entry['name']} {entry['full_path']}"
# СТАЛО:
search_text = (
    f"{entry['name']}. "
    f"Категория: {'; '.join(entry['full_path_names'])}. "
    f"{sibling_synonyms_optional}"
)
```

где `sibling_synonyms_optional` — топ-3 «синонимических» текстов из cleaned PTO для этого кода (если есть). Если PTO не покрывает код — пропускаем эту часть.

### E5 prefix convention (сохраняем)
- Query: `"query: " + appeal_text_clean`
- Passage: `"passage: " + code.search_text`

### Артефакты
- `models/e5-ru-pto-v1/` — base + LoRA adapter
- `models/e5-ru-pto-v1/training_metrics.json`
- `data/vector_db_v4/` — пересобранный db
- `.env` update: `EMBEDDING_MODEL=models/e5-ru-pto-v1`, `VECTOR_DB_DIR=data/vector_db_v4`

---

## 8. Evaluation Framework

### Декомпозированные метрики

| Метрика | Phase 0 Baseline (измерено 2026-05-27) | После Phase 1 (ожидание) | Final Target | Источник |
|---|---:|---:|---:|---|
| Stage 1 L1 accuracy (если измерим heuristic) | ~50% (косвенно) | n/a (если skip Phase 2) | >85% | Phase 2 |
| Stage 2 L2 accuracy | ~30% | n/a | >70% | Phase 2 |
| L2 pool contains gold | ~60% | n/a | >90% | Phase 2 |
| **dense_recall@10 (full pool)** | **41.1%** | ~55-65% | >75% | Phase 1+6 |
| dense_recall@50 (filtered) | 69.6% | ~80-85% | >90% | Phase 1+2+6 |
| aggregate_top1 (no LLM) | 3.6% | ~15-25% | >35% | Phase 1+4+5 |
| aggregate_top5 (no LLM) | 7.1% | ~30-45% | >65% | Phase 1+4+5 |
| **reranked_recall@10** | **12.5%** | ~35-45% | >70% | Phase 1+4+5 |
| **Top-1 final (с LLM)** | **7.1%** ⚠️ (не 25% как заявлено ранее) | ~20-30% | >45% | All phases |
| Top-3 final | TBD | ~35-50% | >70% | All phases |
| L1 prefix accuracy | 23.2% | ~50-65% | >80% | All phases |
| L2 prefix accuracy | 16.1% | ~40-55% | >75% | All phases |

**Колонка "После Phase 1"** — ожидания от tooling-апгрейда без остальных архитектурных фаз. Если реальность сильно лучше — applies Checkpoint 1 "breakthrough" ветка (см. § 9).

### Eval-скрипт

```
tests/eval_hierarchical.py [--stage router|retrieval|reranker|final|all]
```

### Per-case логирование
`eval_results/YYYY-MM-DD_<stage>.jsonl` — для каждого кейса полная трассировка плюс `failure_attribution`:
- `router_stage1` — gold L1 не в top-3
- `router_stage2` — gold L1 ok, gold L2 не в picked
- `retrieval_recall` — gold L2 ok, gold leaf не в dense top-50
- `reranker` — gold leaf в dense top-50, но не в aggregate top-10
- `llm_final` — gold leaf в aggregate top-10, но LLM ошиблась
- `correct` — Top-1 ok

### Test sets

| Файл | Назначение |
|---|---|
| `data/ii25_test.jsonl` (56) | Holdout — только финал |
| `data/ii25_train.jsonl` (43) | Validation — tune весов, early stopping |
| `data/pto_cleaned.jsonl` val split (~450) | LoRA + CE validation |
| `data/test_sample_pto.jsonl` (11) | Sanity (noisy labels — пометка) |

---

## 9. Rollout plan (v2 — после SOTA tooling research)

```
Phase 0: Eval Framework           [Дни 1-2]   ✓ ЗАВЕРШЕНО 2026-05-27
   ↓
Phase 1: Tooling Upgrade          [Дни 3-6]   ⭐ НОВАЯ — НАЧИНАЕМ ОТСЮДА
   1.1 BGE-M3 embedding swap (rebuild vector_db)
   1.2 BGE-Reranker-v2-m3 swap (off-the-shelf)
   1.3 Constrained decoding (response_format JSON schema) во всех LLM-вызовах
   1.4 Baseline eval с новыми инструментами на ii25_test
   ↓
═══ CHECKPOINT 1: Decision Gate ═══
   Замеряем Top-1, recall@K, attribution на новом стэке.
   Принимаем решение о следующих фазах (см. § "Checkpoint 1 logic" ниже).
   ↓
   (Дальнейшие фазы условны)
   ├─ Phase 2: Router LLM          [Дни 7-9]   ─┐ если Checkpoint 1 показал
   └─ Phase 3: PTO Cleanup         [Дни 7-11]   │ необходимость
                                                 │
   ── Gate 2: Router L1 acc ≥ 70% ───────────────┘
   ↓
   Phase 4: Reranker aggregate score fix [Дни 10-12]
   ↓
   ── Gate 3: aggregate_top10 ≥ 50% ──
   ↓
   Phase 5 (CONDITIONAL): CE training [Дни 12-14]
       Отменяется если BGE-Reranker-v2-m3 off-the-shelf даёт recall@10 ≥ 70%
   ↓
   Phase 6 (CONDITIONAL): LoRA fine-tune на BGE-M3 [Дни 14-18]
       Отменяется если Phase 1 даёт recall@10 ≥ 75% off-the-shelf
   ↓
   Phase 7: Integration & Final Eval [Дни 18-20]
   ↓
   Phase 8 (опц): Operator UI       [День 21]
```

### Checkpoint 1 Decision Logic

После завершения Phase 1.4 (eval на новом стэке) — три ветки:

| Top-1 после Phase 1 | Решение | Что делаем дальше |
|---|---|---|
| **≥ 35%** ("breakthrough") | Tooling — главный bottleneck. Архитектурные фазы — опционально. | Только Phase 2 (router) + Phase 4 (reranker score fix) + Phase 7 (integration). Skip Phase 3 (PTO cleanup), 5 (CE), 6 (LoRA). Бюджет: +5 дней до финала. |
| **20-35%** ("strong gain") | Tooling помогло, но архитектура тоже нужна. | Все Phases 2-7 в полном объёме, но Phase 5/6 conditional. Бюджет: +10-12 дней. |
| **< 20%** ("partial gain") | Tooling не закрывает gap. Нужна полная архитектурная перестройка. | Full original spec: все Phases 2-7. Бюджет: +14-16 дней. |

**Дополнительные условные триггеры (внутри ветки):**
- `dense_recall@10 ≥ 75%` после Phase 1 → Skip Phase 6 (LoRA не нужен)
- `reranked_recall@10 ≥ 70%` после Phase 1 → Skip Phase 5 (CE training не нужен)
- `Stage1 L1 accuracy (если измерим heuristic) < 50%` → Phase 2 обязательна
- Все failure-категории распределены равномерно → Phase 3 (PTO cleanup) обязательна (для широкого fine-tuning сигнала)

### Gate fallbacks

| Gate | Если не прошли | Откат |
|---|---|---|
| Checkpoint 1 (Top-1 не вырос на ≥ 5pp) | Откат к Phase 0 baseline. Анализ почему tooling не помог. | Возврат к старому стэку (e5-base + mmarco-CE) и пересмотр spec'а |
| Gate 2 (Router L1 < 70%) | Heuristic-router back. Phase 3 продолжаем | |
| Gate 3 (aggregate_top10 < 50%) | Откат aggregate weights к defaults | |

### Git workflow
- Feature branch на каждую фазу: `phase-1-tooling-upgrade`, `phase-2-llm-router`, ..., `phase-6-lora`
- Merge в `main` только после GO на eval
- Tag `v0.4.<phase>` после каждого merge
- Phase 0 уже на ветке `phase-0-eval-framework` (статус: kept locally, см. HANDOFF.md)

### Артефакты репо
- `docs/superpowers/specs/2026-05-27-hierarchical-classification-design.md` (этот файл)
- `docs/superpowers/plans/2026-05-27-hierarchical-classification-plan.md` (генерится `writing-plans`)
- `docs/HANDOFF.md` (обновляется)
- `eval_results/2026-MM-DD_*.md`

---

## 10. Risks & mitigations

| Риск | Вероятность | Митигация |
|---|---|---|
| LoRA на CPU не сходится за 15 ч | Средняя | Остаёмся на adapter_v3, не блокирует Phase 1-4 |
| Ario throughput даёт меньше ожидаемого на batch 6502 | Низкая | Чекпоинты каждые 100, можно растянуть на 2-3 дня |
| LLM Stage 4 переучивается на формат и теряет редкие коды | Низкая | Per-L2 examples в промпте, негативные few-shot |
| Per-stage metrics ухудшаются после изменения, но Top-1 растёт | Низкая | Принимаем (Top-1 — главная метрика) |
| Регрессия на ii25 после фитинга на PTO | Средняя | Holdout-only ii25_test, validation на ii25_train |

---

## 11. Open questions

- (Открыт) GPU-доступ в перспективе — если появится, можем перейти от LoRA к full fine-tune
- (Открыт) Дополнительные источники annotated data — есть ли в Directum 360 архивы verified классификаций?
- (Открыт) Synthetic generation для редких кодов — пока решили не делать, но может стать опцией если LoRA даёт уверенные результаты на covered classes и плохой transfer на uncovered

---

## 12. Acceptance for spec

Этот документ — design contract. Перед переходом к writing-plans пользователь подтверждает:
- [x] Архитектура корректна (utwerждено 2026-05-27)
- [x] Phase boundaries и gate criteria приемлемы (v1 + v2 с Checkpoint 1 утверждено)
- [x] Acceptance criteria (Top-1 ≥ 45%) реалистичны (теперь от honest baseline 7.1%, не от 25%)
- [x] Используем Ario для всех LLM-задач (confirmed)
- [x] CPU + real-data only для embedding fine-tune (confirmed)
- [x] **NEW (v2):** Tooling upgrade перед всеми архитектурными фазами + Checkpoint 1 (утверждено 2026-05-27)

---

## 13. Phase 1 — Tooling Upgrade (детали)

Точечный апгрейд 3 компонентов до SOTA (2024-2026). Не refactoring архитектуры — компонентная модернизация. Делается ПЕРЕД всеми архитектурными фазами, чтобы baseline для последующих measurement был на современном стэке.

### 13.1 Embedding swap: `intfloat/multilingual-e5-base` → `BAAI/bge-m3`

**Обоснование:**

| Параметр | e5-base (наш) | BGE-M3 |
|---|---|---|
| Год | Dec 2023 | Q2 2024 |
| Размер | 278M | 568M |
| ruMTEB Retrieval | ~67-70 (оценка) | **74.8** |
| ruMTEB Reranking | ~63 | **69.7** |
| ruMTEB Avg | ~57 | **60.8** |
| Лицензия | MIT | MIT |
| Multi-vector | dense only | **dense + sparse + multi-vec** одновременно |

**Изменения в коде:**
- `.env`: `EMBEDDING_MODEL=BAAI/bge-m3`
- `src/build_vectordb.py`: пересобрать `data/vector_db_bge_m3/`
- `src/config.py`: `VECTOR_DB_DIR=data/vector_db_bge_m3`
- `src/classifier_agent.py:_search_candidates`: остаётся без изменений (использует API `SentenceTransformer.encode`)
- **CRITICAL:** Сохранить старую DB (`data/vector_db_adapted_v3`) для rollback
- ENV adapter переменные: `ENABLE_EMBEDDING_ADAPTER=false` (BGE-M3 уже сильна на русском без адаптера)

**Время:** 1-2 дня (включая пересборку DB ~30-60 мин на CPU, smoke-test)

### 13.2 Cross-encoder swap: `mmarco-mMiniLMv2` → `BAAI/bge-reranker-v2-m3`

**Обоснование:**

| Параметр | mmarco (наш) | BGE-Reranker-v2-m3 |
|---|---|---|
| Год | ~2022 | Q2 2024 |
| Размер | 117M | 568M |
| MIRACL multilingual | базовый | **SOTA для open-source** |
| Russian quality | средне | **сильно лучше** |
| Лицензия | Apache 2.0 | Apache 2.0 |

**Изменения в коде:**
- `.env`: `RERANKER_MODEL=BAAI/bge-reranker-v2-m3`
- `src/config.py`: добавить `RERANKER_MODEL` если ещё не вынесено
- `src/reranker.py`: интерфейс `CrossEncoder.predict` тот же, замена прозрачная
- **Smoke-test:** запустить eval `--stage reranker` сразу после swap — увидеть delta

**Время:** 0.5 дня (модель загружается лениво при первом use)

### 13.3 Constrained decoding: native vLLM `response_format` JSON schema

**Обоснование:**
- Ario — vLLM-backend, поддерживает OpenAI-compatible `response_format={"type": "json_schema", ...}`
- Гарантирует **физически невозможный** invalid JSON или неверный код от LLM
- Убирает категорию ошибок "LLM_invalid_format" целиком
- Применяется к: Stage 1 router, Stage 2 router, Stage 4 final pick, PTO cleanup, query expansion

**Caveat (известный):** [vLLM issue #18819](https://github.com/vllm-project/vllm/issues/18819) — bug с `enable_thinking=False` для Qwen3. Митигация:
1. Smoke-test: один вызов с `response_format` и `enable_thinking=True` (default)
2. Если bug проявляется — fallback: грамматика в промпте + JSON-парсинг как сейчас
3. Замерить delta на 5-10 кейсов до коммита

**Изменения в коде:**
- `src/classifier_agent.py`: где сейчас Ario вызывается через httpx (lines ~1073-1080, ~1327-1334) — добавить `response_format` параметр со схемой
- Удалить ```json wrapper-stripping код (lines 1076-1080, 1330-1334) — больше не нужен
- Создать `src/llm_schemas.py` с JSON-схемами для каждого типа вызова:
  - `ROUTER_L1_SCHEMA` — для Stage 1
  - `ROUTER_L2_SCHEMA` — для Stage 2
  - `FINAL_PICK_SCHEMA` — для Stage 4
  - `PTO_CLEANUP_SCHEMA` — для PTO re-annotation

**Время:** 1-2 дня (включая smoke-test, fallback логику)

### 13.4 Baseline replay с новым стэком

После 13.1+13.2+13.3:

```bash
# Пересобрать vector_db
./venv/Scripts/python.exe src/build_vectordb.py

# Прогнать все 3 baseline stages с тегом v0.4.tooling
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage retrieval --tag v0.4.tooling --save-baseline
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage reranker --tag v0.4.tooling --save-baseline
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage all --tag v0.4.tooling --save-baseline
```

Результаты сравнить с `data/eval_baselines/2026-05-27_baseline_*.json`. Сформировать `docs/checkpoint1_decision.md` с:
- Diff таблицей (Phase 0 vs Phase 1 metrics)
- Failure attribution shift
- Решение (breakthrough / strong gain / partial gain) с обоснованием
- Список фаз, которые продолжаем / отменяем

### 13.5 Acceptance для Phase 1 → переход к Checkpoint 1

- [ ] BGE-M3 embedding model работает в pipeline (smoke на 5 кейсах)
- [ ] BGE-Reranker-v2-m3 работает (smoke)
- [ ] Constrained decoding включён для всех 4 типов LLM-вызовов (или fallback задокументирован если Qwen3 bug проявился)
- [ ] Baseline replay на ii25_test сохранён в `data/eval_baselines/2026-05-27_v0.4.tooling_*.json`
- [ ] `docs/checkpoint1_decision.md` написан с явным решением по дальнейшим фазам

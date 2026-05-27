# Hierarchical Classification Pipeline — Design Spec

**Date:** 2026-05-27
**Project:** Class-OG-Final
**Status:** Draft (awaiting user review)
**Author:** Claude (Opus 4.7) in brainstorming session
**Target:** улучшение Top-1 accuracy с ~25% до ≥45% на ii25_test

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

| Метрика | Текущее | Target | Источник |
|---|---:|---:|---|
| Stage 1 L1 accuracy | ~50% | >85% | Phase 1 |
| Stage 2 L2 accuracy | ~30% | >70% | Phase 1 |
| L2 pool contains gold | ~60% | >90% | Phase 1 |
| dense_recall@10 (full pool) | 41.1% | >55% | Phase 5 |
| dense_recall@30 (filtered) | TBD via Phase 0 | >80% | Phase 1+5 |
| dense_recall@50 (filtered) | 69.6% | >90% | Phase 1+5 |
| aggregate_top1 | 3.6% | >25% | Phase 3+4 |
| aggregate_top5 | 7.1% | >55% | Phase 3+4 |
| aggregate_top10 | 12.5% | >70% | Phase 3+4 |
| Top-1 final | ~25% | >45% | All phases |
| Top-3 final | TBD via Phase 0 | >70% | All phases |
| L1 prefix accuracy | 23.2% | >80% | All phases |
| L2 prefix accuracy | 16.1% | >75% | All phases |

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

## 9. Rollout plan

```
Phase 0: Eval Framework           [Дни 1-2]   Блокирует все
   ↓
   ├─ Phase 1: Router LLM          [Дни 3-5]   ─┐
   └─ Phase 2: PTO Cleanup         [Дни 3-7]    │ параллельно
                                                 │
   ── Gate 1: Router L1 acc ≥ 70% ───────────────┘
   ↓
   ├─ Phase 3: Reranker Fix        [Дни 6-8]   ─┐
   └─ Phase 4: CE Training         [Дни 8-10]   │ Phase 4 нужны данные Phase 2
                                                 │
   ── Gate 2: aggregate_top10 ≥ 50% ─────────────┘
   ↓
   Phase 5: LoRA fine-tune         [Дни 10-14]  (CPU фон, ~15 ч)
   ↓
   ── Gate 3: recall@10 ≥ 50% ──
   ↓
   Phase 6: Integration & Final Eval [Дни 15-16]
   ↓
   Phase 7 (опц): Operator UI       [День 17]
```

### Gate fallbacks

| Gate | Если не прошли | Откат |
|---|---|---|
| Gate 1 | Router L1 acc < 70% | Heuristic-router back. Phase 2 продолжаем |
| Gate 2 | aggregate_top10 < 50% | Откат aggregate weights к defaults |
| Gate 3 | recall@10 не выше adapter_v3 | `.env` revert к adapter_v3 |

### Git workflow
- Feature branch на каждую фазу: `phase-1-llm-router`, ..., `phase-5-lora`
- Merge в `main` только после GO на eval
- Tag `v0.4.<phase>` после каждого merge

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
- [ ] Архитектура корректна
- [ ] Phase boundaries и gate criteria приемлемы
- [ ] Acceptance criteria (Top-1 ≥ 45%) реалистичны и согласованы
- [ ] Используем Ario для всех LLM-задач (confirmed)
- [ ] CPU + real-data only для embedding fine-tune (confirmed)

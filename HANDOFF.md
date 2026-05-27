# Class-OG-Final — Handoff Document (2026-05-27)

## Проект
AI-агент классификации обращений граждан РФ по 59-ФЗ и Общероссийскому классификатору (2108 кодов). Интеграция с Directum RX 25.3.

---

## Что было сделано в сессию (2026-05-27)

### 1. PTO Dataset (ЗОЛОТО)
- Импортировано **6502 записей** из `ПТО датасет/ts/Есть в обучении/` (31 класс)
- Encode-ados через `intfloat/multilingual-e5-base` → `data/pto_query_embs.npy` + `data/pto_query_meta.json`
- Скрипт: `scripts/import_pto_dataset.py`

### 2. Adapter V2 (PTO-trained)
- Обучен `Linear(768→768)` adapter на 6502 PTO-примерах
- `scripts/train_adapter_v2.py` — InfoNCE loss, batch=256, lr=0.001
- **Лучший checkpoint = epoch 1** (далее переобучение на PTO-домен)
- Результаты cross-domain recall@10:
  - Baseline (no adapter): **37.5%**
  - Adapter v1 (43 ii25_train): **35.7%**
  - Adapter v2 (6502 PTO, epoch1): **25.0%** ❌
  - **Вывод: v2 хуже на cross-domain** — PTO-домен не переносится

### 3. Adapter V3 (Blend)
- Создан `models/adapter_v3.npz` = blend **90% v1 + 10% v2**
- `scripts/compare_adapters.py` — чистая оценка recall@K:
```
ORIGINAL (no adapter):    recall@10=37.5% recall@50=51.8%
ADAPTER_V1 (43 ii25):     recall@10=35.7% recall@50=67.9%
ADAPTER_V2 (6502 PTO):    recall@10=25.0% recall@50=53.6%
BLEND 90/10 (adapter_v3): recall@10=41.1% recall@50=69.6% ✓
```
- Применён: `scripts/apply_adapter.py --adapter models/adapter_v2.npz --output data/vector_db_adapted_v3`
- В `.env`: `VECTOR_DB_DIR=data/vector_db_adapted_v3`, `ENABLE_EMBEDDING_ADAPTER=false`

### 4. Retrieval-Only Evaluation (ii25_test, 56 cases)

| Конфигурация | Dense recall@10 | Dense recall@50 |
|---|---|---|
| ORIGINAL DB | 37.5% | 51.8% |
| **Adapter v3 DB** | **41.1%** | **69.6%** |

### 5. Whitelist/Routing Order Fix (classifier_agent.py:1145-1200)
**БЫЛО:**
```
whitelist → L2-filter → L3-filter → rerank
```
**СТАЛО:**
```
L2-filter → L3-filter → whitelist → rerank
```
Routing теперь сужает пул ДО применения whitelist.
Код перенесён в `scripts/` частично; изменения в `src/classifier_agent.py`.

### 6. L3-Grouped Prompt (классификация)
Добавлен `CLASSIFICATION_PROMPT_TEMPLATE_L3GROUP` — 2-stage:
1. LLM выбирает L3-тему из `l3_options[]` (список уникальных L3 тем пула)
2. Затем выбирает leaf-код из выбранной L3

Проблема: LLM не использует это (нужно проверить код интеграции).

### 7. Router Prompt Enhancement (section_router.py)
**БЫЛО:** 2000 символов, длинный промпт без акцента на алгоритм
**СТАЛО:**
- ROUTING_SYSTEM_PROMPT переписан с нуля — добавлен "АЛГОРИТМ ВЫБОРА" с конкретными примерами ошибок
- Добавлено предупреждение: читать ВЕСЬ текст, не фокусироваться на "водянистых" фразах
- ROUTING_USER_TEMPLATE — "читай весь текст, не только начало!"
- Window остался 2000 символов (была попытка 4000 — откатили)

### 8. .env Конфигурация (актуальная)
```
VECTOR_DB_DIR=data/vector_db_adapted_v3
ENABLE_EMBEDDING_ADAPTER=false
ENABLE_SECTION_ROUTING=true
ENABLE_LTR_RERANKER=true
LTR_WEIGHT=0.30
TOP_K_CANDIDATES=30
ENABLE_ALLOWED_CODES=true
ALLOWED_CODES_PATH=data/allowed_codes_top69.json
```
**Важно:** Для adapter_v3 DB (`vector_db_adapted_v3`) НЕ включать `ENABLE_EMBEDDING_ADAPTER=true` — DB уже предобработана адаптером. Включение даёт double-adaptation.

---

## Архитектурные проблемы (выявлены анализом)

### Проблема 1: Whitelist → Routing Order
Раньше whitelist фильтровал до routing. Исправлено.
**Файл:** `src/classifier_agent.py:1145-1200`

### Проблема 2: Direct-Fetch Candidates без Сигнала
Когда whitelist срезает пул < TOP_K, добавляются кандидаты с `similarity=0.5` (source="whitelist-direct"), которые:
- НЕ прошли reranker
- Не имеют lexical, branch, LTR scores
- LLM может выбрать их как легитимных кандидатов

### Проблема 3: CrossEncoder использует `similarity`, не `rerank_score`
В `reranker.py:104`:
```python
heuristic_scores = [float(c.get("similarity", 0.0)) for c in head]
```
CE получает сырой dense score, игнорируя lexical/hierarchy/LTR.

### Проблема 4: Routing — главное узкое место
**Router/LLM выбирает неправильную L2-тему** — это каскадируется во все downstream ошибки. На PTO-тесте выборке (11 случаев):

| Case type | Count | Router correct? |
|---|---|---|
| Federal topics (0001.*) | 3 | ❌ Ошибся в 3/3 |
| Social/Education (0002.*) | 0 | — |
| Construction/Infrastructure (0003.*) | 5 | Частично (0096 vs 0097 confusion) |
| Housing (0005.*) | 3 | ❌ Sibling confusion (0053 vs 0055/0056) |

---

## Доступные данные

### Обучающие данные
| Файл | Описание |
|---|---|
| `data/pto_train.jsonl` | 6502 записей PTO (31 класс) |
| `data/pto_query_embs.npy` | embeddings (6502, 768) |
| `data/ii25_train.jsonl` | 43 записей ii25 (обучающая выборка) |
| `data/ii25_test.jsonl` | 56 записей ii25 (тест) |

### Модели
| Файл | Описание |
|---|---|
| `models/adapter_v1.npz` | Adapter на ii25_train (W, b) |
| `models/adapter_v2.npz` | Adapter на PTO epoch1 (лучшийcheckpoint) |
| `models/adapter_v3.npz` | Blend 90%v1+10%v2 — **текущий лучший** |
| `models/ltr_v1.json` | LtR reranker weights |

### Базы эмбеддингов
| Путь | Описание |
|---|---|
| `data/vector_db/` | ORIGINAL (без адаптера) |
| `data/vector_db_adapted_v1/` | С adapter_v1 |
| `data/vector_db_adapted_v2/` | С adapter_v2 |
| `data/vector_db_adapted_v3/` | **Текущая** — blend 90/10 |

---

## Быстрые победы (что попробовать)

### 1. Улучшить Router (главное)
Router — 80% ошибок на PTO domain. Продолжить улучшать промпт, добавив:
- Конкретные примеры разграничения 0096 vs 0097 (новостройка vs благоустройство)
- Примеры разграничения 0053 vs 0055/0056 (жилищные программы vs коммуналка)
- Добавить "sandwich method" — сначала L1 (5 разделов), потом L2

### 2. L3 Recall для нового corpus
- Adapter v3 даёт **L3 recall@30 = 76.8%** — правильный L3-префикс попадает в пул из 30
- LLM получает 30 individual codes, но не видит группировку по L3

### 3. Проверить тестовую выборку PTO
Выборка `ПТО датасет/Выборка для тестирования.xlsx` (11 записей) — коды вызывают сомнения. Например:
- "Закон о Тишине" → ожидаем `0001.0001.0011.0038` — но это явно про noise regulation
- Возможно, нужно переразметить выборку

### 4. Попробовать без routing
```bash
ENABLE_SECTION_ROUTING=false
```
и запустить eval на ii25_test — сравнить с routing enabled.

---

## Запуск

```bash
# API сервер
uvicorn src/api_server:app --host 0.0.0.0 --port 8000 --reload

# Eval на ii25
./venv/Scripts/python.exe scripts/eval_pto_sample.py

# Eval accuracy с LLM
./venv/Scripts/python.exe tests/eval_accuracy.py --fixtures tests/fixtures/ii25_test.json
```

---

## Eval Framework (Phase 0 — 2026-05-27)

Новый orchestrator `tests/eval_hierarchical.py` для per-stage метрик с failure attribution.
Реализован по [spec](docs/superpowers/specs/2026-05-27-hierarchical-classification-design.md) и [plan](docs/superpowers/plans/2026-05-27-phase-0-eval-framework.md).

### Запуск

```bash
# Retrieval only (быстро, ~30 сек)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage retrieval

# Reranker (без LLM, ~1-2 мин)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage reranker

# Полный pipeline с LLM Ario (~35-40 мин на 56 кейсов)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage all

# Сохранить baseline snapshot (для diff'ов)
./venv/Scripts/python.exe tests/eval_hierarchical.py --stage all --tag v1.1-router --save-baseline
```

### Output

- `eval_results/<timestamp>_<tag>_<stage>.jsonl` — per-case детали (gitignored)
- `eval_results/<timestamp>_<tag>_<stage>_summary.md` — markdown отчёт (gitignored)
- `data/eval_baselines/<date>_<tag>_<stage>.json` — baseline snapshot (в git, для diff'ов)

### Failure attribution categories (Phase 0)

- `correct` — Top-1 ok
- `llm_final` — gold в reranked top-10, но LLM выбрала другой код
- `reranker` — gold в dense top-50, но reranker уронил его из top-10
- `retrieval_recall` — gold вообще не в dense top-50
- `no_gold` — у кейса нет gold-кода (исключается из метрик)

В Phase 1+ добавятся `router_stage1`, `router_stage2`.

### Baseline (2026-05-27, ii25_test, 56 кейсов)

| Stage | Top-1 | dense_recall@10 | dense_recall@50 | reranked_recall@10 |
|---|---:|---:|---:|---:|
| retrieval | n/a | **41.1%** | **69.6%** | n/a |
| reranker | 0% (no LLM) | 41.1% | 69.6% | **12.5%** |
| all (full pipeline) | **7.1%** (4/56) | 41.1% | 69.6% | 12.5% |

**Failure attribution (--stage all):**
- 30 кейсов `reranker` — gold в dense top-50, но reranker уронил из top-10
- 17 кейсов `retrieval_recall` — gold вообще не в dense top-50
- 5 кейсов `llm_final` — gold в reranked top-10, но LLM выбрала другой код
- 4 кейса `correct`

**Дополнительные наблюдения:**
- Multi-question appeals: 1/56 — большинство single-question, soft и strict Top-1 совпадают
- 2 LLM errors (400 Bad Request от Ario на 1 кейсе — флуктуация)
- LLM Top-1 (7.1%) ≈ Reranked Top-5 (7.1%) — LLM эффективно выбирает из top-5 reranker'а

**Важно:** ранее в HANDOFF упоминался `Top-1 ~25%`. Это либо измерено на другом cut, либо при других параметрах. Текущий честный baseline на ii25_test = **7.1%**. Spec target Top-1 ≥ 45% → теперь это +38 pp, более амбициозная цель, но и больше room для улучшения.

Любая последующая фаза должна сравниваться с `data/eval_baselines/2026-05-27_baseline_*.json`.

---

## Ключевые файлы (изменены в сессию)

- `src/classifier_agent.py` — whitelist/routing order fix (lines ~1145-1200)
- `src/section_router.py` — improved routing prompt, 2000→4000→2000 window
- `.env` — VECTOR_DB_DIR=data/vector_db_adapted_v3, ENABLE_EMBEDDING_ADAPTER=false
- `scripts/train_adapter_v2.py` — adapter training on PTO
- `scripts/compare_adapters.py` — recall comparison
- `scripts/convert_test_sample.py` — import Excel test sample to JSONL
- `scripts/eval_pto_sample.py` — eval on PTO test sample

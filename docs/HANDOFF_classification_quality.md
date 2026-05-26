# Handoff: Точность классификации обращений граждан

**Дата:** 2026-05-26 (обновлён вечером того же дня — появился датасет ИИ25)
**Передаёт:** Claude Opus 4.7 (предыдущий агент)
**Принимает:** новый агент, продолжающий работу над точностью классификатора

---

## 🆕 0. Главное изменение: появился реальный датасет ИИ25 (99 размеченных кейсов)

**Источник:** `C:\Users\Korotaev_NO\Desktop\Проекты\ИИ25` — папка .docx-файлов обращений с **assigned_code в имени файла** (например `02-239.docx → 0002.0004.0051.0239`). Импортирован пользователем через [scripts/import_ii25_dataset.py](../scripts/import_ii25_dataset.py).

**Куда легло:**
- [data/ii25_train.jsonl](../data/ii25_train.jsonl) — 43 записи
- [data/ii25_test.jsonl](../data/ii25_test.jsonl) — 56 записей
- [data/ii25_report.json](../data/ii25_report.json) — статистика
- [tests/eval_ii25_retrieval.py](../tests/eval_ii25_retrieval.py) — eval скрипт (retrieval-only, без LLM)
- [docs/ii25_retrieval_report.md](ii25_retrieval_report.md) — отчёт по retrieval

**Распределение по L1 (всего 99):** 0001=2 | 0002=24 | 0003=**45** | 0004=6 | 0005=22.
Доминирует Раздел 3 (Экономика — хозяйственная деятельность). 60 уникальных кодов; самые частые: `0003.0009.0097.0689` (6 раз), `0003.0009.0099.0742` (6 раз).

### Базовые retrieval-метрики на ii25_test (56 кейсов, БЕЗ LLM и БЕЗ routing)

| Метрика | Значение |
|---|---:|
| Dense recall@10 | 37.5% (21/56) |
| **Dense recall@50** | **51.8%** (29/56) |
| Lexical recall@30 | 14.3% (8/56) |
| Reranked Top-1 | 10.7% (6/56) |
| Reranked Top-3 | 16.1% |
| Reranked recall@10 | 30.4% |
| **Prefix L1 Top-1** | **57.1%** |
| Prefix L2 Top-1 | 46.4% |
| Prefix L3 Top-1 | 37.5% |

**Ключевой вывод:** даже в top-50 dense есть только **51.8%** правильных кодов. Это потолок embedder'а — никакой reranker / LLM не сможет выбрать то, чего нет в пуле. **Embedder нужно fine-tunить — иначе выше 50-60% Top-1 не подняться.**

### Pipeline-метрика на ii25_test (с LLM + section routing) — НЕ ИЗМЕРЕНА

⚠ Eval `tests/eval_ii25_retrieval.py` — **retrieval-only**, без LLM-классификации и без section routing. Полную end-to-end метрику (Top-1 после LLM + routing) на ii25_test **никто не измерил**. Это первое что нужно сделать новому агенту:

```powershell
# Конвертировать ii25_test.jsonl → формат fixture (eval_accuracy.py ждёт JSON array)
# Или адаптировать eval_accuracy.py чтобы принимать JSONL формат
# Затем:
$env:ENABLE_SECTION_ROUTING="true"; $env:HF_HUB_OFFLINE="1"
.\venv\Scripts\python.exe tests\eval_accuracy.py --fixtures data\ii25_test_fixtures.json
```

Ожидание (на основе real-12): +27 pp от baseline retrieval → ~38% Top-1, Prefix L1 ~85%.

---

## 1. Контекст проекта

**Проект:** [Class-OG-Final](../) — AI-агент классифицирует обращения граждан по 59-ФЗ и Общероссийскому классификатору (v4, 2108 записей, 4-5 уровней иерархии). Запускается в Docker, веб-UI на `http://localhost:8000`, оператор подтверждает/исправляет результат для накопления fine-tuning данных.

**Стек:** Python 3.11 / FastAPI / sentence-transformers (`intfloat/multilingual-e5-base`) / numpy для cosine similarity / Groq/Gemini/Ollama/Ario LLM. Все ключевые файлы — в [src/](../src/), тесты — [tests/](../tests/), документация — [docs/](../docs/).

**Главный CLAUDE.md:** [../CLAUDE.md](../CLAUDE.md). Прочитать ОБЯЗАТЕЛЬНО — там команды, архитектура, ограничения версий, протокол начала сессии.

---

## 2. Что было сделано в предыдущей сессии (5 коммитов)

| Коммит | Что |
|---|---|
| `62be112` | Per-question segmentation (EPIC-08 Task 2) — главный фикс многовопросных обращений |
| `75dffde` | Hierarchy-aware reranking (branch agreement + parent similarity) + CrossEncoder + Query Expansion как opt-in |
| `fbf59bb` | **Section routing (coarse-to-fine)** — главный текущий механизм. LLM отдельным вызовом выбирает 1-3 тематики (L2) из 21, кандидаты в retrieval фильтруются по ним |
| `d8bce3c` | Routing prompt fix: разграничение ведомств в Разделе 4 (МВД ≠ Прокуратура) |
| `(uncommitted)` | `.env` обновлён: `ENABLE_SECTION_ROUTING=true` по умолчанию |
| **`(uncommitted)`** | **Импорт ИИ25 датасета**: scripts/import_ii25_dataset.py + ii25_train.jsonl (43) + ii25_test.jsonl (56) + tests/eval_ii25_retrieval.py + 3 новых unit-теста (test_import_ii25_dataset.py, test_section_router_prompt.py, test_strict_validation.py) |

### Текущие метрики

| Метрика | Baseline до сессии | **Текущая (с section routing)** |
|---|---:|---:|
| **Real-12 Top-1** | 18.2% | **54.5%** (+36 pp, 3x) |
| **Real-12 Prefix L1** | 72.7% | **81.8%** |
| **Real-12 Prefix L2** | 72.7% | **81.8%** |
| **Real-12 Prefix L3** | 54.5% | 72.7% |
| **Synthetic 11 Top-1** | 50% | 45.5% |
| **Synthetic 11 Prefix L1** | 100% | 100% |

Узкое место: **выбор точного L4-leaf-кода среди sibling-кодов** в правильной тематике. Требует fine-tuning эмбеддера на размеченных данных.

---

## 3. Архитектура пайплайна (актуальная)

```
Текст обращения
    │
    ▼
1. Сегментация ([src/classifier_agent.py::split_appeal_questions](../src/classifier_agent.py))
    Маркеры: "1.", "во-первых", абзацы с verb-индикаторами → N сегментов
    │
    ▼
2. Section routing (опц., default ON) — ОДИН LLM-вызов на ВСЁ обращение
    [src/section_router.py](../src/section_router.py) — каталог тематик из методички
    LLM возвращает 1-3 L2-кодов вида "XXXX.XXXX"
    │
    ▼
3. Per-question retrieval (для КАЖДОГО сегмента отдельно)
    Dense (multilingual-e5-base) → top-50/100
    Lexical (BM25-like token overlap) → top-30/60
    Merge → filter by allowed L2 → если мало, добавить direct-fetch из L2
    Heuristic rerank: lexical + level + branch agreement + parent similarity
    (опц.) CrossEncoder reranker (BAAI/bge-reranker-base) — OFF по умолчанию
    → top-10 кандидатов
    │
    ▼
4. LLM-классификация — ОДИН вызов с questions_with_candidates[]
    Промпт: sibling-aware, no fallback meta-code, deeper > L1
    → vid, тип, code per question
    │
    ▼
5. Strict validation
    Код LLM вне кандидатов вопроса → подмена top-1 + conf ≤ 0.45
    Один код на N>1 вопросов с разными темами → conf ≤ 0.5
    │
    ▼
6. Карточка оператора + JSONL-лог для fine-tuning
```

### Ключевые файлы

| Файл | Назначение |
|---|---|
| [src/classifier_agent.py](../src/classifier_agent.py) | Основной агент. `classify()` оркестрирует пайплайн. `_route_to_sections()` (routing LLM), `_retrieve_for_segment()` (retrieval + filter), `_rerank_candidates()` (heuristic + hierarchy), `_classify_with_llm()` (главный LLM-вызов), strict validation внутри `classify()` |
| [src/section_router.py](../src/section_router.py) | `SECTIONS_CATALOG` (5 разделов × 21 тематика с описаниями из методички УПП РФ), `ROUTING_SYSTEM_PROMPT`, `filter_candidates_by_l2()` |
| [src/hierarchy.py](../src/hierarchy.py) | `branch_agreement_scores`, `parent_similarity_boost`, `prefix_at_level`, `dominant_l1_sections` |
| [src/reranker.py](../src/reranker.py) | `CrossEncoderReranker` с lazy-load, fallback на heuristic при ошибке |
| [src/config.py](../src/config.py) | Все флаги и пороги через env. См. `ENABLE_SECTION_ROUTING`, `HIERARCHY_*_WEIGHT`, `ENABLE_CROSS_ENCODER_RERANKER`, `ENABLE_QUERY_EXPANSION` |
| [tests/eval_accuracy.py](../tests/eval_accuracy.py) | Скрипт eval. Использует фикстуры с `expected_codes`, считает Top-K/Prefix-L-N/retrieval recall. Запуск: `./venv/Scripts/python.exe tests/eval_accuracy.py --fixtures <path>` |
| [tests/fixtures/test_appeals.json](../tests/fixtures/test_appeals.json) | Синтетические 11 кейсов (ex01-ex11) |
| [tests/fixtures/test_appeals_real12.json](../tests/fixtures/test_appeals_real12.json) | **11 real-кейсов с экспертной разметкой из xlsx** |
| [docs/accuracy_report_v2.md](accuracy_report_v2.md) | Свежий отчёт eval (перезаписывается каждым прогоном) |
| [docs/superpowers/plans/2026-05-26-epic08-classification-quality.md](superpowers/plans/2026-05-26-epic08-classification-quality.md) | Полный план EPIC-08. Сделано: Task 2, частично 4-5; не сделано: Task 6 (strict validation полная), 7 (calibrated confidence), 8 (hard negatives), 9 (debug API), 10 (full feature flags doc) |

---

## 4. Ключевые конвенции и ограничения

- **Python 3.11 строго**. 3.12+ ломает ML-зависимости.
- **numpy<2.0** запинено (np.float_ используется старыми зависимостями)
- **Все скрипты запускаются из корня проекта**, не из `src/`. Импорт через `sys.path.insert(0, 'src')`.
- **HF mirror** — установлены `HF_ENDPOINT=https://hf-mirror.com`, `HF_HUB_DISABLE_SSL=1`. Для offline-eval использовать `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` (модель уже скачана).
- **LLM-провайдер** в текущем `.env` = **Ario** (`Qwen/Qwen3-32B-AWQ` через `https://gpt.ario.directum360.ru/v1`). Корпоративный, не Groq.
- **Не трогать `.env` без явной просьбы** — там реальные ключи.
- **Не коммитить автоматически** — пользователь сам решает что коммитить (он попросил «закоммить всё» в этой сессии).
- **Eval долгий** (~3-5 минут на 11 фикстур). Запускать в фоне или ставить timeout 600000.

---

## 5. ⚡ Что прямо сейчас нужно сделать

### 🔴 Задача №0: Прогнать FULL pipeline eval на ИИ25 (КРИТИЧНО, до всего остального)

Сейчас на ии25 измерены только retrieval-метрики (`tests/eval_ii25_retrieval.py`). Полная end-to-end метрика (с LLM + section routing + strict validation) **не измерена**. Без этого числа все приоритеты ниже — гадание.

**Шаги:**
1. Сконвертировать `data/ii25_test.jsonl` (поля: `id`, `appeal_text`, `assigned_code`, `level`, `parent_code`, `full_path`) в формат `tests/fixtures/*.json` (поля: `id`, `text`, `expected_codes: list`, `expected_prefix`). Положить в `tests/fixtures/ii25_test_fixtures.json`.
2. Запустить: `$env:ENABLE_SECTION_ROUTING="true"; .\venv\Scripts\python.exe tests\eval_accuracy.py --fixtures tests\fixtures\ii25_test_fixtures.json`
3. Сравнить полученные **Top-1, Top-3, Prefix L1/L2/L3** с retrieval-only метриками из [docs/ii25_retrieval_report.md](ii25_retrieval_report.md).
4. Понять — где основные провалы (retrieval не находит? LLM выбирает соседа? Router выбирает не тот L2?).

Только после этого можно осмысленно решать какую задачу делать дальше.

### Задача №1: Проблемные места в routing-промпте (высокий приоритет)

Пользователь спросил: «Может быть в промпт роутинге всё ещё есть проблемные места?» — я выявил **9 точек** в [src/section_router.py](../src/section_router.py)::`SECTIONS_CATALOG` и `ROUTING_SYSTEM_PROMPT`. **Топ-3 на исправление** (согласовано с пользователем):

1. **Школы — детализировать правило**
   Сейчас: «детский сад/школа → 0002.0013 Образование» — слишком жёстко.
   Нужно: «**деятельность** школы/детсада (поступление, обучение, очередь) → 0002.0013. **Строительство/реконструкция здания** школы/детсада → 0003.0009.0097.0687 (Соцобъекты)».

2. **Раздел 1 — расширить 0001.0001, 0001.0003, 0001.0021**
   - **0001.0001** Конституционный строй: добавить «нарушения избирательного процесса, права на ассоциации/собрания, государственная символика»
   - **0001.0003** Гражданское право: уточнить разницу с 0004.0019 (Нотариат) — наследство по существу = 0001.0003, оформление через нотариуса = 0004.0019
   - **0001.0021** Индивидуальные правовые акты: триггеры «получение гражданства РФ», «помилование», «представление к награде», «присвоение почётного звания»

3. **Правило «Лично Президенту» — явное**
   Real06 показал: упоминание «Уважаемый Президент Путин» сваливает router в 0001.0002.0027.0145 (Личный приём). Добавить правило: «**Упоминание главы государства/Президента/мэра в обращении НЕ определяет тематику**. Смотри по СУЩЕСТВУ вопроса». Если заявитель пишет Президенту жалобу на ЖКХ → тематика 0005.0005, не 0001.0002.

### Задача №2: real06 hallucination (отдельный bug)

На real-12 кейс real06 (письмо Президенту про прокуратуру) router выбирает `['0004.0019']` (Прокуратура), но финальная LLM возвращает `0001.0002.0027.0145` — **код вне отфильтрованного пула**. Strict validation должна это поймать (подменить на top-1 + conf ≤ 0.45), но возвращается conf 92%. Гипотезы:
- `valid_code_sets[ordinal]` пуст по какой-то причине → проверка пропускается
- Ordinal mismatch между сегментом и LLM-ответом
- LLM возвращает код который IS в кандидатах (после direct-fetch L2 пул содержит ВСЕ 0004.0019.* коды — а 0001.0002.0027.0145 НЕ должно быть)

**Расследование:** добавить debug print в strict validation секцию `classify()` ([src/classifier_agent.py](../src/classifier_agent.py) ~строка 730), прогнать real06 отдельно, понять что происходит.

### Задача №3 (большая): fine-tuning эмбеддера

Dense recall@50 = только 51.8% на ИИ25_test — главный потолок. Sibling-confusion после fine-tuning тоже улучшится. Embedder обучить на размеченных парах `(текст обращения, search_text правильного кода)`.

**Источники данных для датасета:**
- **`data/ii25_train.jsonl` — 43 записи с надёжной экспертной разметкой** (ГЛАВНЫЙ источник)
- `data/appeals_log.jsonl` — 269 записей, есть verified/corrected (фильтровать по `verification_status`)
- `data/historical_verified.jsonl` — 40 записей
- `tests/fixtures/test_appeals_real12.json` — 11 кейсов с экспертной разметкой

Итого ~360 пар. Достаточно для первого прохода `MultipleNegativesRankingLoss`.

**Workflow:**
1. Адаптировать [src/finetune_model.py](../src/finetune_model.py) чтобы читал `ii25_train.jsonl` (формат: `appeal_text` → `search_text(assigned_code)`)
2. Запустить: `python src/finetune_model.py --source ii25` (или комбинированный)
3. Обновить `.env`: `EMBEDDING_MODEL=models/e5-finetuned-v1`
4. Пересобрать векторную БД: `python src/build_vectordb.py`
5. Прогнать eval **на ii25_test** (это 56 кейсов которых модель НЕ видела при обучении — независимая оценка)
6. Сравнить retrieval@50 до/после, Top-1 до/после

[docs/FINETUNING.md](FINETUNING.md) — документация процесса.

### Задача №4 (medium): унификация fixture-наборов

Сейчас три источника тестов с разными форматами:
- `tests/fixtures/test_appeals.json` — 11 синтетических (поля: `id`, `text`, `expected_codes`)
- `tests/fixtures/test_appeals_real12.json` — 11 real-кейсов
- `data/ii25_test.jsonl` — 56 ии25-кейсов (поля: `appeal_text`, `assigned_code`)

Логично свести всё к одному формату или сделать удобный wrapper. Можно либо:
- Адаптировать `eval_accuracy.py` чтобы читал и JSON-array и JSONL
- Или конвертировать ii25_test в формат fixture (см. Задача №0)

---

## 6. Что НЕ делать (грабли)

- **Не лезть в Query Expansion** (`ENABLE_QUERY_EXPANSION=true`) — на наших фикстурах даёт регрессию (sibling drift). Оставлено как opt-in для экспериментов на других данных.
- **Не повышать `LEXICAL_RERANK_WEIGHT` выше 0.20** — увеличивает Reranked recall@10, но Top-1 падает (правильный код в пуле, но LLM выбирает соседа).
- **Не блокировать ВЕСЬ `0001.0002.0027.*`** в LLM-промпте — там есть легитимные коды (например `0124` — Бездействие при рассмотрении обращения, real04). Сейчас блокирован только `0126` (Отсутствует адресат).
- **Не повышать `SECTION_ROUTING_MAX_TOPICS` выше 3** — на 4 даёт регрессию (LLM выбирает лишние тематики).
- **Не запускать `python tests/eval_accuracy.py` без `HF_HUB_OFFLINE=1`** — иногда падает с «client closed» из-за HF mirror.

---

## 7. Известные баги и наблюдения

1. **`accuracy_report_v2.md` перезаписывается каждым прогоном eval** — если нужно сравнение «до/после», сохранять копии.
2. **Точность вида = 0% на real-12** — это не баг, в фикстурах нет `expected_vid` (эксперт не размечал вид).
3. **Среднее время 14-25 сек** — выше цели 8 сек. Per-question retrieval делает N запросов вместо 1. Ускорить можно batch-encoding embedding'ов (sentence-transformers поддерживает).
4. **Сегментатор иногда не склеивает преамбулу с первым вопросом** если она длинная (>40 символов) и содержит verb-indicator. Например, юридическая цитата в начале обращения становится отдельным сегментом. Не критично — LLM находит для неё разумный код.
5. **`appeals_log.jsonl` растёт** от каждого запроса. В Docker volume mount — пересоберётся при пересборке.

---

## 8. Команды для быстрого старта новой сессии

```powershell
# 1. Прочитать актуальный CLAUDE.md
cat CLAUDE.md

# 2. Проверить статус git
git log --oneline -10
git status --short

# 3. Запустить полный eval (~3-5 мин на каждый набор)
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:ENABLE_SECTION_ROUTING="true"
.\venv\Scripts\python.exe tests\eval_accuracy.py --fixtures tests\fixtures\test_appeals.json
.\venv\Scripts\python.exe tests\eval_accuracy.py --fixtures tests\fixtures\test_appeals_real12.json

# 4. ⚡ Retrieval-only eval на ИИ25 (~1 мин, без LLM-вызовов)
.\venv\Scripts\python.exe tests\eval_ii25_retrieval.py --dataset data\ii25_test.jsonl

# 5. Все unit-тесты
.\venv\Scripts\python.exe -m pytest tests/ -v

# 6. Docker
docker-compose down; docker-compose up -d --build; docker-compose logs -f classifier
```

---

## 9. Контакт с пользователем

- Пользователь — **Никита Коротаев** (knic764@gmail.com), руководитель направления AI-агентов в Directum
- Главная задача: **точность классификации**, не code quality / refactoring
- Стиль работы: короткие ответы, конкретные предложения, минимум воды, прогресс инкрементально
- Уже знаком с проектом и архитектурой — не нужно объяснять базы
- Ждёт экспертную разметку от внешнего эксперта — пока её нет, fixture-набор ограничен real12+synthetic11
- Коммитит сам решает что коммитить, но в этой сессии разрешил «коммить всё»

---

## 10. Чек-лист первых 30 минут новой сессии

- [ ] Прочитать `CLAUDE.md`
- [ ] Прочитать этот handoff целиком (особенно секцию 0 — новый ИИ25 датасет)
- [ ] `git log --oneline -10` + `git status --short` — что коммитнуто, что нет
- [ ] Прочитать `data/ii25_report.json` — статистика по новому датасету
- [ ] Прочитать `docs/ii25_retrieval_report.md` — текущие retrieval-метрики (Top-1=10.7% на 56 кейсах)
- [ ] Прочитать `src/section_router.py` — главный механизм routing
- [ ] Прочитать `tests/fixtures/test_appeals_real12.json` — что считается правильным
- [ ] Глянуть 3 новых unit-теста: `tests/test_import_ii25_dataset.py`, `tests/test_section_router_prompt.py`, `tests/test_strict_validation.py`
- [ ] Прокомментировать пользователю: «Готов прогнать full pipeline eval на ИИ25_test (Задача №0) — это даст реальную end-to-end Top-1 на свежих 56 кейсах. Делать?»

**НЕ начинать имплементацию без подтверждения от пользователя — он направляет приоритеты.**

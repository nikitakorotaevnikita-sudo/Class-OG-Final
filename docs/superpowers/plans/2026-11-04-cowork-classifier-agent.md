# Прототип агента-классификатора на ArioCowork — план реализации

> **Для агента-исполнителя:** обязателен навык superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans — план выполняется по задачам. Шаги отмечаются чекбоксами (`- [ ]`).

**Цель:** дать оператору всё, чтобы собрать в ArioCowork агента «Классификатор обращений» и проверить его вручную: HTTP-точку поиска кандидатов в сервисе классификатора, тексты инструкции и навыков, zip-навыки и xlsx-шаблон проверки.

**Архитектура:** у агента один MCP — mcpOGV. Наш поиск (эмбеддинги + лексика поверх уже загруженного `ClassifierAgent`) отдаётся двумя HTTP-методами в роутере `/integration` сервиса классификатора. Новый тул mcpOGV `classifier_search` / `classifier_get` вызывает их — его делает сессия MCP в своём репозитории по нашему контракту. Финальный код выбирает агент по навыку `classifier-code` с учётом истории заявителя (`og-repeat`). Тексты агента — markdown-файлы в `cowork/`, из них скрипт собирает zip-навыки.

**Технологии:** Python 3.11, FastAPI (`fastapi.testclient.TestClient` в тестах), pytest, openpyxl.

**Спецификация:** `docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md`, редакция 4 (ветка `research/quality-followups-68`).

**Ветки:** план и спецификация живут в `research/quality-followups-68`; **код пишется в `int-og`** (от `main`, туда идёт сервис стенда). Задача 1 переносит оба документа в `int-og`.

## Общие ограничения

- Python 3.11; запуск из корня проекта; интерпретатор — `venv\Scripts\python` (системный Python без зависимостей).
- Тесты: `venv\Scripts\python -m pytest tests/ -q -p no:cacheprovider`. База на `int-og`: 457 проходят, 3 давних падения `test_candidate_rerank` — не чинить, не считать регрессией.
- Новых зависимостей в сервис классификатора не добавлять (библиотека MCP ему не нужна).
- Только чтение: точка поиска не пишет в RX и никуда не ходит по сети.
- Логи точки поиска: время и число результатов. **Без текста запроса** (ПДн по 59-ФЗ).
- **Не читать тексты обращений граждан** (`tests/fixtures/*appeals*`, `data/*.jsonl`, `eval_results/*`): скрипты копируют их, не печатая; в выводе — только id, коды, счётчики. Примеры в навыке `appeal-questions` — **синтетические**, не из реальных обращений.
- `.env` не коммитить. README не трогать (в `main` — только по просьбе оператора).
- Тул в mcpOGV не писать и mcpOGV не менять — это зона сессии «MCP»; ей уходит контракт (задача 2, шаг 6).
- Кириллицу в файлах править через Edit/Write, не через heredoc в Bash.
- После каждой задачи, меняющей сервис, сообщать оператору, поднят ли сервис 8010 с правкой (обычно — нет).

## Отступления от спецификации (решения плана)

- Вариант B: в файле навыка `classifier-full` 1876 рубрик (уровни 4 и 5), а не 2108 — выбирать агент может только листья. Контрольный вопрос агенту — про 1876.

## На что смотреть при ревью

- Пустой или пробельный `query` → 400 с понятным текстом, а не пустой список. Тест — задача 2.
- `top_k` вне диапазона (0, −5, 500) → приводится к 1…50, не 422 и не падение. Тест — задача 2.
- Код в другом виде: с пробелами, три сегмента (`0003.0009.0097`), пятисегментный L5 → нормализуется (дополняется `.0000` до четырёх сегментов) и находится; неизвестный → 200 и `exists: false`. Тест — задача 2.
- Очень длинный `query` (10 000 символов, целое обращение) → ответ 200, без ошибки эмбеддера. Тест — задача 2.
- Сервис ещё грузит модель (`app.state.agent` нет) → 503 с текстом, как у остальных точек `/integration`. Тест — задача 2.

---

### Задача 1: Ветка `int-og` и документы

**Файлы:**
- Добавить (из `research/quality-followups-68`): `docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md`, `docs/superpowers/plans/2026-11-04-cowork-classifier-agent.md`

- [ ] **Шаг 1:** `git switch int-og` (локальные `data/*.jsonl` при конфликте — `git stash push -- data/appeals_log.jsonl data/request_log.jsonl`); `git checkout research/quality-followups-68 -- docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md docs/superpowers/plans/2026-11-04-cowork-classifier-agent.md`.
- [ ] **Шаг 2:** полный прогон тестов → 457 проходят, те же 3 падения.
- [ ] **Шаг 3:** коммит `docs(int-og): bring the Cowork agent spec and plan`.

---

### Задача 2: HTTP-точка поиска кандидатов

**Файлы:**
- Создать: `src/candidates_api.py`
- Изменить: `src/integration_api.py` (подключить роутер), `docs/INTEGRATION.md` (раздел про две новые точки), `docs/DEPLOY.md` (порт 8020 при соседстве с mcpOGV)
- Тесты: `tests/test_candidates_api.py`

**Интерфейсы:**
- Использует: `request.app.state.agent` (как `_get_agent` в `src/integration_api.py`: нет агента → 503 «Агент не инициализирован»); у агента — `_retrieve_for_segment(text: str) -> list[dict]` (кандидаты с `code, name, level, full_path, parent_code, similarity`) и `code_index: dict[str, dict]` (у уровней 1–3 коды дополнены `.0000` до четырёх сегментов, у L5 — пять сегментов).
- Даёт:
  - `normalize_code(code: str) -> str`;
  - `POST /integration/candidates`, тело `{"query": str, "top_k": int = 50}` → `{"items": [{"code", "name", "level", "full_path", "score"}], "returned": int}`;
  - `GET /integration/classifier/{code}` → `{"code", "name", "level", "full_path", "parent_code", "exists": true}` или `{"code", "exists": false}`.
  Это контракт для тула mcpOGV — менять только с сообщением сессии «MCP».

- [ ] **Шаг 1: тесты** в `tests/test_candidates_api.py` на голом `FastAPI()` с подключённым роутером и фейковым агентом в `app.state.agent` — `SimpleNamespace(_retrieve_for_segment=..., code_index={...})`: три кандидата (`0004.0015.0158.0974` similarity 0.61, ещё два ниже), в `code_index` — `0003.0009.0097.0000` (level 3), `0004.0015.0158.0974` (level 4), `0001.0001.0006.0013.0001` (level 5). Реальный `api_server` не поднимать — он грузит модель.
  - `test_search_returns_candidates_with_score` — `items[0] == {"code": "0004.0015.0158.0974", ..., "score": 0.61}`, ключи ровно `code,name,level,full_path,score`.
  - `test_search_default_is_fifty` — фейк отдаёт 60 кандидатов, `top_k` не передан → `returned == 50`.
  - `test_search_clamps_top_k` — `top_k=0` → `returned == 1`; `top_k=500` → 200, `returned <= 50`; `top_k=-5` → `returned == 1`.
  - `test_search_rejects_empty_query` — `query="  "` → 400, в `detail` «пуст».
  - `test_search_accepts_a_long_query` — `query` из 10 000 символов → 200 (фейк получает текст).
  - `test_get_finds_leaf_and_normalizes` — `GET /integration/classifier/%200004.0015.0158.0974%20` → `exists is True`, `level == 4`; `0003.0009.0097` → `code == "0003.0009.0097.0000"`, `level == 3`; L5-код → `level == 5`.
  - `test_get_unknown_code` — `9999.9999.9999.9999` → 200, `{"code": "9999.9999.9999.9999", "exists": False}`.
  - `test_503_while_agent_loads` — `app.state.agent` нет → обе точки 503.
  - `test_query_text_not_logged` — `capsys`: после запроса «секретный текст» вывод не содержит «секретный».
  - `test_router_is_registered` — в основном `integration_api.router` (или `api_server.app`) есть пути `/integration/candidates` и `/integration/classifier/{code}` (проверка по `app.routes`/`router.routes`, без старта модели).
- [ ] **Шаг 2:** прогнать → тесты падают (`ModuleNotFoundError: candidates_api`).
- [ ] **Шаг 3: реализовать** `src/candidates_api.py`: `APIRouter` с теми же префиксом и тегом, что у `integration_api`; pydantic-модель запроса без ограничений на `top_k` (приведение `max(1, min(50, top_k))` в коде — чтобы 500 не давало 422); поиск — `agent._retrieve_for_segment(query.strip())[:top_k]`, `score = round(similarity, 3)`; `normalize_code`: `strip()`, затем дополнение сегментами `0000` до четырёх (пять сегментов не трогать). Лог одной строкой: `[candidates] 50 за 0.84с`. Подключить роутер в `src/integration_api.py` так, чтобы `api_server` получил его без правок.
- [ ] **Шаг 4:** прогнать новые тесты → проходят; полный прогон → 457 + новые проходят, те же 3 давних падения.
- [ ] **Шаг 5:** документация: `docs/INTEGRATION.md` — обе точки с примерами запроса и ответа, поведение при пустом запросе и загрузке; `docs/DEPLOY.md` — если mcpOGV на той же машине на 8010, сервис на `HOST_PORT=8020`. Коммит `feat(integration): classifier candidates and code lookup endpoints for the Cowork agent`.
- [ ] **Шаг 6: контракт сессии «MCP».** Отправить ей (SendMessage, имя «MCP») запрос на новый тул: имена `classifier_search(query, top_k=50)` и `classifier_get(code)`, два HTTP-метода с телами ответа из «Даёт» выше, коды ответов 400/503, что адрес сервиса — настройка mcpOGV, только чтение. Без текстов обращений. Ответ сессии внести в §8.2 спецификации.

---

### Задача 3: Тексты агента — инструкция и три навыка

**Файлы:**
- Создать: `cowork/agent-instruction.md`, `cowork/skills/appeal-questions/SKILL.md`, `cowork/skills/applicant-contacts/SKILL.md`, `cowork/skills/classifier-code/SKILL.md`, `cowork/README.md`
- Тесты: `tests/test_cowork_texts.py`

**Интерфейсы:**
- Даёт: каталог `cowork/skills/<name>/SKILL.md` с frontmatter `name` и `description` — задача 4 собирает из него zip.

- [ ] **Шаг 1: тесты** `tests/test_cowork_texts.py`:
  - `test_instruction_names_every_tool_and_skill` — в `agent-instruction.md` есть `get_document`, `get_document_text`, `classifier_search`, `classifier_get`, `appeal-questions`, `applicant-contacts`, `classifier-code`, `og-repeat`, «mcpOGV».
  - `test_instruction_has_the_rx_contract` — есть ключи `document_id`, `applicant_fio`, `applicant_email`, `summary`, `reasoning`, `questions`, `check` и лимиты «250» и «1000».
  - `test_skills_have_valid_frontmatter` — для каждого из трёх: frontmatter разбирается, `name` совпадает с именем каталога и подходит под `^[a-z0-9]+(-[a-z0-9]+)*$`, `description` непустое и не длиннее 1024 символов, тело после frontmatter не короче 500 символов.
  - `test_classifier_code_keeps_the_disambiguations` — в `classifier-code/SKILL.md` есть `top_k=50`, `0096`, `0097`, `0007`, `0014`, `0004`, `0013`, `0005.0005.0056`, `0003.0009.0099` и запрет `0001.0002.0027.0126`.
  - `test_example_emails_are_placeholders` — все адреса email во всех файлах `cowork/` — в домене `example.ru`.
- [ ] **Шаг 2:** прогнать → тесты падают.
- [ ] **Шаг 3: написать тексты.** `agent-instruction.md` — содержимое блока из спецификации §5 дословно (без внешней markdown-рамки). Навыки — по §6.1, §6.2, §6.4: в frontmatter `description` — абзац «Описание (для платформы)», в теле — «Назначение», «Что внутри» и «Инструменты» развёрнутым текстом для модели. В `appeal-questions` — 3–4 синтетических разбора «обращение → вопросы». `cowork/README.md` — сборка агента в Cowork: вставить инструкцию; загрузить zip трёх навыков и `og-repeat` (из mcpOGV, `docs/skills/og-repeat.zip`); подключить mcpOGV (с тулом поиска); ссылка на чек-лист §9 спецификации.
- [ ] **Шаг 4:** прогнать → тесты проходят.
- [ ] **Шаг 5:** коммит `feat(cowork): agent instruction and three classification skills`.

---

### Задача 4: Сборка zip-навыков и вариант B

**Файлы:**
- Создать: `scripts/build_cowork_skills.py`, `cowork/skills/classifier-full/SKILL.md`
- Изменить: `.gitignore` (`cowork/dist/`)
- Тесты: `tests/test_build_cowork_skills.py`

**Интерфейсы:**
- Использует: `cowork/skills/*/SKILL.md` (задача 3), `data/classifier_flat.json`.
- Даёт: `build(out_dir: Path) -> list[Path]` в `scripts/build_cowork_skills.py` — по zip на каждый каталог `cowork/skills/*`, `SKILL.md` в корне архива; для `classifier-full` в архив кладётся сгенерированный `classifier_full.txt`.

- [ ] **Шаг 1: тесты** (сборка в `tmp_path`):
  - `test_builds_one_zip_per_skill` — 4 архива: `appeal-questions`, `applicant-contacts`, `classifier-code`, `classifier-full`.
  - `test_skill_md_at_zip_root` — в каждом архиве есть `SKILL.md` без вложенной папки.
  - `test_full_listing_has_every_leaf` — в `classifier_full.txt` ровно 1876 строк формата `КОД — полный путь`, коды только уровней 4 и 5, первая строка начинается с первого L4-кода `classifier_flat.json`, все коды уникальны.
  - `test_full_skill_states_the_count` — `classifier-full/SKILL.md` упоминает «1876» и имя файла `classifier_full.txt`.
- [ ] **Шаг 2:** прогнать → тесты падают.
- [ ] **Шаг 3: реализовать.** Запуск: `venv\Scripts\python scripts\build_cowork_skills.py [out_dir]`, по умолчанию `cowork/dist`. `classifier-full/SKILL.md`: frontmatter (`description` — «Полный перечень рубрик классификатора для варианта B проверки; применяй вместо classifier_search, когда поиск недоступен»), тело — как читать файл, выбирать только коды из него, и контрольный вопрос для оператора (§7 спецификации: три кода из конца файла, число рубрик 1876).
- [ ] **Шаг 4:** прогнать → тесты проходят; собрать в `cowork/dist` и вывести список архивов с размерами.
- [ ] **Шаг 5:** коммит `feat(cowork): build skill zips, add the full-classifier skill for variant B`.

---

### Задача 5: xlsx-шаблон ручной проверки

**Файлы:**
- Создать: `scripts/make_cowork_check_template.py`
- Тесты: `tests/test_cowork_check_template.py`

**Интерфейсы:**
- Даёт: `make_template(out: Path, summary_xlsx: Path | None = None) -> int` — пишет xlsx, возвращает число строк-обращений.

- [ ] **Шаг 1: тесты** (выход в `tmp_path`, без `summary_xlsx`):
  - `test_has_all_cases` — возвращает 16: 11 из `tests/fixtures/test_appeals_real12.json` + `vet1`…`vet5`.
  - `test_headers` — лист «Проверка» с колонками: `id`, `Набор`, `Эталонные коды`, `Текст для вставки`, `Пайплайн (Qwen3 + предобработка)`, затем для варианта A и B: `Коды агента`, `Совпало с эталоном`, `Время, с`, `История использована`, `Коды вне классификатора`, `Комментарий`.
  - `test_vet_expected_code` — у `vet1`…`vet5` эталон `0004.0015.0158.0974`.
  - `test_pipeline_column_blank_without_summary` — колонка пайплайна пустая.
  - `test_does_not_print_texts` — `capsys`: вывод не содержит ни одного текста из фикстуры (сравнить с первыми 30 символами каждого `text`, не печатая их).
- [ ] **Шаг 2:** прогнать → тесты падают.
- [ ] **Шаг 3: реализовать.** Пять синтетических ветеранских текстов — константой в скрипте, дословно (их же использовали замеры 22.10):
  1. «Прошу оказать содействие в получении удостоверения ветерана боевых действий. Мой сын участвовал в специальной военной операции.»
  2. «Не выплачены единовременные выплаты за участие в специальной военной операции. Прошу разобраться и произвести выплату.»
  3. «Прошу выдать удостоверение ветерана боевых действий, документы сданы в военкомат полгода назад, ответа нет.»
  4. «Муж вернулся из зоны СВО, выплаты за участие в боевых действиях до сих пор не получены.»
  5. «Отказали в присвоении статуса ветерана боевых действий, прошу пересмотреть решение.»

  С `summary_xlsx` (`eval_results/e2e_summary_all.xlsx`, лист «По обращениям», колонка `Qwen3 · предобработка replace`) — заполнить колонку пайплайна по `id`. Вывод в консоль — только путь и число строк. По умолчанию `out = eval_results/cowork_manual_check.xlsx` (каталог в `.gitignore`).
- [ ] **Шаг 4:** прогнать → тесты проходят; создать шаблон с `summary_xlsx`, если файл есть.
- [ ] **Шаг 5:** коммит `feat(cowork): xlsx template for the operator's manual check`.

---

### Задача 6: Итоговая проверка и передача оператору

- [ ] **Шаг 1:** полный прогон тестов → новые тесты проходят, 3 давних падения — те же.
- [ ] **Шаг 2:** локальный смоук: поднять сервис на 8010; `POST /integration/candidates` с запросом «выплаты участникам боевых действий» → 200, в выдаче есть `0004.0015.0158.0974`; `GET /integration/classifier/0004.0015.0158.0974` → `exists: true`. Сервис остановить, сообщить оператору PID и статус.
- [ ] **Шаг 3:** обновить спецификацию в `research/quality-followups-68` (§7: 1876 рубрик; §8.2: ответ сессии MCP о туле) — коммит там же.
- [ ] **Шаг 4:** сообщить оператору: что готово, где файлы (`cowork/`, `cowork/dist/*.zip`, шаблон xlsx), что нужно на стенде (обновить сервис из `int-og` или после слияния в `main`; порт; подключение mcpOGV с новым тулом в Cowork). Push `int-og` — только по просьбе оператора.

**Вне плана (стенд, оператор и сессия MCP):** тул `classifier_search` / `classifier_get` в mcpOGV; кто слушает 8010 на машине стенда; фактический хост mcpOGV; «Правила назначения прав» на стенде; перенос Qwen3 на стенд — открытые вопросы §11 спецификации.

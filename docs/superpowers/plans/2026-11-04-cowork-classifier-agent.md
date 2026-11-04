# Прототип агента-классификатора на ArioCowork — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** дать оператору всё, чтобы собрать в ArioCowork агента «Классификатор обращений» и проверить его вручную: MCP-точку `og-classifier` в сервисе классификатора, тексты инструкции и навыков, zip-навыки и xlsx-шаблон проверки.

**Architecture:** MCP-сервер (`mcp==2.2.0`, как в mcpOGV) с двумя тулами поверх уже загруженного `ClassifierAgent` монтируется внутрь существующего FastAPI-сервиса классификатора по пути `/og-classifier/mcp`, доступ по заголовку `X-MCP-Key`. Тексты агента — markdown-файлы в `cowork/`, из них скрипт собирает zip-навыки. RX-часть (mcpOGV, `og-repeat`) не трогаем — это зона сессии MCP.

**Tech Stack:** Python 3.11, FastAPI, `mcp==2.2.0` (`MCPServer`, streamable HTTP, `mcp.Client` для тестов), pytest, openpyxl.

**Spec:** `docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md` (согласована 30.10.2026; ветка `research/quality-followups-68`).

**Ветки:** план и спецификация живут в `research/quality-followups-68`; **код пишется в `int-og`** (от `main`, туда идёт сервис стенда). Task 1 переносит оба документа в `int-og`.

## Global Constraints

- Python 3.11; запуск из корня проекта; интерпретатор — `venv\Scripts\python` (системный Python без зависимостей).
- Тесты: `venv\Scripts\python -m pytest tests/ -q -p no:cacheprovider`. База на `int-og`: 457 passed, 3 давних падения `test_candidate_rerank` — не чинить, не считать регрессией.
- Библиотека MCP — `mcp==2.2.0`, точно та же версия, что в mcpOGV.
- Только чтение: тулы не пишут в RX и никуда не ходят по сети.
- Логи тулов: имя тула, время, число результатов. **Без текста запроса** (ПДн по 59-ФЗ).
- **Не читать тексты обращений граждан** (`tests/fixtures/*appeals*`, `data/*.jsonl`, `eval_results/*`): скрипты копируют их, не печатая; в выводе — только id, коды, счётчики. Примеры в навыке `appeal-questions` — **синтетические**, не из реальных обращений.
- `.env` не коммитить; секрет `OG_MCP_KEY` — только в `.env`.
- README не трогать (в `main` — только по просьбе оператора).
- RX-тулы не писать и mcpOGV не менять: нужен новый тул RX — сообщение сессии «MCP».
- Кириллицу в файлах править через Edit/Write, не через heredoc в Bash.
- После каждой задачи, меняющей сервис, сообщать оператору, поднят ли сервис 8010 с правкой (обычно — нет).

## Отступления от спецификации (решения плана)

- Путь MCP-точки: `/og-classifier/mcp` вместо `/mcp` — `/mcp` в сервисе мог бы конфликтовать с будущими маршрутами, а префикс сразу говорит, что это.
- Вариант B: в файле навыка `classifier-full` 1876 рубрик (уровни 4 и 5), а не 2108 — выбирать агент может только листья. Контрольный вопрос агенту — про 1876.
- Без `OG_MCP_KEY` точка не монтируется вовсе (404), а не отвечает 401: открытая по сети точка без секрета хуже отсутствующей.

## Review Focus

- Пустой или пробельный `query` → тул отвечает понятной ошибкой, а не пустым списком. Тест — Task 2.
- `top_k` вне диапазона (0, −5, 500) → приводится к 1…50, не падает. Тест — Task 2.
- Код в другом виде: с пробелами, три сегмента (`0003.0009.0097`), пятисегментный L5 → `classifier_get` нормализует (дополняет `.0000` до четырёх сегментов) и находит; неизвестный → `exists: false`, не ошибка. Тест — Task 2.
- Неверный или отсутствующий `X-MCP-Key` → 401; ключ не задан → точки нет (404). Тест — Task 3.
- Сервис ещё грузит модель (агент `None`) → тул отвечает «сервис классификатора ещё загружается», а не 500. Тест — Task 2.

---

### Task 1: Ветка `int-og` и зависимость `mcp`

**Files:**
- Modify: `requirements.txt` (добавить `mcp==2.2.0`)
- Add (из `research/quality-followups-68`): `docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md`, `docs/superpowers/plans/2026-11-04-cowork-classifier-agent.md`

**Interfaces:**
- Produces: установленный в `venv` пакет `mcp==2.2.0`; импорты `from mcp.server.mcpserver import MCPServer`, `from mcp.server.mcpserver.exceptions import ToolError`, `from mcp.server.transport_security import TransportSecuritySettings`, `from mcp import Client` работают.

- [ ] **Step 1:** `git switch int-og` (локальные `data/*.jsonl` при конфликте — `git stash push -- data/appeals_log.jsonl data/request_log.jsonl`); `git checkout research/quality-followups-68 -- docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md docs/superpowers/plans/2026-11-04-cowork-classifier-agent.md`.
- [ ] **Step 2:** добавить строку `mcp==2.2.0` в `requirements.txt`; `venv\Scripts\python -m pip install mcp==2.2.0`.
- [ ] **Step 3:** проверка импортов: `venv\Scripts\python -c "from mcp.server.mcpserver import MCPServer; from mcp import Client; print('ok')"` → `ok`.
- [ ] **Step 4:** полный прогон тестов → 457 passed, те же 3 падения `test_candidate_rerank` (установка `mcp` не должна сломать FastAPI/pydantic). Иначе — стоп, сообщить оператору версии конфликтующих пакетов.
- [ ] **Step 5:** commit `chore(int-og): bring the Cowork agent spec and plan, add mcp==2.2.0`.

---

### Task 2: Тулы `og-classifier`

**Files:**
- Create: `src/og_mcp.py`
- Test: `tests/test_og_mcp_tools.py`

**Interfaces:**
- Consumes: у агента — `agent._retrieve_for_segment(text: str) -> list[dict]` (кандидаты с ключами `code, name, level, full_path, parent_code, similarity`) и `agent.code_index: dict[str, dict]` (ключ — код; у уровней 1–3 коды дополнены `.0000` до четырёх сегментов, у L5 — пять сегментов).
- Produces:
  - `build_og_mcp(get_agent: Callable[[], Any]) -> MCPServer` — сервер с именем `og-classifier` и двумя тулами (аннотация только чтение);
  - `normalize_code(code: str) -> str`;
  - тул `classifier_search(query: str, top_k: int = 30) -> {"items": [{"code", "name", "level", "full_path", "score"}], "returned": int}`;
  - тул `classifier_get(code: str) -> {"code", "name", "level", "full_path", "parent_code", "exists": bool}` (при `exists: false` — только `code` и `exists`).

- [ ] **Step 1: тесты** в `tests/test_og_mcp_tools.py`. Фейковый агент — `SimpleNamespace(_retrieve_for_segment=..., code_index={...})` с тремя кандидатами (`0004.0015.0158.0974` similarity 0.61, ещё два ниже) и `code_index` с `0003.0009.0097.0000` (level 3), `0004.0015.0158.0974` (level 4), `0001.0001.0006.0013.0001` (level 5). Вызов — через `mcp.Client(server)`: `client.call_tool(name, args)`, результат `result.is_error`, `json.loads(result.content[0].text)` (как `tests/unit/mcp_fakes.py` в mcpOGV, в отдельном потоке через `anyio.run`).
  - `test_search_returns_candidates_with_score` — `items[0] == {"code": "0004.0015.0158.0974", ..., "score": 0.61}`, ключи ровно `code,name,level,full_path,score`.
  - `test_search_respects_top_k` — `top_k=2` → `returned == 2`.
  - `test_search_clamps_top_k` — `top_k=0` → `returned == 1`; `top_k=500` → тул не падает, фейку передан запрос, `returned <= 50`.
  - `test_search_rejects_empty_query` — `query="  "` → `is_error`, в тексте «пуст».
  - `test_get_finds_leaf_and_normalizes` — `" 0004.0015.0158.0974 "` → `exists is True`, `level == 4`; `"0003.0009.0097"` → `code == "0003.0009.0097.0000"`, `level == 3`; L5-код → `level == 5`.
  - `test_get_unknown_code` — `"9999.9999.9999.9999"` → `{"code": "9999.9999.9999.9999", "exists": False}`, не `is_error`.
  - `test_tools_answer_while_agent_loads` — `get_agent` возвращает `None` → оба тула `is_error`, в тексте «ещё загружается».
  - `test_tools_are_read_only` — `client.list_tools()`: ровно `{"classifier_search", "classifier_get"}`, у каждого `annotations.readOnlyHint is True`.
  - `test_query_text_not_logged` — `capsys`: вывод после `classifier_search(query="секретный текст")` не содержит «секретный».
- [ ] **Step 2:** прогнать → FAIL (`ModuleNotFoundError: og_mcp`).
- [ ] **Step 3: реализовать** `src/og_mcp.py`. `top_k` приводить `max(1, min(50, top_k))`; поиск — `agent._retrieve_for_segment(query.strip())[:top_k]`, `score = round(similarity, 3)`. `normalize_code`: `strip()`, затем дополнять сегментами `0000` до четырёх (пять сегментов не трогать). Ошибки — `ToolError` с русским текстом. Лог одной строкой: `[og-mcp] classifier_search: 30 за 0.84с`. Описания тулов (docstring) — на русском: что искать языком классификатора и что код из ответа проверять `classifier_get`.
- [ ] **Step 4:** прогнать → PASS.
- [ ] **Step 5:** commit `feat(og-mcp): classifier_search and classifier_get tools over the loaded classifier`.

---

### Task 3: HTTP-точка в сервисе и доступ по ключу

**Files:**
- Modify: `src/og_mcp.py` (добавить монтирование), `src/api_server.py` (вызвать монтирование), `src/config.py` (`OG_MCP_KEY`), `.env.example`, `docs/DEPLOY.md` (раздел «MCP-точка для ArioCowork»)
- Test: `tests/test_og_mcp_http.py`

**Interfaces:**
- Consumes: `build_og_mcp` из Task 2.
- Produces:
  - `OG_MCP_KEY: str` в `config.py` (`os.getenv("OG_MCP_KEY", "").strip()`);
  - `mount_og_mcp(app: FastAPI, get_agent: Callable[[], Any], key: str) -> bool` — `False` и ничего не монтирует, если `key` пуст; иначе монтирует по `/og-classifier`, итоговый URL `/og-classifier/mcp`.

- [ ] **Step 1: тесты** в `tests/test_og_mcp_http.py` на голом `FastAPI()` с фейковым агентом из Task 2 (реальный `api_server` не поднимать — он грузит модель). `with TestClient(app) as client:` (контекст обязателен: запускает startup). POST `/og-classifier/mcp` с JSON-RPC `{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}` и заголовками `Accept: application/json, text/event-stream`, `Content-Type: application/json`.
  - `test_lists_tools_with_the_right_key` — с `X-MCP-Key: k` → 200, в ответе оба имени тулов.
  - `test_rejects_missing_key` — без заголовка → 401.
  - `test_rejects_wrong_key` — `X-MCP-Key: wrong` → 401.
  - `test_not_mounted_without_key` — `mount_og_mcp(app, ..., "")` → `False`; POST → 404.
  - `test_other_routes_untouched` — маршрут `GET /ping`, добавленный в тестовый app, по-прежнему 200.
  - `test_service_mounts_when_key_set` — в `src/api_server.py` есть вызов `mount_og_mcp(app, lambda: agent, OG_MCP_KEY)` (проверка по исходнику, как в `test_llm_timeout.py`).
- [ ] **Step 2:** прогнать → FAIL.
- [ ] **Step 3: реализовать.** ASGI: `server.streamable_http_app(stateless_http=True, json_response=True, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))` (запросы приходят с IP-адреса машины, защиту по Host заменяет ключ), обёрнутый в middleware проверки `X-MCP-Key` через `hmac.compare_digest` (как `McpKeyMiddleware` в mcpOGV). Смонтированное приложение FastAPI сам не стартует — в `mount_og_mcp` зарегистрировать startup/shutdown-обработчики, которые входят в `server.session_manager.run()` через `contextlib.AsyncExitStack` и выходят из него. В `api_server.py` — вызов после создания `app`; агент берётся лениво (`lambda: agent`), потому что создаётся в startup. В лог при старте: `MCP-точка og-classifier: /og-classifier/mcp` или `MCP-точка og-classifier выключена: OG_MCP_KEY не задан`. `.env.example`: `OG_MCP_KEY=` с комментарием (секрет для Cowork; пусто — точки нет). `docs/DEPLOY.md`: URL `http://<хост>:<HOST_PORT>/og-classifier/mcp`, транспорт streamable HTTP, заголовок `X-MCP-Key` в поле API Key настройки MCP-сервера в Cowork; если mcpOGV на той же машине занимает 8010 — сервис на `HOST_PORT=8020`.
- [ ] **Step 4:** прогнать новые тесты → PASS; полный прогон → 457 + новые passed, те же 3 давних падения.
- [ ] **Step 5:** commit `feat(og-mcp): serve the classifier tools at /og-classifier/mcp behind X-MCP-Key`.

---

### Task 4: Тексты агента — инструкция и три навыка

**Files:**
- Create: `cowork/agent-instruction.md`, `cowork/skills/appeal-questions/SKILL.md`, `cowork/skills/applicant-contacts/SKILL.md`, `cowork/skills/classifier-code/SKILL.md`, `cowork/README.md`
- Test: `tests/test_cowork_texts.py`

**Interfaces:**
- Produces: каталог `cowork/skills/<name>/SKILL.md` с frontmatter `name` и `description` — Task 5 собирает из него zip.

- [ ] **Step 1: тесты** `tests/test_cowork_texts.py`:
  - `test_instruction_names_every_tool_and_skill` — в `agent-instruction.md` есть `get_document`, `get_document_text`, `classifier_search`, `classifier_get`, `appeal-questions`, `applicant-contacts`, `classifier-code`, `og-repeat`.
  - `test_instruction_has_the_rx_contract` — есть ключи `document_id`, `applicant_fio`, `applicant_email`, `summary`, `reasoning`, `questions`, `check` и лимиты «250» и «1000».
  - `test_skills_have_valid_frontmatter` — для каждого из трёх: frontmatter разбирается, `name` совпадает с именем каталога и подходит под `^[a-z0-9]+(-[a-z0-9]+)*$`, `description` непустое и не длиннее 1024 символов, тело после frontmatter не короче 500 символов.
  - `test_classifier_code_keeps_the_disambiguations` — в `classifier-code/SKILL.md` есть `0096`, `0097`, `0007`, `0014`, `0004`, `0013`, `0005.0005.0056`, `0003.0009.0099` и запрет `0001.0002.0027.0126`.
  - `test_example_emails_are_placeholders` — все адреса email во всех файлах `cowork/` — в домене `example.ru` (примеры синтетические, без реальных ПДн).
- [ ] **Step 2:** прогнать → FAIL.
- [ ] **Step 3: написать тексты.** `agent-instruction.md` — содержимое блока из спецификации §5 дословно (без внешних ````` ````markdown `````). Навыки — по §6.1, §6.2, §6.4: в frontmatter `description` — абзац «Описание (для платформы)», в теле — «Назначение», «Что внутри» и «Инструменты» развёрнутым текстом для модели. В `appeal-questions` — 3–4 синтетических разбора «обращение → вопросы». `cowork/README.md` — сборка агента в Cowork: вставить инструкцию; загрузить zip трёх навыков и `og-repeat` (из mcpOGV, `docs/skills/og-repeat.zip`); подключить MCP `og-classifier` (URL, транспорт, ключ) и mcpOGV; ссылка на чек-лист §9 спецификации.
- [ ] **Step 4:** прогнать → PASS.
- [ ] **Step 5:** commit `feat(cowork): agent instruction and three classification skills`.

---

### Task 5: Сборка zip-навыков и вариант B

**Files:**
- Create: `scripts/build_cowork_skills.py`, `cowork/skills/classifier-full/SKILL.md`
- Modify: `.gitignore` (`cowork/dist/`)
- Test: `tests/test_build_cowork_skills.py`

**Interfaces:**
- Consumes: `cowork/skills/*/SKILL.md` (Task 4), `data/classifier_flat.json`.
- Produces: `build(out_dir: Path) -> list[Path]` в `scripts/build_cowork_skills.py` — по zip на каждый каталог `cowork/skills/*`, `SKILL.md` в корне архива; для `classifier-full` в архив кладётся сгенерированный `classifier_full.txt`.

- [ ] **Step 1: тесты** (сборка в `tmp_path`):
  - `test_builds_one_zip_per_skill` — 4 архива: `appeal-questions`, `applicant-contacts`, `classifier-code`, `classifier-full`.
  - `test_skill_md_at_zip_root` — в каждом архиве есть `SKILL.md` без вложенной папки.
  - `test_full_listing_has_every_leaf` — в `classifier_full.txt` ровно 1876 строк формата `КОД — полный путь`, коды только уровней 4 и 5, первая строка начинается с первого L4-кода `classifier_flat.json`, все коды уникальны.
  - `test_full_skill_states_the_count` — `classifier-full/SKILL.md` упоминает «1876» и имя файла `classifier_full.txt`.
- [ ] **Step 2:** прогнать → FAIL.
- [ ] **Step 3: реализовать.** Запуск: `venv\Scripts\python scripts\build_cowork_skills.py [out_dir]`, по умолчанию `cowork/dist`. `classifier-full/SKILL.md`: frontmatter (`description` — «Полный перечень рубрик классификатора для варианта B проверки; применяй вместо classifier_search, когда поиск недоступен»), тело — как читать файл, выбирать только коды из него, и контрольный вопрос для оператора (§7 спецификации: три кода из конца файла, число рубрик 1876).
- [ ] **Step 4:** прогнать → PASS; собрать в `cowork/dist` и вывести список архивов с размерами.
- [ ] **Step 5:** commit `feat(cowork): build skill zips, add the full-classifier skill for variant B`.

---

### Task 6: xlsx-шаблон ручной проверки

**Files:**
- Create: `scripts/make_cowork_check_template.py`
- Test: `tests/test_cowork_check_template.py`

**Interfaces:**
- Produces: `make_template(out: Path, summary_xlsx: Path | None = None) -> int` — пишет xlsx, возвращает число строк-обращений.

- [ ] **Step 1: тесты** (выход в `tmp_path`, без `summary_xlsx`):
  - `test_has_all_cases` — возвращает 16: 11 из `tests/fixtures/test_appeals_real12.json` + `vet1`…`vet5`.
  - `test_headers` — лист «Проверка» с колонками: `id`, `Набор`, `Эталонные коды`, `Текст для вставки`, `Пайплайн (Qwen3 + предобработка)`, затем для варианта A и B: `Коды агента`, `Совпало с эталоном`, `Время, с`, `История использована`, `Коды вне классификатора`, `Комментарий`.
  - `test_vet_expected_code` — у `vet1`…`vet5` эталон `0004.0015.0158.0974`.
  - `test_pipeline_column_blank_without_summary` — колонка пайплайна пустая.
  - `test_does_not_print_texts` — `capsys`: вывод не содержит ни одного текста из фикстуры (сравнить с первыми 30 символами каждого `text`, не печатая их).
- [ ] **Step 2:** прогнать → FAIL.
- [ ] **Step 3: реализовать.** Пять синтетических ветеранских текстов — константой в скрипте, дословно (их же использовали замеры 22.10):
  1. «Прошу оказать содействие в получении удостоверения ветерана боевых действий. Мой сын участвовал в специальной военной операции.»
  2. «Не выплачены единовременные выплаты за участие в специальной военной операции. Прошу разобраться и произвести выплату.»
  3. «Прошу выдать удостоверение ветерана боевых действий, документы сданы в военкомат полгода назад, ответа нет.»
  4. «Муж вернулся из зоны СВО, выплаты за участие в боевых действиях до сих пор не получены.»
  5. «Отказали в присвоении статуса ветерана боевых действий, прошу пересмотреть решение.»

  С `summary_xlsx` (`eval_results/e2e_summary_all.xlsx`, лист «По обращениям», колонка `Qwen3 · предобработка replace`) — заполнить колонку пайплайна по `id`. Вывод в консоль — только путь и число строк. По умолчанию `out = eval_results/cowork_manual_check.xlsx` (каталог в `.gitignore`).
- [ ] **Step 4:** прогнать → PASS; создать шаблон с `summary_xlsx`, если файл есть.
- [ ] **Step 5:** commit `feat(cowork): xlsx template for the operator's manual check`.

---

### Task 7: Итоговая проверка и передача оператору

- [ ] **Step 1:** полный прогон тестов → новые тесты зелёные, 3 давних падения — те же.
- [ ] **Step 2:** локальный смоук: в `.env` задать `OG_MCP_KEY` (значение придумывает оператор или генерируется `python -c "import secrets;print(secrets.token_urlsafe(24))"` и вписывается в `.env`, в чат не выводить); поднять сервис на 8010; POST `tools/list` и `tools/call classifier_search` с запросом «выплаты участникам боевых действий» → 200, в выдаче есть `0004.0015.0158.0974`. Сервис после смоука остановить, сообщить оператору PID/статус.
- [ ] **Step 3:** обновить спецификацию в `research/quality-followups-68` (§8.1: путь `/og-classifier/mcp`; §7: 1876 рубрик) — commit там же.
- [ ] **Step 4:** сообщить оператору: что готово, где файлы (`cowork/`, `cowork/dist/*.zip`, шаблон xlsx), что нужно на стенде (pull `int-og` или слияние в `main`, `OG_MCP_KEY` в `.env`, порт, подключение в Cowork). Push `int-og` — только по просьбе оператора.

**Вне плана (стенд, оператор и сессия MCP):** кто слушает 8010 на машине стенда; фактический хост mcpOGV; «Правила назначения прав» на стенде; перенос Qwen3 на стенд — открытые вопросы §11 спецификации.

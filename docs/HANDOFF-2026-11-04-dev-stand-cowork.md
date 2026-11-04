# Хэндофф: развёртывание поиска для агента ArioCowork на дев-стенде, 04.11.2026

Для агента, который работает на машине дев-стенда (`governmentgenai.directum360.ru` =
`172.16.104.68`). Всё ниже проверено локально на машине разработчика; на стенде —
ещё нет. Секретов здесь нет: ключи и пароли — только в `.env` стенда.

## 0. Что нужно сделать

1. Обновить сервис классификатора на стенде из ветки **`int-og`** (не `main`).
2. Перевести его на порт **8020**, если 8010 занят mcpOGV.
3. Проверить новые точки поиска и что прежняя интеграция с RX не сломалась.
4. Сообщить адрес сессии MCP (или оператору) — там в mcpOGV задают
   `MCP_CLASSIFIER_URL`.

Сливать `int-og` в `main` **не нужно** — оператор решил оставить ветку отдельно.

## 1. Что в `int-og`

Репозиторий: https://github.com/nikitakorotaevnikita-sudo/Class-OG-Final, ветка `int-og`,
голова `5a5d482`. Это `main` (`96331c8`) плюс 8 коммитов:

| Что | Где |
|---|---|
| `POST /integration/candidates` — топ-50 рубрик классификатора по фразе (первая стадия пайплайна, без LLM) | `src/candidates_api.py`, подключён в `src/integration_api.py` |
| `GET /integration/classifier/{code}` — проверка кода | там же |
| Тексты агента Cowork: системные инструкции A и B, навыки `appeal-questions`, `applicant-contacts`, `classifier-code`, `classifier-full` | `cowork/` |
| Сборка zip-навыков | `scripts/build_cowork_skills.py` → `cowork/dist/` |
| xlsx-шаблон ручной проверки оператора | `scripts/make_cowork_check_template.py` |
| Спецификация, план | `docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md`, `docs/superpowers/plans/2026-11-04-cowork-classifier-agent.md` |

Из `main` в ветке уже есть: `LLM_MAX_RETRIES` (число попыток к LLM, по умолчанию 5),
`LLM_TIMEOUT_SEC=360`, повтор при таймауте.

Контракт точек — `docs/INTEGRATION.md`, раздел «Кандидаты для агента ArioCowork».
Согласован с сессией MCP: поля и коды ответов не менять без неё.

## 2. Как устроено целиком

```
Агент Cowork ──MCP──► mcpOGV (8010) ──HTTP──► классификатор (8020)
                       │                       /integration/candidates
                       │                       /integration/classifier/{code}
                       └── тулы RX (get_document, get_citizen_request_status …) ──► RX OData
```

- mcpOGV — репозиторий `MCP-Directum-RX`, ветка `feature/mcp-ogv`, коммит `7221a55`
  (на GitHub). Новые тулы `classifier_search` / `classifier_get` появляются в нём,
  только когда задан `MCP_CLASSIFIER_URL`. Это зона сессии MCP — mcpOGV не трогать.
- Поиск — текущая связка стенда: `intfloat/multilingual-e5-base` + база
  `data/vector_db_adapted_v3`. **Qwen3 не переносим** (решение оператора 04.11).

## 3. Развёртывание

Из папки проекта на стенде.

**Перед обновлением:**

1. Кто слушает 8010 (Windows — `netstat -ano | findstr :8010`, Linux —
   `ss -ltnp | grep 8010`). Если mcpOGV — классификатор идёт на 8020.
2. Проверить `.env` стенда (не коммитить его, значения ключей не выводить):
   - `TOP_K_CANDIDATES=50` — **обязательно**: поиск режет выдачу до этого значения,
     по умолчанию в коде 10, и агент получил бы 10 кандидатов вместо 50;
   - `HOST_PORT=8020`, если 8010 занят;
   - `VECTOR_DB_DIR=data/vector_db_adapted_v3`, `EMBEDDING_MODEL` не задан или e5;
   - `LLM_MAX_RETRIES` можно не задавать (5); при медленной модели — 2.
3. Запомнить текущую ветку и коммит (`git branch --show-current`, `git log -1 --oneline`)
   — для отката.

**Обновление:**

```bash
git fetch origin
git switch int-og
git pull origin int-og
docker compose build classifier
docker compose up -d classifier
```

Если на стенде локальные изменения мешают переключению — **не удалять их**:
`git stash push -- <файлы>` и сказать оператору. `data/*.jsonl` — журналы с текстами
обращений, их нельзя терять и нельзя коммитить.

**Откат:** `git switch <прежняя ветка>`, затем те же `docker compose build` и `up -d`.

## 4. Проверка после развёртывания

Порт ниже — 8020 (или 8010, если оставили).

1. Живость: `curl http://127.0.0.1:8020/health` → `"agent_ready": true`,
   `"classifier_entries": 2108`. Модель грузится до минуты.
2. Поиск. Кириллицу в `curl -d '…'` из Git Bash/PowerShell легко испортить (ответ 400
   «There was an error parsing the body») — тело лучше класть в файл UTF-8:

   ```bash
   printf '{"query": "выплаты участникам боевых действий", "top_k": 50}' > /tmp/q.json
   curl -X POST http://127.0.0.1:8020/integration/candidates -H "Content-Type: application/json" --data-binary @/tmp/q.json
   ```

   Ожидание: `returned` = 50, среди `items` — `0004.0015.0158.0974` (локально был на
   2-м месте). Первый запрос после старта ~5 с, дальше ~1 с.
3. Проверка кода: `curl http://127.0.0.1:8020/integration/classifier/0004.0015.0158.0974`
   → `"exists": true`; `…/9999.9999.9999.9999` → `"exists": false` (код 200).
4. Пустой запрос `{"query": " "}` → 400 «Запрос пуст: передайте фразу для поиска».
5. В логе контейнера **нет** строки `[candidates] поиск ограничен TOP_K_CANDIDATES=…`.
   Есть — в `.env` меньше 50, поправить и `docker compose up -d classifier`.
6. Прежняя интеграция RX не сломалась: `/integration/classify-document` и
   асинхронная пара `/integration/classify-document-async` + `/integration/jobs/{id}`
   отвечают, как раньше (проверять на тестовом тексте без персональных данных).
7. RX и mcpOGV видят новый порт: если классификатор переехал на 8020, у RX адрес
   сервиса тоже меняется — сказать оператору.

## 5. После проверки

- Сообщить сессии MCP (или оператору): адрес `http://172.16.104.68:8020`. В mcpOGV
  задать `MCP_CLASSIFIER_URL` на него (`MCP_CLASSIFIER_TIMEOUT_SECONDS=15`), перезапустить
  mcpOGV — тулы `classifier_search` и `classifier_get` появятся у агента.
- Собрать zip-навыки: `python scripts/build_cowork_skills.py` → `cowork/dist/` (4
  архива). Передать оператору вместе с `cowork/agent-instruction.md` и
  `cowork/agent-instruction-b.md`; порядок сборки агента — `cowork/README.md`.
- Ручную проверку агента в Cowork делает оператор сам (§9 спецификации).

## 6. Правила

- **Не читать тексты обращений граждан** (`data/*.jsonl`, `data/jobs/`,
  `tests/fixtures/*appeals*`, `eval_results/`). В отчётах — только id, коды, метрики.
- `.env` не коммитить и значения ключей не выводить; пароли за пользователя не вводить.
- README в `main` — только по просьбе оператора.
- mcpOGV и тулы RX — зона сессии MCP: нужно изменение — сообщение ей.
- Push — только по просьбе оператора.
- После изменений сообщать оператору, поднят ли сервис с правкой: порт, `/health`.

## 7. Известные риски

- Синхронный `/integration/classify-document` занимает сервис целиком: пока он идёт,
  запросы агента к `/integration/candidates` ждут и могут не уложиться в таймаут
  mcpOGV 15 с — агент увидит «Классификатор недоступен». Во время ручной проверки
  агента лучше не гонять синхронную классификацию.
- Системный прокси на машине может перехватывать внутренние адреса (503 / отказ
  соединения). Для RX и LLM в коде есть обход (`RX_VIA_PROXY`, `LLM_VIA_PROXY`); для
  проверочных `curl` — `--noproxy '*'`.
- Docker Desktop держит свой прокси: из контейнера RX может быть недоступен
  (`docs/DEPLOY.md`, раздел «Известные грабли»).

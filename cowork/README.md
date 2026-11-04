# Агент «Классификатор обращений» для ArioCowork

Тексты и навыки агента, который классифицирует обращения граждан в ArioCowork.
Проект — `docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md`.

## Что здесь

| Файл | Куда в Cowork |
|---|---|
| `agent-instruction.md` | поле системной инструкции агента — целиком |
| `skills/appeal-questions/` | навык: выделение вопросов |
| `skills/applicant-contacts/` | навык: ФИО и email заявителя |
| `skills/classifier-code/` | навык: подбор кода классификатора |
| `skills/classifier-full/` | навык варианта B: полный перечень рубрик вместо поиска |

Zip-архивы навыков собирает `scripts/build_cowork_skills.py` в `cowork/dist/`.

## Как собрать агента

1. Создать агента «Классификатор обращений», модель Qwen3.8-27B (Ario).
2. Вставить `agent-instruction.md` в поле системной инструкции.
3. Загрузить навыки из `cowork/dist/`: `appeal-questions.zip`,
   `applicant-contacts.zip`, `classifier-code.zip`.
4. Загрузить готовый навык `og-repeat` из mcpOGV (репозиторий `MCP-Directum-RX`,
   ветка `feature/mcp-ogv`, `docs/skills/og-repeat.zip`).
5. Подключить MCP-сервер mcpOGV (streamable HTTP, ключ `X-MCP-Key`). В нём должны
   быть тулы `classifier_search` и `classifier_get`. Их mcpOGV регистрирует, только
   когда задан `MCP_CLASSIFIER_URL` — адрес этого сервиса классификатора.
6. Проверить вручную по чек-листу §9 спецификации. Таблица для записи результатов —
   `scripts/make_cowork_check_template.py`.

Вариант B для сравнения: вместо `classifier-code` загрузить `classifier-full.zip`.
Перед сравнением спросить агента о трёх последних кодах файла и о числе рубрик
(1876) — если не знает, файл до модели целиком не доходит и сравнение не имеет
смысла.

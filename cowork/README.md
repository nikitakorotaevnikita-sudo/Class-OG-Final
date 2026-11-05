# Агент «Классификатор обращений» для ArioCowork

Тексты и навыки агента, который классифицирует обращения граждан в ArioCowork.
Проект — `docs/superpowers/specs/2026-10-29-cowork-classifier-agent-design.md`.

## Что здесь

| Файл | Куда в Cowork |
|---|---|
| `agent-instruction.md` | поле системной инструкции агента — целиком (вариант A, поиск) |
| `agent-instruction-b.md` | то же для варианта B (полный перечень рубрик вместо поиска) |
| `skills/appeal-questions/` | навык: выделение вопросов |
| `skills/applicant-contacts/` | навык: ФИО и email заявителя |
| `skills/classifier-code/` | навык: подбор кода классификатора |
| `skills/classifier-full/` | навык варианта B: полный перечень рубрик вместо поиска |

Готовые архивы — `docs/skills/`: по `ИМЯ.zip` и `ИМЯ.skill` на навык, как у навыков
mcpOGV. Пересобирает их `scripts/build_cowork_skills.py`.

## Как собрать агента

1. Создать агента «Классификатор обращений», модель Qwen3.8-27B (Ario).
2. Вставить `agent-instruction.md` в поле системной инструкции.
3. Загрузить навыки из `docs/skills/`: `appeal-questions.zip`,
   `applicant-contacts.zip`, `classifier-code.zip`.
4. Загрузить готовый навык `og-repeat` из mcpOGV (репозиторий `MCP-Directum-RX`,
   ветка `feature/mcp-ogv`, `docs/skills/og-repeat.zip`).
5. Подключить MCP-сервер mcpOGV (streamable HTTP, ключ `X-MCP-Key`). В нём должны
   быть тулы `classifier_search` и `classifier_get`. Их mcpOGV регистрирует, только
   когда задан `MCP_CLASSIFIER_URL` — адрес этого сервиса классификатора.
6. Проверить вручную по чек-листу §9 спецификации. Таблица для записи результатов —
   `scripts/make_cowork_check_template.py`.

Вариант B для сравнения — отдельный агент или та же настройка с заменой двух вещей:
системная инструкция — `agent-instruction-b.md`, навык `classifier-code` —
`classifier-full.zip`. Инструкция A требует коды только из `classifier_search`, поэтому
с ней вариант B проверял бы снова поиск. Перед сравнением спросить агента о трёх последних кодах файла и о числе рубрик
(1876) — если не знает, файл до модели целиком не доходит и сравнение не имеет
смысла.

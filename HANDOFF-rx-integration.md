# HANDOFF — Интеграция классификатора с Directum RX (MVP)

> Документ для продолжения работы **на машине, где установлены зависимости
> проекта** (Python 3.11 + ML-стек). Дизайн и план готовы; реализация кода —
> НЕ начата. Здесь всё, чтобы продолжить без потери контекста.

## 1. Что нужно сделать (суть задачи)

MVP-сервис интеграции: RX-процесс дёргает наш REST-эндпоинт, передавая **id
документа**. Сервис сам тянет документ из RX по OData, извлекает текст,
классифицирует и возвращает JSON:

```
POST /integration/classify-document
Запрос:  { "document_id": 26 }
Ответ:   {
  "document_id": 26,
  "applicant_fio": "Иванов И.И.",        // ФИО заявителя, формат «Фамилия И.О.» или null
  "summary": "краткая суть ≤250 символов",
  "questions": [ { "code": "0001.0002.0015.0042", "question": "наименование текстом" } ]
}
```

Модель взаимодействия — **pull (Вариант B)**: RX даёт только id, текст качаем мы.

## 2. Текущий статус (workflow superpowers)

| Этап | Статус |
|---|---|
| Brainstorming (дизайн) | ✅ готово, утверждён |
| Спека | ✅ `docs/superpowers/specs/2026-07-08-rx-integration-classify-document-design.md` |
| План реализации | ✅ `docs/superpowers/plans/2026-07-08-rx-integration-classify-document.md` |
| Реализация кода (Tasks 1–6) | ⛔ НЕ начата (заблокировано окружением на текущей машине) |

**Ветка:** `feat/rx-integration-classify-document` (от `main` @ `62e06bd`).
Коммиты пока только docs: спека + план + этот хэндофф.

## 3. Почему остановились здесь

Текущая машина (Windows Server 2019) не подходит для сборки:
- Только Python **3.13** и **3.14** (в `C:\DirectumLauncher\tools\Python`), оба
  несовместимы с ML-стеком (см. CLAUDE.md: «3.12+ ломает ML-зависимости»).
- Python 3.11 отсутствует; установка MSI **запрещена политикой** (exit 1625),
  прав администратора нет; `winget` недоступен.

Поэтому переносим реализацию на машину с рабочим окружением проекта (откуда
делались последние коммиты — там `venv` 3.11 и модели уже есть).

## 4. Подготовка окружения на целевой машине

```powershell
# из корня проекта
# 1) venv 3.11 + зависимости (скрипт уже это делает)
.\setup.ps1
# 2) добавить pytest (его нет в requirements.txt)
.\venv\Scripts\python.exe -m pip install pytest
# 3) sanity: классификатор импортируется
.\venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); import classifier_agent; print('OK')"
# 4) при первом запуске нужна векторная база (если ещё не собрана):
.\venv\Scripts\python.exe src\build_vectordb.py
```

Все команды плана (`python -m pytest ...`, `uvicorn ...`) выполнять через
`.\venv\Scripts\python.exe` (или активировав venv).

## 5. Как продолжить реализацию

План разбит на 6 TDD-задач (полный код приведён в каждом шаге плана):

1. **Конфиг RX** — `RX_ODATA_URL/RX_USER/RX_PASSWORD` в `src/config.py` + `.env.example`.
2. **`src/fio_extractor.py`** — `normalize_fio()` → «Фамилия И.О.».
3. **Доп. поля классификации** — `extract_extra_fields()` + поля `applicant_fio`,
   `summary` в `ClassificationResult`; правка промпта LLM.
4. **`src/rx_client.py`** — `get_document_text(document_id)` (id→версия→тело→текст).
5. **`src/integration_api.py`** — эндпоинт `/integration/classify-document`,
   подключение к `api_server.py` через `app.state.agent`.
6. **Смоук на стенде** + отладка OData-500 + выдача адреса сервиса.

Исполнять через skill **`superpowers:subagent-driven-development`** (как и
задумано) либо `superpowers:executing-plans`. Начать с чтения плана и создания
todo по задачам.

### Заметка по Task 3 (важно)
Была идея вынести `extract_extra_fields` в лёгкий модуль
`src/classification_fields.py`, чтобы тест не тянул ML-стек (это был обходной
путь под 3.13). **На машине с полными зависимостями это НЕ требуется** — план
как есть (функция в `classifier_agent.py`) полностью рабочий. Вынос опционален
как чистка.

## 6. Что проверено на стенде RX (факты, готовые к использованию)

- **OData:** `http://172.16.96.98/integration/odata/`, Basic-auth `Administrator:11111`.
- Документ по id читается: `GET IElectronicDocuments(26)` → метаданные, версия `Id:2`.
- Классификатор **уже загружен в RX** нативно, тот же `FullCode`, что и у нас:
  `ISections`(6)→`ITopics`(23)→`IThemes`(208)→`IQuestions`→`ISubQuestions`(651).
  Проверено: `IQuestions.FullCode == "0001.0001.0001.0001"` резолвится. Наш код L4
  == `IQuestions.FullCode`.
- Предмет ведения: `ICitizenRequestsAuthorityMatters` (4 значения).
- Полные факты интеграции — в памяти проекта: файл памяти `rx-integration-odata`.

## 7. ⚠️ Открытый риск (Task 6)

Скачивание **тела** документа через Integration OData на стенде даёт
`500 «Произошла непредвиденная ошибка»`:
```
GET .../IElectronicDocuments(26)/Versions(2)/Body/$value    → 500
GET .../PublicBody/$value                                   → 500
```
Метаданные и список версий тянутся нормально — падает именно бинарь тела.
План отладки (Task 6, step 3): (1) права интеграционного пользователя;
(2) альтернативные пути тела/`PublicBody`; (3) путь через `/Web/api`;
(4) отдать тело как base64-свойство `Body.Value`. Инкапсулировано в
`rx_client._fetch_body` — на контракт API не влияет.

## 8. Ссылки

- Спека: `docs/superpowers/specs/2026-07-08-rx-integration-classify-document-design.md`
- План: `docs/superpowers/plans/2026-07-08-rx-integration-classify-document.md`
- Память проекта: `rx-integration-odata` (endpoint, creds, маппинг сущностей)
- Деливери после сборки: сообщить оператору адрес
  `http://<IP>:8000/integration/classify-document` (+ Swagger `/docs`).

# Быстрый старт — AI-классификатор обращений граждан

## Предварительные требования

- Python 3.11
- Docker (опционально)
- Браузер для веб-интерфейса

## Вариант A: Docker (рекомендуется)

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd citizens-appeals-classifier

# 2. Создать .env с ключом
cp .env.example .env
# Заполнить GROQ_API_KEY=<ваш-ключ>

# 3. Запустить
docker-compose up -d

# 4. Открыть
open http://localhost:8000
```

## Вариант B: Локально

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Создать .env
cp .env.example .env
# Заполнить GROQ_API_KEY

# 3. Построить векторную базу (один раз)
python src/build_vectordb.py

# 4. Запустить сервер
uvicorn src.api_server:app --host 0.0.0.0 --port 8000

# 5. Открыть
open http://localhost:8000
```

## Первый запрос

Через веб-интерфейс: выберите пример из списка и нажмите "Классифицировать".

Через API:

```bash
curl -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -d '{"appeal_text": "Прошу провести ремонт дороги по улице Ленина, 15"}'
```

## Остановка

```bash
# Docker
docker-compose down

# Локально
# Ctrl+C
```
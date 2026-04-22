FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости отдельно для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код и данные
COPY src/ ./src/
COPY data/classifier_flat.json ./data/
COPY data/classifier_hierarchy.json ./data/
COPY data/vector_db/ ./data/vector_db/
COPY .env.example .env

EXPOSE 8000

CMD ["uvicorn", "src.api_server:app", "--host", "0.0.0.0", "--port", "8000"]

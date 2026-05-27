FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости отдельно для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код и данные
COPY src/ ./src/
COPY data/classifier_flat.json ./data/
COPY data/classifier_hierarchy.json ./data/
COPY data/test_appeals.json ./data/
COPY data/vector_db/ ./data/vector_db/
COPY data/vector_db_adapted/ ./data/vector_db_adapted/
COPY data/allowed_codes_top69.json ./data/allowed_codes_top69.json
COPY models/adapter_v1.npz ./models/adapter_v1.npz
COPY models/adapter_v1.json ./models/adapter_v1.json
COPY models/ltr_v1.json ./models/ltr_v1.json
COPY .env.example .env

EXPOSE 8005

CMD ["uvicorn", "src.api_server:app", "--host", "0.0.0.0", "--port", "8005"]

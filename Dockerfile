# Версия Python вынесена в ARG: прод собирается на 3.11 (проверенная база),
# совместимость с 3.13 проверяется той же сборкой через --build-arg.
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

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
COPY data/vector_db_adapted_v3/ ./data/vector_db_adapted_v3/
COPY data/allowed_codes_top69.json ./data/allowed_codes_top69.json
COPY models/adapter_v1.npz ./models/adapter_v1.npz
COPY models/adapter_v1.json ./models/adapter_v1.json
COPY models/ltr_v1.json ./models/ltr_v1.json
COPY .env.example .env

EXPOSE 8005

CMD ["uvicorn", "src.api_server:app", "--host", "0.0.0.0", "--port", "8005"]

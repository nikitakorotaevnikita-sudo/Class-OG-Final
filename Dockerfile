# Версия Python вынесена в ARG: прод собирается на 3.11 (проверенная база),
# совместимость с 3.13 проверяется той же сборкой через --build-arg.
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app

# OpenMP: на debian-slim без libgomp1 numpy/torch падают с «libgomp.so.1 not found».
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# CPU-torch по умолчанию. pip из requirements иначе тянет CUDA (~8 ГБ) —
# бесполезно на Linux-сервере без GPU. Для CUDA:
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 .
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir torch --index-url ${TORCH_INDEX_URL}

# Устанавливаем зависимости отдельно для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код и данные
COPY src/ ./src/
# Скрипты БД (init_db, migrate_from_files) — их запускает сервис migrate;
# deploy/ — init-SQL и шаблоны развёртывания.
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/
COPY data/classifier_flat.json ./data/
COPY data/classifier_hierarchy.json ./data/
COPY data/test_appeals.json ./data/
# Боевая база — adapted_v3 (на неё указывает VECTOR_DB_DIR в .env.example; сервис
# migrate импортирует её векторы в PostgreSQL как версию модели v0).
# Каталог data/vector_db в .gitignore, на чистом клоне его нет, и COPY по нему
# ронял сборку на стенде.
COPY data/vector_db_adapted_v3/ ./data/vector_db_adapted_v3/
COPY data/allowed_codes_top69.json ./data/allowed_codes_top69.json
COPY models/adapter_v1.npz ./models/adapter_v1.npz
COPY models/adapter_v1.json ./models/adapter_v1.json
COPY models/ltr_v1.json ./models/ltr_v1.json
COPY .env.example .env

# src/ — корень импорта, модули разложены по пакетам (`from core import config`,
# `worker.run`) — нужно и воркерам, и скриптам миграции.
ENV PYTHONPATH=/app/src

EXPOSE 8005

# По умолчанию — API. Воркеры и migrate переопределяют command в docker-compose.yml.
CMD ["uvicorn", "api.api_server:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8005"]

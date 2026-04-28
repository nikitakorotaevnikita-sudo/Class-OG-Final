# =============================================================================
# ШАБЛОН DOCKERFILE
# =============================================================================
# Multi-stage build: production-образ БЕЗ Playwright.
# Playwright устанавливается только в test-образе.
#
# IMPORTANT: НЕ ставить Playwright и Chromium в production-образ —
# это +сотни МБ и десятки системных пакетов, которые не нужны в runtime.
# =============================================================================

# --- PRODUCTION ---
FROM python:3.10-slim AS production

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# TEMPLATE: Скопируй .env.example если нужен
# COPY .env.example .env.example

COPY src/ src/

RUN mkdir -p /app/data

ENV DATA_DIR=/app/data
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]


# --- TEST (поверх production) ---
# Использование: docker build --target test -t myapp-test .
FROM production AS test

COPY tests/ tests/
COPY pytest.ini .

# TEMPLATE: Раскомментируй если используешь .coveragerc
# COPY .coveragerc .

# Playwright + Chromium (только для E2E тестов)
RUN pip install --no-cache-dir playwright pytest-playwright && \
    apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/* && \
    playwright install chromium

CMD ["pytest", "--cov=src", "tests/", "-v"]

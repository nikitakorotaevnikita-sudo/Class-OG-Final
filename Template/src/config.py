"""Конфигурация приложения: чтение переменных окружения.

TEMPLATE: Добавь проектно-специфичные переменные окружения по аналогии.
"""

import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# Загружаем .env из корня проекта
load_dotenv(find_dotenv(), override=True)


def get_openai_api_key() -> str:
    """Возвращает API-ключ OpenAI."""
    return os.getenv("OPENAI_API_KEY", "")


def get_openai_model() -> str:
    """Возвращает название модели OpenAI."""
    return os.getenv("OPENAI_MODEL", "gpt-4o")


def get_openai_server() -> str | None:
    """Возвращает кастомный endpoint OpenAI (или None для стандартного)."""
    server = os.getenv("OPENAI_SERVER", "").strip()
    return server if server else None


def get_data_dir() -> str:
    """Возвращает путь к директории с данными (тестовые файлы и т.п.)."""
    return os.getenv("DATA_DIR", str(Path(__file__).parent.parent / "data"))


def get_port() -> int:
    """Возвращает порт приложения."""
    return int(os.getenv("PORT", "8000"))


def get_directum_base_url() -> str | None:
    """Возвращает базовый URL Directum RX API."""
    url = os.getenv("DIRECTUM_BASE_URL", "").strip()
    return url if url else None


def get_directum_token() -> str | None:
    """Возвращает Bearer-токен для Directum RX API."""
    token = os.getenv("DIRECTUM_TOKEN", "").strip()
    return token if token else None


def get_backoffice_credentials() -> tuple[str, str]:
    """Возвращает (логин, пароль) для Basic Auth бэкофиса."""
    user = os.getenv("BACKOFFICE_USER", "admin").strip()
    password = os.getenv("BACKOFFICE_PASSWORD", "admin").strip()
    return user, password

"""Адрес PostgreSQL проекта по умолчанию.

Если DATABASE_URL не задан (или пуст), он собирается из тех же ключей, что и
контейнер docker compose: POSTGRES_USER/PASSWORD/DB/PORT. Так config (operator_cli,
test_agent, launch check) и start-скрипты смотрят на один и тот же порт хоста.

Учётные данные экранируются (urllib.parse.quote): пароль с @ / : # не ломает URL.
Модуль без побочных эффектов — его импортируют config.py и
scripts/setup_infra_env.py (установщики).
"""

from typing import Mapping
from urllib.parse import quote


def _value(env: Mapping[str, str], key: str, default: str) -> str:
    value = (env.get(key) or "").strip()
    return value or default


def _q(value: str) -> str:
    return quote(value, safe="")


def build_database_url(env: Mapping[str, str]) -> str:
    """URL PostgreSQL проекта на localhost из POSTGRES_* (DATABASE_URL не смотрится)."""
    return "postgresql://{}:{}@localhost:{}/{}".format(
        _q(_value(env, "POSTGRES_USER", "classifier")),
        _q(_value(env, "POSTGRES_PASSWORD", "classifier")),
        _value(env, "POSTGRES_PORT", "5432"),
        _q(_value(env, "POSTGRES_DB", "classifier")),
    )


def database_url(env: Mapping[str, str]) -> str:
    """DATABASE_URL, если задан, иначе собранный из POSTGRES_*."""
    return _value(env, "DATABASE_URL", "") or build_database_url(env)

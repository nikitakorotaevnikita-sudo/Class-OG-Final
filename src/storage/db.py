"""Пул соединений PostgreSQL и применение схемы.

Пул создаётся лениво: импорт модуля к БД не подключается.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_pool: Optional[ConnectionPool] = None
_lock = threading.Lock()


def _configure(conn: psycopg.Connection) -> None:
    # register_vector требует, чтобы расширение vector уже существовало.
    register_vector(conn)
    conn.commit()


def get_pool() -> ConnectionPool:
    """Ленивый singleton пула; pgvector регистрируется на каждом соединении."""
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = ConnectionPool(
                    config.DATABASE_URL,
                    min_size=1,
                    max_size=config.DB_POOL_MAX,
                    configure=_configure,
                    open=True,
                )
    return _pool


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """Соединение из пула в одной транзакции (commit при выходе, rollback при ошибке)."""
    with get_pool().connection() as conn:
        yield conn


def apply_schema(conn: psycopg.Connection) -> None:
    """Применяет schema.sql (идемпотентно, расширения уже могут существовать)."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def close_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None

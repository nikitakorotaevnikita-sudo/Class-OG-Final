"""Фикстуры для тестов слоя хранения (PostgreSQL)."""

from contextlib import contextmanager

import pytest


@pytest.fixture(params=["memory", "pg"])
def storage(request):
    from storage.memory import MemoryStorage
    from storage.pg import PgStorage

    if request.param == "memory":
        return MemoryStorage()

    from pgvector.psycopg import register_vector

    pg_conn = request.getfixturevalue("pg_conn")
    register_vector(pg_conn)

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    return PgStorage(connect)

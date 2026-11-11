"""Фикстуры для тестов слоя хранения (PostgreSQL)."""

import os
import uuid

import psycopg
import pytest


@pytest.fixture
def pg_conn():
    import config
    from storage import db

    url = os.getenv("TEST_DATABASE_URL") or config.DATABASE_URL
    try:
        conn = psycopg.connect(url, connect_timeout=2, autocommit=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL недоступен: {exc}")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        # public остаётся в пути: там могут жить уже созданные расширения (vector, pgcrypto)
        conn.execute(f"SET search_path TO {schema}, public")
        db.apply_schema(conn)
        yield conn
    finally:
        try:
            conn.execute("SET search_path TO public")
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            conn.close()

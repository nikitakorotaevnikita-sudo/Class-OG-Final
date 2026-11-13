"""conftest.py — общие fixtures для pytest."""

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def pytest_configure(config):
    """Ensure api_server imports work without triggering ML startup."""
    pass


@pytest.fixture(autouse=True)
def _memory_storage_backend(monkeypatch):
    """get_storage() в тестах — свежий MemoryStorage (никогда не прод-БД)."""
    from core import config
    from storage import appeals_logger
    from storage import reset_storage

    monkeypatch.setattr(config, "STORAGE_BACKEND", "memory")
    monkeypatch.setattr(appeals_logger, "_logger", None)
    reset_storage()
    yield
    reset_storage()


@pytest.fixture
def pg_conn():
    import psycopg
    from storage import db

    # Только явно заданная тестовая БД: тесты создают/удаляют схемы, и откат к
    # config.DATABASE_URL мог бы задеть рабочую базу с персональными данными.
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан — тесты PostgreSQL пропущены")
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


@pytest.fixture(autouse=True)
def _rx_harness_cleanup():
    """Закрыть клиенты, созданные tests/rx_harness.make_client."""
    yield
    if "rx_harness" in sys.modules:
        sys.modules["rx_harness"].cleanup()

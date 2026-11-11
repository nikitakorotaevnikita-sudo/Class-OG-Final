"""Тесты PostgreSQL ходят только в явно заданную TEST_DATABASE_URL."""
import pytest


def test_pg_conn_skips_without_test_database_url(monkeypatch, request):
    import psycopg

    import config

    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://prod:secret@prod-host:5432/prod")

    calls = []

    def no_connect(*a, **_k):
        calls.append(a)
        raise AssertionError("pg_conn не должен подключаться")

    monkeypatch.setattr(psycopg, "connect", no_connect)
    with pytest.raises(pytest.skip.Exception, match="TEST_DATABASE_URL не задан"):
        request.getfixturevalue("pg_conn")
    assert calls == []

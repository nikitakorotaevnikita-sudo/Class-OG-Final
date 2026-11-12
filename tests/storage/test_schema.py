import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "src" / "storage" / "schema.sql"
DEPLOY = ROOT / "deploy" / "db" / "init" / "002_schema.sql"

TABLES = [
    "classifier_entries", "classifier_vectors", "appeal_classifications",
    "appeal_questions", "appeal_verifications", "historical_appeals",
    "ml_model_registry", "jobs", "llm_request_log", "schema_meta",
]


def test_deploy_schema_matches_canonical():
    assert DEPLOY.read_bytes() == SCHEMA.read_bytes()


def test_schema_declares_all_tables():
    text = SCHEMA.read_text(encoding="utf-8")
    for name in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {name}" in text, name


@pytest.mark.db
def test_apply_schema_twice_is_idempotent(pg_conn):
    from storage import db

    db.apply_schema(pg_conn)
    db.apply_schema(pg_conn)
    count = pg_conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchone()[0]
    assert count == 10


def test_config_defaults(monkeypatch):
    import dotenv

    import config

    for key in ("SYNC_WAIT_TIMEOUT_SEC", "ENABLE_ANNOTATION_VECTORS", "WEBHOOK_ALLOWED_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    try:
        importlib.reload(config)
        assert config.SYNC_WAIT_TIMEOUT_SEC == 1800
        assert config.ENABLE_ANNOTATION_VECTORS is False
        assert config.WEBHOOK_ALLOWED_HOSTS == []
    finally:
        monkeypatch.undo()
        importlib.reload(config)


@pytest.mark.db
def test_schema_supersedes_extra_active_finetunes_before_unique_index(pg_conn):
    """Старая БД без one_active_finetune с двумя активными дообучениями: схема применяется."""
    from storage import db

    pg_conn.execute("DROP INDEX one_active_finetune")
    for jid, minutes in (("a" * 32, 10), ("b" * 32, 5), ("c" * 32, 1)):
        pg_conn.execute(
            """INSERT INTO jobs (id, job_type, status, payload, created_at)
               VALUES (%s, 'finetune', %s, '{}', now() - make_interval(mins => %s))""",
            (jid, "running" if jid == "a" * 32 else "queued", minutes),
        )
    db.apply_schema(pg_conn)
    db.apply_schema(pg_conn)  # повторно — без изменений
    rows = dict(pg_conn.execute(
        "SELECT id, status || ':' || coalesce(error, '') FROM jobs ORDER BY id").fetchall())
    # выполняющееся (running) дообучение сохраняется, даже если оно старше queued
    assert rows == {"a" * 32: "running:", "b" * 32: "error:superseded",
                    "c" * 32: "error:superseded"}
    assert pg_conn.execute(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'one_active_finetune' "
        "AND schemaname = current_schema()").fetchone()[0] == 1

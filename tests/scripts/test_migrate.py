"""Миграция файлового хранилища в слой Storage (синтетические фикстуры)."""

import importlib.util
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from storage.memory import MemoryStorage

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "migration"
_spec = importlib.util.spec_from_file_location(
    "migrate_from_files", ROOT / "scripts" / "db" / "migrate_from_files.py"
)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

CODE = "0001.0001.0001.0001"
CARD_A = "11111111-2222-4333-8444-555555555555"
CARD_B = str(uuid.uuid5(uuid.NAMESPACE_URL, "legacy-appeal:abc-123"))
JOB_DONE = "a" * 32
JOB_RUN = "b" * 32


def run(storage):
    return mig.migrate(
        storage, FIX, FIX / "vector_db", FIX / "models", "intfloat/multilingual-e5-base"
    )


@pytest.fixture
def mem():
    return MemoryStorage()


@pytest.fixture
def pg_storage(pg_conn):
    from pgvector.psycopg import register_vector
    from storage.pg import PgStorage

    register_vector(pg_conn)

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    return PgStorage(connect)


def snapshot(s):
    return {
        "entries": s.classifier.count_entries(),
        "vectors": sorted(s.classifier.vectors_for_version(0)),
        "log": s.appeals.as_log_entries(),
        "llm": s.appeals.llm_log_count(),
        "hist": s.historical.count(),
        "jobs": [(j.id, j.status, j.error) for j in s.jobs.list(limit=100)],
        "models": [(m.version, m.is_active) for m in s.models.list()],
    }


def test_migration_counts(mem):
    rec = run(mem)
    assert all(a == b for a, b in rec.values()), rec

    assert mem.classifier.count_entries() == 3
    root = [e for e in mem.classifier.load_entries() if e.level == 1][0]
    assert root.parent_code is None

    vecs = mem.classifier.vectors_for_version(0)
    assert len(vecs) == 4
    assert sum(1 for v in vecs if v[1] == "base") == 3
    assert mem.classifier.annotations_for(CODE) == ["note: x"]
    assert mem.classifier.pending_annotations(0) != []

    entries = {e["id"]: e for e in mem.appeals.as_log_entries()}
    assert set(entries) == {CARD_A, CARD_B}
    assert len(entries[CARD_A]["agent_questions"]) == 1
    assert len(entries[CARD_B]["agent_questions"]) == 2
    assert entries[CARD_A]["verification"]["status"] == "confirmed"
    assert entries[CARD_B]["verification"]["status"] == "corrected"
    assert entries[CARD_B]["verification"]["operator_codes"] == [CODE]
    assert entries[CARD_B]["verification"]["comment"] == "исправлено"
    assert entries[CARD_B]["timestamp"] == "2026-04-23T09:00:00"
    assert mem.appeals._cards[CARD_B]["ip_address"] == "10.0.0.7"
    assert mem.appeals._cards[CARD_B]["elapsed_seconds"] == 3.2
    assert mem.appeals._cards[CARD_A]["ip_address"] is None
    assert mem.appeals.llm_log_count() == 1

    assert mem.historical.count() == 1
    assert mem.historical.all()[0]["registration_date"] == "2025-01-10"

    done, running = mem.jobs.get(JOB_DONE), mem.jobs.get(JOB_RUN)
    assert done.status == "done" and done.result == {"ok": True} and done.document_id == 42
    assert done.job_type == "classify" and done.payload == {"queue": "classify"}
    assert running.status == "error"
    assert running.error == "Сервис переведён на новую очередь, отправьте обращение заново"
    assert mem.jobs.stats()["queued"] == 0

    v0, v1 = mem.models.get(0), mem.models.get(1)
    assert v0.is_active and v0.adapter == {"kind": "v3_blend", "alpha": 0.5}
    assert v0.base_model_name == "intfloat/multilingual-e5-base"
    assert not v1.is_active
    assert (v1.recall5_before, v1.recall5_after) == (0.61, 0.66)
    assert v1.model_path == str(FIX / "models" / "e5-finetuned-v1")
    assert mem.models.active().version == 0


def test_legacy_card_fields_are_null(mem):
    run(mem)
    r = mem.appeals._cards[CARD_A]["result"]
    assert r.vid_obrascheniya is None and r.is_ustnoe is None
    assert r.needs_verification is None and r.summary is None
    assert r.overall_confidence == 0.9


def test_migration_is_idempotent(mem):
    first = run(mem)
    before = snapshot(mem)
    second = run(mem)
    assert first == second
    assert snapshot(mem) == before


@pytest.mark.db
def test_migration_is_idempotent_pg(pg_storage):
    first = run(pg_storage)
    assert all(a == b for a, b in first.values()), first
    before = snapshot(pg_storage)
    second = run(pg_storage)
    assert first == second
    assert snapshot(pg_storage) == before
    assert pg_storage.models.get(0).adapter == {"kind": "v3_blend", "alpha": 0.5}


def test_mismatched_metadata_aborts(mem, tmp_path):
    shutil.copytree(FIX / "vector_db", tmp_path / "vdb")
    meta = tmp_path / "vdb" / "metadata.json"
    meta.write_text(
        meta.read_text(encoding="utf-8").replace("0001.0001.0001.0001", "0009.0009.0009.0009"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as ei:
        mig.migrate(mem, FIX, tmp_path / "vdb", FIX / "models", "m")
    assert "0009.0009.0009.0009" in str(ei.value)
    assert mem.classifier.count_entries() == 0  # ничего не записано


def test_skip_cases_are_reconciled(mem, tmp_path):
    """Пустой log_id пропускается; дубль истории не раздувает счёт."""
    data = tmp_path / "data"
    shutil.copytree(FIX, data, ignore=shutil.ignore_patterns("make_*"))
    hist = data / "historical_verified.jsonl"
    hist.write_text(hist.read_text(encoding="utf-8") * 2, encoding="utf-8")
    rec = mig.migrate(mem, data, data / "vector_db", data / "models", "m")
    assert all(a == b for a, b in rec.values()), rec
    assert mem.historical.count() == 1
    assert rec["ip_records"] == (1, 1)


def test_cli_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(mig, "_get_storage", lambda: MemoryStorage())
    code = mig.main([
        "--data-dir", str(FIX), "--vector-db-dir", str(FIX / "vector_db"),
        "--models-dir", str(FIX / "models"),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "OK" in out and "MISMATCH" not in out

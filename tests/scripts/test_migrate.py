"""Миграция файлового хранилища в слой Storage (синтетические фикстуры).

Переносится только то, без чего сервис не работает: классификатор, векторы и
реестр версии 0, плюс исторические обращения. Фикстуры нарочно содержат и
прошлые обращения, журнал запросов, задачи, аннотации и дообученную модель —
тесты проверяют, что миграция их не трогает.
"""

import importlib.util
import shutil
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


def run(storage, data=FIX, vdb=None):
    return mig.migrate(storage, data, vdb or FIX / "vector_db", "intfloat/multilingual-e5-base")


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
    assert set(rec) == {"classifier_entries", "vectors_base", "model_registry", "historical"}

    assert mem.classifier.count_entries() == 3
    root = [e for e in mem.classifier.load_entries() if e.level == 1][0]
    assert root.parent_code is None

    vecs = mem.classifier.vectors_for_version(0)
    assert len(vecs) == 3 and all(v[1] == "base" for v in vecs)

    v0 = mem.models.get(0)
    assert v0.is_active and v0.adapter == {"kind": "v3_blend", "alpha": 0.5}
    assert v0.base_model_name == "intfloat/multilingual-e5-base"
    assert mem.models.active().version == 0

    assert mem.historical.count() == 1
    assert mem.historical.all()[0]["registration_date"] == "2025-01-10"


def test_history_and_extras_are_not_migrated(mem):
    """Прошлые обращения, журнал, задачи, аннотации и дообученные версии не переносятся."""
    for name in ("appeals_log.jsonl", "request_log.jsonl", "classifier_annotations.json",
                 "jobs", "models"):
        assert (FIX / name).exists(), f"фикстура {name} нужна, чтобы проверка что-то значила"
    run(mem)
    assert mem.appeals.as_log_entries() == []
    assert mem.appeals.llm_log_count() == 0
    assert mem.jobs.list(limit=100) == []
    assert mem.classifier.annotations_for(CODE) == []
    assert mem.classifier.pending_annotations(0) == []
    assert [m.version for m in mem.models.list()] == [0]


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
    assert pg_storage.appeals.as_log_entries() == []


def test_mismatched_metadata_aborts(mem, tmp_path):
    shutil.copytree(FIX / "vector_db", tmp_path / "vdb")
    meta = tmp_path / "vdb" / "metadata.json"
    meta.write_text(
        meta.read_text(encoding="utf-8").replace("0001.0001.0001.0001", "0009.0009.0009.0009"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as ei:
        run(mem, vdb=tmp_path / "vdb")
    assert "0009.0009.0009.0009" in str(ei.value)
    assert mem.classifier.count_entries() == 0  # ничего не записано


def test_duplicate_history_is_reconciled(mem, tmp_path):
    """Дубль исторических записей не раздувает счёт."""
    data = tmp_path / "data"
    shutil.copytree(FIX, data, ignore=shutil.ignore_patterns("make_*"))
    hist = data / "historical_verified.jsonl"
    hist.write_text(hist.read_text(encoding="utf-8") * 2, encoding="utf-8")
    rec = run(mem, data=data, vdb=data / "vector_db")
    assert all(a == b for a, b in rec.values()), rec
    assert mem.historical.count() == 1


def test_missing_history_file_is_fine(mem, tmp_path):
    """Нового стенда без historical_verified.jsonl: переносится только необходимое."""
    data = tmp_path / "data"
    shutil.copytree(FIX, data)
    (data / "historical_verified.jsonl").unlink()
    rec = run(mem, data=data, vdb=data / "vector_db")
    assert all(a == b for a, b in rec.values()), rec
    assert rec["historical"] == (0, 0)
    assert mem.classifier.count_entries() == 3


def test_cli_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(mig, "_get_storage", lambda: MemoryStorage())
    code = mig.main(["--data-dir", str(FIX), "--vector-db-dir", str(FIX / "vector_db")])
    out = capsys.readouterr().out
    assert code == 0
    assert "OK" in out and "MISMATCH" not in out


def _copy_data(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIX, data)
    return data


def _cli(data):
    return ["--data-dir", str(data), "--vector-db-dir", str(data / "vector_db")]


# --- Отметка о выполненном переносе (schema_meta.files_migrated) ---------------------

def test_successful_run_writes_marker_and_rerun_is_noop(monkeypatch, tmp_path, capsys):
    data = _copy_data(tmp_path)
    mem = MemoryStorage()
    monkeypatch.setattr(mig, "_get_storage", lambda: mem)
    assert mig.main(_cli(data)) == 0
    marker = mem.meta.get("files_migrated")
    assert marker

    # живая работа после переноса: новое обращение
    mem.appeals.create("живое обращение", ip_address="10.9.9.9")
    before = snapshot(mem)
    called = []
    monkeypatch.setattr(mig, "migrate", lambda *a, **k: called.append(a))
    capsys.readouterr()

    assert mig.main(_cli(data)) == 0
    out = capsys.readouterr().out
    assert called == []
    assert f"Данные из файлов уже перенесены {marker}" in out
    assert "--force" in out
    assert snapshot(mem) == before
    assert mem.meta.get("files_migrated") == marker


def test_force_reimports_despite_marker(monkeypatch, tmp_path):
    data = _copy_data(tmp_path)
    mem = MemoryStorage()
    monkeypatch.setattr(mig, "_get_storage", lambda: mem)
    assert mig.main(_cli(data)) == 0
    real = mig.migrate
    called = []

    def spy(*a, **k):
        called.append(1)
        return real(*a, **k)

    monkeypatch.setattr(mig, "migrate", spy)
    assert mig.main(_cli(data) + ["--force"]) == 0
    assert called == [1]


def test_mismatch_does_not_write_marker(monkeypatch, tmp_path):
    data = _copy_data(tmp_path)
    mem = MemoryStorage()
    monkeypatch.setattr(mig, "_get_storage", lambda: mem)
    rec = mig.Reconciliation()
    rec["historical"] = (2, 1)
    monkeypatch.setattr(mig, "migrate", lambda *a, **k: rec)
    assert mig.main(_cli(data)) == 1
    assert mem.meta.get("files_migrated") is None


@pytest.mark.db
def test_marker_roundtrip_pg(pg_storage):
    assert pg_storage.meta.get("files_migrated") is None
    pg_storage.meta.set("files_migrated", "2026-11-11T10:00:00+00:00")
    pg_storage.meta.set("files_migrated", "2026-11-11T11:00:00+00:00")
    assert pg_storage.meta.get("files_migrated") == "2026-11-11T11:00:00+00:00"


def test_entries_keep_flat_order(mem, tmp_path):
    """Порядок classifier_flat.json сохраняется."""
    import json

    data = tmp_path / "data"
    shutil.copytree(FIX, data, ignore=shutil.ignore_patterns("make_*"))
    flat_path = data / "classifier_flat.json"
    flat = json.loads(flat_path.read_text(encoding="utf-8"))
    flat = [flat[2], flat[0], flat[1]]  # не по коду
    flat_path.write_text(json.dumps(flat, ensure_ascii=False), encoding="utf-8")

    run(mem, data=data)

    assert [e.code for e in mem.classifier.load_entries()] == [e["code"] for e in flat]


def _vdb_copy(tmp_path):
    import json

    import numpy as np

    vdb = tmp_path / "vdb"
    shutil.copytree(FIX / "vector_db", vdb)
    meta = json.loads((vdb / "metadata.json").read_text(encoding="utf-8"))
    emb = np.load(vdb / "embeddings.npy")
    return vdb, meta, emb


def _vdb_write(vdb, meta, emb):
    import json

    import numpy as np

    (vdb / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    np.save(vdb / "embeddings.npy", emb)


def test_wrong_embedding_dim_aborts_before_writes(mem, tmp_path):
    vdb, meta, emb = _vdb_copy(tmp_path)
    _vdb_write(vdb, meta, emb[:, :384])
    with pytest.raises(SystemExit) as ei:
        run(mem, vdb=vdb)
    assert "768" in str(ei.value) and "384" in str(ei.value)
    assert mem.classifier.count_entries() == 0


def test_row_count_mismatch_aborts_with_clear_error(mem, tmp_path):
    vdb, meta, emb = _vdb_copy(tmp_path)
    _vdb_write(vdb, meta, emb[:2])
    with pytest.raises(SystemExit) as ei:
        run(mem, vdb=vdb)
    assert "2" in str(ei.value) and "3" in str(ei.value)
    assert mem.classifier.count_entries() == 0


def test_duplicate_metadata_codes_are_reported(mem, tmp_path, capsys):
    import numpy as np

    vdb, meta, emb = _vdb_copy(tmp_path)
    meta = meta + [dict(meta[2], search_text="passage: дубль")] * 2
    emb = np.vstack([emb, emb[2:3], emb[2:3]])
    _vdb_write(vdb, meta, emb)
    rec = run(mem, vdb=vdb)
    out = capsys.readouterr().out
    assert rec.warnings == 1
    assert "дублир" in out and "2" in out
    assert rec["vectors_base"] == (5, 5)


def test_vector_row_i_matches_metadata_row_i(mem):
    import json

    import numpy as np

    run(mem)
    meta = json.loads((FIX / "vector_db" / "metadata.json").read_text(encoding="utf-8"))
    emb = np.load(FIX / "vector_db" / "embeddings.npy")
    rows = mem.classifier.vectors_for_version(0)
    assert [(c, t) for c, st, t in rows if st == "base"] == [
        (m["code"], m.get("search_text", "")) for m in meta]
    for i, m in enumerate(meta):
        (code, sim), *_ = mem.classifier.search(emb[i], 0, 1, False)
        assert code == m["code"] and sim == pytest.approx(1.0, abs=1e-5)

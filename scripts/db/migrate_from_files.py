"""Разовый импорт файлового хранилища в слой Storage (PostgreSQL / memory).

Переносится только то, без чего сервис не работает, и исторические обращения:
  - classifier_entries — классификатор (data/classifier_flat.json);
  - classifier_vectors и ml_model_registry — векторы и запись активной версии 0
    (VECTOR_DB_DIR, по умолчанию data/vector_db_adapted_v3);
  - historical_appeals — исторические размеченные обращения
    (data/historical_verified.jsonl, если файл есть).
Прошлые обращения, журнал запросов, задачи старой очереди, аннотации операторов и
дообученные версии модели НЕ переносятся. Все таблицы при этом создаются как
обычно (init_db.py); остальные заполняются в работе.

Идемпотентен: повторный запуск ничего не дублирует. Исходные файлы не меняются.
Запуск из корня проекта:
    python scripts/db/migrate_from_files.py [--data-dir data] [--vector-db-dir ...]
Код возврата 1, если сверка по какому-либо источнику не сошлась.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np  # noqa: E402

from storage.models import ClassifierEntry, ModelVersion, Storage  # noqa: E402

ROOT_PARENT = "0000.0000.0000.0000"
HIST_SOURCE = "historical_verified.jsonl"
VECTOR_DIM = 768  # размерность classifier_vectors.embedding (schema.sql)
MIGRATED_KEY = "files_migrated"  # schema_meta: отметка полного успешного переноса


class Reconciliation(dict):
    """{источник: (в файлах, в БД)} + пометки пропущенных источников и счётчик предупреждений."""

    def __init__(self) -> None:
        super().__init__()
        self.notes: dict[str, str] = {}
        self.warnings = 0


def _say(msg: str) -> None:
    print(msg, flush=True)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    """Строки JSONL; битые строки пропускаются с предупреждением."""
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            _say(f"  предупреждение: {path.name}:{n} - битая строка пропущена")
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# --- 1-2. Классификатор, реестр версии 0, векторы -------------------------------------------

def _load_entries(data_dir: Path) -> list[ClassifierEntry]:
    flat = _read_json(data_dir / "classifier_flat.json")
    return [
        ClassifierEntry(
            code=e["code"], name=e["name"], level=int(e["level"]),
            parent_code=None if e.get("parent_code") in (None, "", ROOT_PARENT)
            else e["parent_code"],
            full_path=e.get("full_path", ""), children_count=int(e.get("children_count", 0)),
            sort_order=i,
        )
        for i, e in enumerate(flat)
    ]


def _load_vector_db(vector_db_dir: Path, entry_codes: set[str]):
    """Проверки до любых записей: размерность, число строк, состав кодов.

    Строка i embeddings.npy соответствует записи i metadata.json (порядок сохраняется).
    """
    meta = _read_json(vector_db_dir / "metadata.json")
    emb = np.load(vector_db_dir / "embeddings.npy")
    if emb.ndim != 2 or emb.shape[1] != VECTOR_DIM:
        raise SystemExit(
            f"embeddings.npy: размерность {tuple(emb.shape)}, ожидается (N, {VECTOR_DIM}) - "
            f"векторы не от multilingual-e5-base?"
        )
    if emb.shape[0] != len(meta):
        raise SystemExit(
            f"embeddings.npy ({emb.shape[0]} строк) не соответствует metadata.json "
            f"({len(meta)} записей): строка i векторов должна соответствовать записи i"
        )
    meta_codes = {m["code"] for m in meta}
    if meta_codes != entry_codes:
        only_meta = sorted(meta_codes - entry_codes)
        only_flat = sorted(entry_codes - meta_codes)
        raise SystemExit(
            "Коды metadata.json и classifier_flat.json не совпадают. "
            f"Только в metadata: {only_meta}; только в classifier_flat: {only_flat}"
        )
    return meta, emb


def _migrate_classifier(storage, entries, meta, emb, vector_db_dir, embedding_model, rec):
    storage.classifier.upsert_entries(entries)
    rec["classifier_entries"] = (len(entries), storage.classifier.count_entries())

    adapter_path = vector_db_dir / "adapter_v3.json"
    adapter = _read_json(adapter_path) if adapter_path.exists() else None
    if storage.models.get(0) is None:
        storage.models.register(ModelVersion(
            version=0, base_model_name=embedding_model, model_path=embedding_model,
            is_active=True, adapter=adapter,
        ))
        _say("  модель v0 зарегистрирована (активна)")
    rec["model_registry"] = (1, 1 if storage.models.get(0) is not None else 0)

    def base_count() -> int:
        return sum(1 for v in storage.classifier.vectors_for_version(0) if v[1] == "base")

    if base_count() == 0:
        storage.classifier.add_vectors(
            0, [(m["code"], "base", m.get("search_text", ""), emb[i]) for i, m in enumerate(meta)]
        )
        _say(f"  векторы v0: загружено {len(meta)}")
    else:
        _say("  векторы v0 уже есть - пропуск")
    rec["vectors_base"] = (len(meta), base_count())


# --- 3. Исторические ---------------------------------------------------------------------

def _migrate_historical(storage, data_dir: Path, rec):
    records = _read_jsonl(data_dir / HIST_SOURCE)
    unique = {(r.get("appeal_text", ""), r.get("assigned_code", ""), r.get("date") or "")
              for r in records}
    storage.historical.add(records, HIST_SOURCE)
    have = sum(1 for r in storage.historical.all() if r.get("source_file") == HIST_SOURCE)
    rec["historical"] = (len(unique), have)


def migrate(storage: Storage, data_dir: Path, vector_db_dir: Path,
            embedding_model: str) -> dict[str, tuple[int, int]]:
    """Импортирует необходимые для работы данные и исторические обращения.

    Возвращает {источник: (в файлах, в БД)}.
    """
    data_dir, vector_db_dir = Path(data_dir), Path(vector_db_dir)
    rec = Reconciliation()

    entries = _load_entries(data_dir)
    entry_codes = {e.code for e in entries}
    meta, emb = _load_vector_db(vector_db_dir, entry_codes)  # до любых записей
    dups = len(meta) - len({m["code"] for m in meta})
    if dups:
        _say(f"  предупреждение: metadata.json - {dups} строк дублируют уже встреченные коды "
             f"(импортируются как есть, поиск берёт максимум по коду)")
        rec.warnings += 1

    _say("Классификатор и векторы...")
    _migrate_classifier(storage, entries, meta, emb, vector_db_dir, embedding_model, rec)
    _say("Исторические обращения...")
    _migrate_historical(storage, data_dir, rec)
    return rec


def _get_storage() -> Storage:
    from storage import get_storage

    return get_storage()


def main(argv: Optional[list[str]] = None) -> int:
    from core import config

    p = argparse.ArgumentParser(description="Импорт файлового хранилища в БД")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--vector-db-dir", default=config.VECTOR_DB_DIR)
    p.add_argument("--force", action="store_true",
                   help="повторить импорт, даже если перенос уже выполнен")
    args = p.parse_args(argv)

    storage = _get_storage()
    done_at = storage.meta.get(MIGRATED_KEY)
    if done_at and not args.force:
        # Повторный импорт после живой работы мог бы вернуть то, что уже удалено.
        _say(f"Данные из файлов уже перенесены {done_at}; повторный импорт: --force")
        return 0

    rec = migrate(storage, Path(args.data_dir), Path(args.vector_db_dir), config.EMBEDDING_MODEL)
    _say("\nСверка (источник: в файлах / в БД):")
    bad = 0
    for name, (files, db_n) in rec.items():
        ok = files == db_n
        bad += not ok
        note = rec.notes.get(name)
        _say(f"  {name:<20} {files:>8} {db_n:>8}  {note or ('OK' if ok else 'MISMATCH')}"
             + (" OK" if note else ""))
    _say(f"  {'warnings':<20} {rec.warnings:>8}")
    _say("Готово." if not bad else f"Расхождений: {bad}")
    if bad:
        return 1
    storage.meta.set(MIGRATED_KEY, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Явно, не дожидаясь atexit: потоки пула psycopg не держат выход процесса.
        from storage import db
        db.close_pool()

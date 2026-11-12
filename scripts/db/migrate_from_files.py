"""Разовый импорт файлового хранилища в слой Storage (PostgreSQL / memory).

Идемпотентен: повторный запуск ничего не дублирует. Исходные файлы не меняются.
Запуск из корня проекта:
    python scripts/db/migrate_from_files.py [--data-dir data] [--vector-db-dir ...] [--models-dir models]
Код возврата 1, если сверка по какому-либо источнику не сошлась.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np  # noqa: E402

from storage import appeal_common as ac  # noqa: E402
from storage.models import (  # noqa: E402
    AppealQuestionRow, AppealResult, ClassifierEntry, Job, ModelVersion, Storage,
)

ROOT_PARENT = "0000.0000.0000.0000"
RESTART_ERROR = "Сервис переведён на новую очередь, отправьте обращение заново"
HIST_SOURCE = "historical_verified.jsonl"
VECTOR_DIM = 768  # размерность classifier_vectors.embedding (schema.sql)
MIGRATED_KEY = "files_migrated"  # schema_meta: отметка полного успешного переноса
_MODEL_DIR_RE = re.compile(r"^e5-finetuned-v(\d+)$")


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


def _legacy_uuid(legacy_id: str) -> str:
    """UUID карточки: валидный UUID как есть, иначе детерминированный uuid5."""
    norm = ac.norm_uuid(legacy_id)
    if norm is not None:
        return norm
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "legacy-appeal:" + str(legacy_id)))


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _epoch(value) -> Optional[datetime]:
    return None if value is None else datetime.fromtimestamp(float(value), tz=timezone.utc)


# --- 1-2. Классификатор, реестр моделей, векторы -------------------------------------------

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


def _migrate_models(storage, models_dir: Path, rec):
    found = []
    if models_dir.is_dir():
        for d in sorted(models_dir.iterdir()):
            m = _MODEL_DIR_RE.match(d.name)
            if m and (d / "training_report.json").exists():
                found.append((int(m.group(1)), d))
    for version, d in found:
        if storage.models.get(version) is not None:
            continue
        rep = _read_json(d / "training_report.json")
        storage.models.register(ModelVersion(
            version=version,
            base_model_name=rep.get("base_model", ""),
            model_path=str(d),
            is_active=False,
            recall5_before=rep.get("recall5_before"),
            recall5_after=rep.get("recall5_after"),
            training_samples_count=rep.get("train_size"),
            trained_at=ac.aware(_parse_dt(rep.get("timestamp"))),
            report=rep,
        ))
        _say(f"  модель v{version} зарегистрирована (неактивна)")
    versions = {0} | {v for v, _ in found}
    present = {mv.version for mv in storage.models.list()}
    rec["model_registry"] = (len(versions), len(versions & present))


# --- 3. Аннотации -------------------------------------------------------------------------

def _migrate_annotations(storage, data_dir: Path, entry_codes: set[str], rec):
    path = data_dir / "classifier_annotations.json"
    raw = _read_json(path) if path.exists() else {}
    rows: list[tuple[str, str]] = []
    for code, fields in raw.items():
        if code.startswith("_") or not isinstance(fields, dict):
            continue
        if code not in entry_codes:
            _say(f"  предупреждение: аннотация для неизвестного кода {code} пропущена")
            continue
        for field, value in fields.items():
            if field.startswith("_") or value is None or not str(value).strip():
                continue
            text = f"{field}: {value}"
            rows.append((code, text))
    existing = {c: set(t) for c, t in storage.classifier.annotations_by_code().items()}
    added = 0
    for code, text in rows:
        if text in existing.get(code, ()):
            continue
        storage.classifier.add_annotation(code, text, 0)
        existing.setdefault(code, set()).add(text)
        added += 1
    final = storage.classifier.annotations_by_code()
    present = sum(1 for code, text in rows if text in final.get(code, ()))
    _say(f"  аннотации: добавлено {added} из {len(rows)}")
    rec["annotations"] = (len(rows), present)


# --- 4-5. Обращения и request_log ---------------------------------------------------------

def _legacy_result(card: dict) -> AppealResult:
    questions = [
        AppealQuestionRow(
            ordinal=i, question_text=q.get("question_text", ""),
            selected_code=q.get("selected_code", ""),
            confidence=q.get("confidence") or 0.0,
            alternative_codes=list(q.get("alternative_codes") or []),
        )
        for i, q in enumerate(card.get("agent_questions") or [])
    ]
    return AppealResult(
        summary=card.get("summary"), applicant_fio=card.get("fio"),
        applicant_email=card.get("email"), vid_obrascheniya=card.get("vid"),
        tip_obrascheniya=card.get("tip"), is_ustnoe=card.get("is_ustnoe"),
        overall_confidence=card.get("overall_confidence") or 0.0,
        needs_verification=card.get("needs_verification"),
        full_fallback_used=card.get("full_fallback_used"),
        candidates=list(card.get("candidates") or []),
        llm_provider=card.get("llm_provider"), llm_model=card.get("llm_model"),
        elapsed_seconds=None, questions=questions,
    )


def _migrate_appeals(storage, data_dir: Path, rec) -> dict[str, dict]:
    cards: dict[str, dict] = {}
    for c in _read_jsonl(data_dir / "appeals_log.jsonl"):
        if c.get("id") and c.get("appeal_text") is not None:
            cards.setdefault(_legacy_uuid(c["id"]), c)
    for aid, c in cards.items():
        v = c.get("verification") or {}
        status = v.get("status") or "pending"  # null / "" / нет поля -> pending
        if status not in ac.STATUSES:
            _say(f"  предупреждение: карточка {aid}: неизвестный статус {status!r} -> pending")
            rec.warnings += 1
            status = "pending"
        # статус нормализован один раз: импорт и сверка читают одно и то же значение
        c["verification"] = {**v, "status": status}

    created = 0
    for aid, c in cards.items():
        if storage.appeals.get_text(aid) is not None:
            continue  # карточка уже есть - не трогаем вопросы и верификацию
        v = c.get("verification") or {}
        status = v.get("status") or "pending"
        with storage.transaction() as tx:  # карточка целиком или никак
            storage.appeals.create(
                c["appeal_text"], appeal_id=aid, created_at=_parse_dt(c.get("timestamp")),
                conn=tx,
            )
            storage.appeals.save_result(aid, _legacy_result(c), conn=tx)
            if status != "pending":
                storage.appeals.set_verification(
                    aid, status, v.get("operator_codes"), v.get("comment"),
                    verified_at=_parse_dt(v.get("verified_at")), conn=tx,
                )
        created += 1
    _say(f"  обращения: создано {created}, уже было {len(cards) - created}")

    in_db = {e["id"]: e for e in storage.appeals.as_log_entries()}
    present = [aid for aid in cards if aid in in_db]
    rec["appeals"] = (len(cards), len(present))
    rec["questions"] = (
        sum(len(c.get("agent_questions") or []) for c in cards.values()),
        sum(len(in_db[a]["agent_questions"]) for a in present),
    )
    # файл не pending => в БД тот же статус; файл pending => в БД любой (оператор мог
    # проверить карточку уже после переноса)
    ok = sum(
        1 for a in present
        if cards[a]["verification"]["status"] == "pending"
        or in_db[a]["verification"]["status"] == cards[a]["verification"]["status"]
    )
    rec["verifications"] = (len(cards), ok)
    return cards


def _migrate_request_log(storage, data_dir: Path, cards: dict, rec):
    records = _read_jsonl(data_dir / "request_log.jsonl")
    ip_records = [r for r in records if "ip" in r and "event" not in r]
    events = [r for r in records if "event" in r]

    matched: dict[str, dict] = {}  # id карточки -> последняя IP-запись
    unmatched = 0
    for r in ip_records:
        log_id = r.get("log_id") or ""
        aid = _legacy_uuid(log_id) if log_id else None
        if aid is None or aid not in cards or ac.norm_ip(r.get("ip")) is None:
            unmatched += 1
            continue
        matched[aid] = r
    for aid, r in matched.items():
        # only_missing: при повторе (--force) живые ip/elapsed карточки не перетираются
        storage.appeals.set_request_meta(aid, r.get("ip"), r.get("elapsed_seconds"),
                                         only_missing=True)
    have_ip = storage.appeals.ip_addresses(list(matched))
    rec["ip_records"] = (len(matched), sum(1 for a in matched if have_ip.get(a)))
    _say(f"  IP-записи: применено {len(matched)}, без пары {unmatched}")

    if storage.appeals.llm_log_count() == 0:
        for e in events:
            storage.appeals.log_llm_request(
                None, bool(e.get("success")), e.get("attempt") or 1,
                e.get("elapsed_seconds"), e.get("confidence"), e.get("error"),
                e.get("candidates_count") or 0, created_at=_parse_dt(e.get("timestamp")),
            )
    else:
        _say("  llm_request_log уже заполнен - пропуск")
        rec.notes["llm_events"] = "skipped (table not empty)"
        rec["llm_events"] = (len(events), len(events))
        return
    rec["llm_events"] = (len(events), storage.appeals.llm_log_count())


# --- 6. Исторические ---------------------------------------------------------------------

def _migrate_historical(storage, data_dir: Path, rec):
    records = _read_jsonl(data_dir / HIST_SOURCE)
    unique = {(r.get("appeal_text", ""), r.get("assigned_code", ""), r.get("date") or "")
              for r in records}
    storage.historical.add(records, HIST_SOURCE)
    have = sum(1 for r in storage.historical.all() if r.get("source_file") == HIST_SOURCE)
    rec["historical"] = (len(unique), have)


# --- 7. Задачи ---------------------------------------------------------------------------

def _migrate_jobs(storage, data_dir: Path, rec):
    files = sorted((data_dir / "jobs").glob("*.json")) if (data_dir / "jobs").is_dir() else []
    ids = []
    for f in files:
        try:
            d = _read_json(f)
        except ValueError:
            _say(f"  предупреждение: {f.name} - битый JSON пропущен")
            continue
        jid = d.get("id")
        if not isinstance(jid, str) or not re.fullmatch(r"[0-9a-f]{32}", jid):
            _say(f"  предупреждение: {f.name} - некорректный id пропущен")
            continue
        ids.append(jid)
        status, error, finished = d.get("status"), d.get("error"), _epoch(d.get("finished_at"))
        if status in ("queued", "running"):
            status, error = "error", RESTART_ERROR
            finished = datetime.now(timezone.utc)
        elif status not in ("done", "error"):
            status = "error"
        created = _epoch(d.get("created_at")) or datetime.now(timezone.utc)
        started = _epoch(d.get("started_at"))
        storage.jobs.import_job(Job(
            id=jid, job_type="classify", status=status, appeal_id=None,
            document_id=(d.get("meta") or {}).get("document_id"), webhook_url=None,
            payload={"queue": "classify"}, result=d.get("result"), error=error,
            attempts=1 if started else 0, created_at=created, started_at=started,
            finished_at=finished,
        ))
    rec["jobs"] = (len(ids), sum(1 for j in ids if storage.jobs.get(j) is not None))


# --- Точка входа -------------------------------------------------------------------------

def migrate(storage: Storage, data_dir: Path, vector_db_dir: Path, models_dir: Path,
            embedding_model: str) -> dict[str, tuple[int, int]]:
    """Импортирует все файловые источники. Возвращает {источник: (в файлах, в БД)}."""
    data_dir, vector_db_dir, models_dir = Path(data_dir), Path(vector_db_dir), Path(models_dir)
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
    _say("Версии моделей...")
    _migrate_models(storage, models_dir, rec)
    _say("Аннотации...")
    _migrate_annotations(storage, data_dir, entry_codes, rec)
    _say("Обращения...")
    cards = _migrate_appeals(storage, data_dir, rec)
    _say("Журнал запросов...")
    _migrate_request_log(storage, data_dir, cards, rec)
    _say("Исторические обращения...")
    _migrate_historical(storage, data_dir, rec)
    _say("Задачи...")
    _migrate_jobs(storage, data_dir, rec)
    return rec


def _get_storage() -> Storage:
    from storage import get_storage

    return get_storage()


def main(argv: Optional[list[str]] = None) -> int:
    import config

    p = argparse.ArgumentParser(description="Импорт файлового хранилища в БД")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--vector-db-dir", default=config.VECTOR_DB_DIR)
    p.add_argument("--models-dir", default=config.MODELS_DIR)
    p.add_argument("--force", action="store_true",
                   help="повторить импорт, даже если перенос уже выполнен")
    args = p.parse_args(argv)

    storage = _get_storage()
    done_at = storage.meta.get(MIGRATED_KEY)
    if done_at and not args.force:
        # Повторный импорт после живой работы (перепроверка карточки, удалённая
        # аннотация) дал бы расхождение сверки или вернул бы удалённое.
        _say(f"Данные из файлов уже перенесены {done_at}; повторный импорт: --force")
        return 0

    rec = migrate(
        storage, Path(args.data_dir), Path(args.vector_db_dir), Path(args.models_dir),
        config.EMBEDDING_MODEL,
    )
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

"""In-memory реализация хранилищ (тесты)."""

from __future__ import annotations

import copy
import dataclasses
import logging
import threading
import re
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from . import appeal_common as ac
from .models import (STATUS_DONE, STATUS_ERROR, STATUS_QUEUED, STATUS_RUNNING,
                     AppealResult, ClassifierEntry, Job, ModelVersion, QueueFull, Storage)

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class MemoryModelStore:
    def __init__(self) -> None:
        self._items: dict[int, ModelVersion] = {}
        self._lock = threading.RLock()

    def register(self, mv):
        with self._lock:
            if mv.is_active:
                for other in self._items.values():
                    other.is_active = False
            self._items[mv.version] = dataclasses.replace(mv)

    def active(self):
        with self._lock:
            for mv in self._items.values():
                if mv.is_active:
                    return dataclasses.replace(mv)
            return None

    def get(self, version):
        with self._lock:
            mv = self._items.get(version)
            return dataclasses.replace(mv) if mv else None

    def list(self):
        with self._lock:
            return [dataclasses.replace(self._items[v]) for v in sorted(self._items)]

    def activate(self, version):
        with self._lock:
            if version not in self._items:
                raise KeyError(version)
            for mv in self._items.values():
                mv.is_active = mv.version == version

    def next_version(self):
        with self._lock:
            return max(self._items, default=-1) + 1


class MemoryClassifierStore:
    def __init__(self, models: MemoryModelStore) -> None:
        self._models = models
        self._entries: dict[str, ClassifierEntry] = {}
        # id -> {code, source_type, search_text, embedding, version}
        self._vectors: dict[int, dict] = {}
        self._next_id = 1
        self._lock = threading.RLock()

    def upsert_entries(self, entries):
        with self._lock:
            for e in entries:
                self._entries[e.code] = dataclasses.replace(e, search_text="")
            return len(entries)

    def load_entries(self):
        with self._lock:
            act = self._models.active()
            base: dict[str, str] = {}
            if act is not None:
                for _, v in sorted(self._vectors.items()):
                    if v["version"] == act.version and v["source_type"] == "base":
                        base.setdefault(v["code"], v["search_text"])
            return [
                dataclasses.replace(self._entries[c], search_text=base.get(c, ""))
                for c in sorted(self._entries)
            ]

    def count_entries(self):
        with self._lock:
            return len(self._entries)

    def _insert(self, code, source_type, text, emb, version):
        vid = self._next_id
        self._next_id += 1
        self._vectors[vid] = {
            "code": code,
            "source_type": source_type,
            "search_text": text,
            "embedding": None if emb is None else np.asarray(emb, dtype=np.float32),
            "version": version,
        }
        return vid

    def add_vectors(self, version, rows):
        with self._lock:
            for code, st, text, emb in rows:
                self._insert(code, st, text, emb, version)
            return len(rows)

    def search(self, query, version, top_k, include_annotations):
        q = np.asarray(query, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        best: dict[str, float] = {}
        with self._lock:
            for v in self._vectors.values():
                if v["version"] != version or v["embedding"] is None:
                    continue
                if v["source_type"] != "base" and not include_annotations:
                    continue
                e = v["embedding"]
                den = float(np.linalg.norm(e)) * qn
                sim = float(e @ q) / den if den else 0.0
                if v["code"] not in best or sim > best[v["code"]]:
                    best[v["code"]] = sim
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]

    def pending_annotations(self, version):
        with self._lock:
            return [
                (i, v["search_text"])
                for i, v in sorted(self._vectors.items())
                if v["version"] == version
                and v["source_type"] == "annotation"
                and v["embedding"] is None
            ]

    def set_embedding(self, vector_id, embedding):
        with self._lock:
            self._vectors[vector_id]["embedding"] = np.asarray(embedding, dtype=np.float32)

    def add_annotation(self, code, text, version):
        with self._lock:
            return self._insert(code, "annotation", text, None, version)

    def annotations_for(self, code):
        with self._lock:
            return [
                v["search_text"]
                for _, v in sorted(self._vectors.items())
                if v["code"] == code and v["source_type"] == "annotation"
            ]

    def annotated_codes(self):
        with self._lock:
            return sorted(
                {v["code"] for v in self._vectors.values() if v["source_type"] == "annotation"}
            )

    def vectors_for_version(self, version):
        with self._lock:
            return [
                (v["code"], v["source_type"], v["search_text"])
                for _, v in sorted(self._vectors.items())
                if v["version"] == version
            ]


class MemoryAppealStore:
    def __init__(self) -> None:
        self._cards: dict[str, dict] = {}
        self._llm_log: list[dict] = []
        self._lock = threading.RLock()

    def create(self, appeal_text, document_id=None, ip_address=None, appeal_id=None,
               created_at=None):
        aid = ac.norm_uuid(appeal_id) if appeal_id else str(uuid.uuid4())
        if aid is None:
            raise ValueError(f"appeal_id не UUID: {appeal_id!r}")
        with self._lock:
            if aid in self._cards:
                raise ValueError(f"обращение {aid} уже существует")
            self._cards[aid] = {
                "id": aid,
                "document_id": document_id,
                "appeal_text": appeal_text,
                "ip_address": ac.norm_ip(ip_address),
                "created_at": ac.aware(created_at) if created_at else ac.now_aware(),
                "result": None,
                "elapsed_seconds": None,
                "verification": {
                    "status": "pending", "verified_at": None, "operator_codes": None,
                    "comment": None, "verified_by": None,
                },
            }
        return aid

    def save_result(self, appeal_id, result: AppealResult):
        with self._lock:
            card = self._cards.get(ac.norm_uuid(appeal_id))
            if card is None:
                raise KeyError(appeal_id)
            card["result"] = copy.deepcopy(result)
            if result.elapsed_seconds is not None:
                card["elapsed_seconds"] = result.elapsed_seconds

    def get_text(self, appeal_id):
        with self._lock:
            card = self._cards.get(ac.norm_uuid(appeal_id))
            return card["appeal_text"] if card else None

    def set_verification(self, appeal_id, status, operator_codes, comment,
                         verified_by=None, verified_at=None):
        if status not in ac.STATUSES:
            raise ValueError(f"неизвестный статус верификации: {status!r}")
        with self._lock:
            card = self._cards.get(ac.norm_uuid(appeal_id))
            if card is None:
                return False
            if status == "pending":
                verified_at = None
            else:
                verified_at = ac.aware(verified_at) if verified_at else ac.now_aware()
            card["verification"] = {
                "status": status,
                "verified_at": verified_at,
                "operator_codes": None if operator_codes is None else list(operator_codes),
                "comment": comment,
                "verified_by": verified_by,
            }
            return True

    def as_log_entries(self, statuses=None):
        with self._lock:
            cards = sorted(self._cards.values(), key=lambda c: c["created_at"])
            out = []
            for c in cards:
                v = c["verification"]
                if statuses is not None and v["status"] not in statuses:
                    continue
                r = c["result"]
                questions = []
                if r:
                    for q in sorted(r.questions, key=lambda q: q.ordinal):
                        questions.append({
                            "question_text": q.question_text,
                            "selected_code": q.selected_code,
                            "confidence": q.confidence,
                            "alternative_codes": list(q.alternative_codes),
                        })
                out.append({
                    "id": c["id"],
                    "timestamp": ac.local_iso(c["created_at"]),
                    "appeal_text": c["appeal_text"],
                    "candidates": copy.deepcopy(r.candidates) if r else [],
                    "agent_questions": questions,
                    "overall_confidence": (r.overall_confidence if r else None) or 0.0,
                    "verification": {
                        "status": v["status"],
                        "verified_at": ac.local_iso(v["verified_at"]),
                        "operator_codes": None if v["operator_codes"] is None
                        else list(v["operator_codes"]),
                        "comment": v["comment"],
                    },
                })
            return out

    def stats(self):
        with self._lock:
            counts = {s: 0 for s in ac.STATUSES}
            for c in self._cards.values():
                counts[c["verification"]["status"]] += 1
            return {
                "total": len(self._cards),
                "verified": counts["confirmed"] + counts["corrected"],
                "pending": counts["pending"],
                "confirmed": counts["confirmed"],
                "corrected": counts["corrected"],
                "rejected": counts["rejected"],
            }

    def backoffice_stats(self, days=30):
        """Агрегаты для бэк-офиса.

        Карточки с NULL overall_confidence (ещё не классифицированы) входят в
        total_classifications и в статусы, но не в гистограмму и не в avg_confidence.
        top_codes считаются только по confirmed/corrected; ip_stats - только по
        карточкам с известным ip.
        """
        with self._lock:
            cards = [
                (
                    c["created_at"],
                    c["result"].overall_confidence if c["result"] else None,
                    c["verification"]["status"],
                    c["ip_address"],
                    [q.selected_code for q in c["result"].questions] if c["result"] else [],
                )
                for c in self._cards.values()
            ]
        return ac.compute_backoffice_stats(cards, days)

    def llm_log_count(self):
        with self._lock:
            return len(self._llm_log)

    def log_llm_request(self, appeal_id, success, attempt, elapsed, confidence, error,
                        candidates_count, created_at=None):
        with self._lock:
            self._llm_log.append({
                "appeal_id": appeal_id, "success": success, "attempt": attempt,
                "elapsed_seconds": elapsed, "confidence": confidence, "error": error,
                "candidates_count": candidates_count,
                "created_at": ac.aware(created_at) if created_at else ac.now_aware(),
            })

    def set_request_meta(self, appeal_id, ip_address, elapsed_seconds):
        with self._lock:
            card = self._cards.get(ac.norm_uuid(appeal_id))
            if card is None:
                return
            ip = ac.norm_ip(ip_address)
            if ip is not None:
                card["ip_address"] = ip
            if elapsed_seconds is not None:
                card["elapsed_seconds"] = elapsed_seconds


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._seq: dict[str, int] = {}
        self._next_seq = 0
        self._listeners: list = []
        self._lock = threading.RLock()

    # --- эмуляция NOTIFY job_done ---
    def add_listener(self, fn) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    @staticmethod
    def _queue_of(job: Job) -> str:
        return (job.payload or {}).get("queue", "classify")

    def _order(self, job: Job):
        return (job.created_at, self._seq[job.id])

    def _store(self, job: Job) -> None:
        self._jobs[job.id] = job
        self._seq[job.id] = self._next_seq
        self._next_seq += 1

    def create(self, job_type, payload, appeal_id=None, document_id=None, webhook_url=None,
               max_queued=None, queue_class="classify"):
        with self._lock:
            if max_queued is not None:
                queued = sum(
                    1 for j in self._jobs.values()
                    if j.status == STATUS_QUEUED and j.job_type == job_type
                    and self._queue_of(j) == queue_class
                )
                if queued >= max_queued:
                    raise QueueFull(f"в очереди уже {queued} задач, лимит {max_queued}")
            job = Job(
                id=uuid.uuid4().hex, job_type=job_type, status=STATUS_QUEUED,
                appeal_id=ac.norm_uuid(appeal_id) if appeal_id else None,
                document_id=document_id, webhook_url=webhook_url,
                payload={**copy.deepcopy(payload), "queue": queue_class},
                result=None, error=None, attempts=0, created_at=_utcnow(),
            )
            self._store(job)
            return copy.deepcopy(job)

    def get(self, job_id):
        if not _ID_RE.match(job_id or ""):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def claim(self, job_id):
        if not _ID_RE.match(job_id or ""):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in (STATUS_QUEUED, STATUS_RUNNING):
                return None
            job.status = STATUS_RUNNING
            job.attempts += 1
            job.started_at = _utcnow()
            return copy.deepcopy(job)

    def finish(self, job_id, result, error, conn=None):
        if not _ID_RE.match(job_id or ""):
            return False
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in (STATUS_QUEUED, STATUS_RUNNING):
                return False
            job.status = STATUS_ERROR if error is not None else STATUS_DONE
            job.result = copy.deepcopy(result)
            job.error = error
            job.finished_at = _utcnow()
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(job_id)
            except Exception:  # noqa: BLE001 — сбой слушателя не должен ломать finish
                logger.exception("слушатель job_done упал для задачи %s", job_id)
        return True

    def position(self, job_id):
        if not _ID_RE.match(job_id or ""):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != STATUS_QUEUED:
                return None
            waiting = sorted(
                (j for j in self._jobs.values()
                 if j.status == STATUS_QUEUED and j.job_type == job.job_type
                 and self._queue_of(j) == self._queue_of(job)),
                key=self._order,
            )
            return next((i + 1 for i, j in enumerate(waiting) if j.id == job_id), None)

    def list(self, limit=50, job_type=None):
        with self._lock:
            jobs = [j for j in self._jobs.values() if job_type is None or j.job_type == job_type]
            jobs.sort(key=self._order, reverse=True)
            return [copy.deepcopy(j) for j in jobs[:limit]]

    def stats(self):
        with self._lock:
            counts = {STATUS_QUEUED: 0, STATUS_RUNNING: 0, STATUS_DONE: 0, STATUS_ERROR: 0}
            for j in self._jobs.values():
                counts[j.status] += 1
            return counts

    def latest(self, job_type):
        found = self.list(limit=1, job_type=job_type)
        return found[0] if found else None

    def has_active(self, job_type):
        with self._lock:
            return any(
                j.job_type == job_type and j.status in (STATUS_QUEUED, STATUS_RUNNING)
                for j in self._jobs.values()
            )

    def cleanup_expired(self, ttl_hours):
        # Сравнение нестрогое (как в старой очереди): метки времени могут совпасть.
        deadline = _utcnow() - timedelta(hours=ttl_hours)
        with self._lock:
            stale = [
                j.id for j in self._jobs.values()
                if j.status in (STATUS_DONE, STATUS_ERROR)
                and (j.finished_at or j.created_at) <= deadline
            ]
            for jid in stale:
                del self._jobs[jid]
                del self._seq[jid]
            return len(stale)

    def import_job(self, job):
        with self._lock:
            if job.id not in self._jobs:
                self._store(copy.deepcopy(job))


class MemoryHistoricalStore:
    """Исторические размеченные обращения; ключ дедупа как у индекса hist_dedup."""

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._keys: set[tuple] = set()
        self._lock = threading.RLock()

    def add(self, records, source_file):
        inserted = 0
        with self._lock:
            for r in records:
                text = r.get("appeal_text", "")
                code = r.get("assigned_code", "")
                date = r.get("date")
                key = (source_file or "", text, code, date or "")
                if key in self._keys:
                    continue
                self._keys.add(key)
                self._rows.append({
                    "appeal_text": text, "assigned_code": code,
                    "specialist": r.get("specialist"), "registration_date": date,
                    "source_file": source_file,
                })
                inserted += 1
        return inserted

    def count(self):
        with self._lock:
            return len(self._rows)

    def all(self):
        with self._lock:
            return [dict(r) for r in self._rows]


class MemoryStorage(Storage):
    def __init__(self) -> None:
        models = MemoryModelStore()
        super().__init__(
            classifier=MemoryClassifierStore(models), models=models,
            appeals=MemoryAppealStore(), jobs=MemoryJobStore(), historical=MemoryHistoricalStore(),
        )

"""PostgreSQL-реализация хранилищ."""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator, Optional

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from . import appeal_common as ac
from . import db
from .models import ClassifierEntry, Job, ModelVersion, ModelVersionExists, QueueFull, Storage

_ID_RE = re.compile(r"^[0-9a-f]{32}$")

ConnectFn = Callable[[], ContextManager[psycopg.Connection]]


class _PgBase:
    def __init__(self, connect: ConnectFn) -> None:
        self._connect = connect

    @contextmanager
    def _tx(self, conn: Optional[psycopg.Connection] = None) -> Iterator[psycopg.Connection]:
        """Одна транзакция на вызов; переданное соединение переиспользуется без commit."""
        if conn is not None:
            yield conn
        else:
            with self._connect() as c:
                yield c


def _f32(v) -> np.ndarray:
    return np.asarray(v, dtype=np.float32)


class PgClassifierStore(_PgBase):
    def upsert_entries(self, entries, conn=None):
        with self._tx(conn) as c:
            with c.cursor() as cur:
                cur.executemany(
                    """INSERT INTO classifier_entries
                       (code, name, level, parent_code, full_path, children_count, sort_order)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name,
                         level=EXCLUDED.level, parent_code=EXCLUDED.parent_code,
                         full_path=EXCLUDED.full_path, children_count=EXCLUDED.children_count,
                         sort_order=EXCLUDED.sort_order""",
                    [
                        (e.code, e.name, e.level, e.parent_code, e.full_path, e.children_count,
                         i if e.sort_order is None else e.sort_order)
                        for i, e in enumerate(entries)
                    ],
                )
        return len(entries)

    def load_entries(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT e.code, e.name, e.level, e.parent_code, e.full_path,
                          e.children_count, coalesce(b.search_text, ''), e.sort_order
                   FROM classifier_entries e
                   LEFT JOIN LATERAL (
                     SELECT v.search_text FROM classifier_vectors v
                     JOIN ml_model_registry m ON m.version = v.model_version AND m.is_active
                     WHERE v.code = e.code AND v.source_type = 'base'
                     ORDER BY v.id LIMIT 1
                   ) b ON true
                   ORDER BY e.sort_order NULLS LAST, e.code"""
            ).fetchall()
        return [ClassifierEntry(*r) for r in rows]

    def count_entries(self, conn=None):
        with self._tx(conn) as c:
            return c.execute("SELECT count(*) FROM classifier_entries").fetchone()[0]

    def add_vectors(self, version, rows, conn=None):
        with self._tx(conn) as c:
            with c.cursor() as cur:
                cur.executemany(
                    """INSERT INTO classifier_vectors
                       (code, source_type, search_text, embedding, model_version)
                       VALUES (%s,%s,%s,%s,%s)""",
                    [
                        (code, st, text, None if emb is None else _f32(emb), version)
                        for code, st, text, emb in rows
                    ],
                )
        return len(rows)

    def search(self, query, version, top_k, include_annotations, conn=None):
        with self._tx(conn) as c:
            with c.cursor() as cur:
                cur.execute("SET LOCAL hnsw.ef_search = 400")
                cur.execute(
                    """SELECT code, max(1 - (embedding <=> %s)) AS similarity
                       FROM classifier_vectors
                       WHERE model_version = %s AND embedding IS NOT NULL
                         AND (source_type = 'base' OR %s)
                       GROUP BY code ORDER BY similarity DESC, code LIMIT %s""",
                    (_f32(query), version, include_annotations, top_k),
                )
                return [(code, float(sim)) for code, sim in cur.fetchall()]

    def pending_annotations(self, version, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT id, search_text FROM classifier_vectors
                   WHERE model_version = %s AND source_type = 'annotation'
                     AND embedding IS NULL ORDER BY id""",
                (version,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def vector_text(self, vector_id, conn=None):
        with self._tx(conn) as c:
            row = c.execute(
                "SELECT search_text FROM classifier_vectors WHERE id = %s", (vector_id,)
            ).fetchone()
        return row[0] if row else None

    def set_embedding(self, vector_id, embedding, conn=None):
        with self._tx(conn) as c:
            c.execute(
                "UPDATE classifier_vectors SET embedding = %s WHERE id = %s",
                (_f32(embedding), vector_id),
            )

    def add_annotation(self, code, text, version, conn=None):
        with self._tx(conn) as c:
            return c.execute(
                """INSERT INTO classifier_vectors (code, source_type, search_text, model_version)
                   VALUES (%s,'annotation',%s,%s) RETURNING id""",
                (code, text, version),
            ).fetchone()[0]

    def annotations_for(self, code, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT search_text FROM classifier_vectors
                   WHERE code = %s AND source_type = 'annotation' ORDER BY id""",
                (code,),
            ).fetchall()
        return [r[0] for r in rows]

    def delete_annotations(self, code, text_prefix=None, conn=None):
        with self._tx(conn) as c:
            cur = c.execute(
                """DELETE FROM classifier_vectors
                   WHERE code = %s AND source_type = 'annotation'
                     AND (%s::text IS NULL OR starts_with(search_text, %s::text))""",
                (code, text_prefix, text_prefix),
            )
            return cur.rowcount

    def annotated_codes(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT DISTINCT code FROM classifier_vectors
                   WHERE source_type = 'annotation' ORDER BY code"""
            ).fetchall()
        return [r[0] for r in rows]

    def vectors_for_version(self, version, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT code, source_type, search_text FROM classifier_vectors
                   WHERE model_version = %s ORDER BY id""",
                (version,),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


_MODEL_COLS = (
    "version, base_model_name, model_path, is_active, recall5_before, "
    "recall5_after, training_samples_count, trained_at, report, adapter"
)


def _mv(r) -> ModelVersion:
    return ModelVersion(
        version=r[0], base_model_name=r[1], model_path=r[2], is_active=r[3],
        recall5_before=r[4], recall5_after=r[5], training_samples_count=r[6],
        trained_at=r[7], report=r[8], adapter=r[9],
    )


class PgModelStore(_PgBase):
    def register(self, mv, conn=None):
        with self._tx(conn) as c:
            if mv.is_active:
                # как в MemoryModelStore: активна только одна версия (уникальный индекс)
                c.execute(
                    "UPDATE ml_model_registry SET is_active = false WHERE is_active AND version <> %s",
                    (mv.version,),
                )
            c.execute(
                f"""INSERT INTO ml_model_registry ({_MODEL_COLS})
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (version) DO UPDATE SET base_model_name=EXCLUDED.base_model_name,
                      model_path=EXCLUDED.model_path, is_active=EXCLUDED.is_active,
                      recall5_before=EXCLUDED.recall5_before, recall5_after=EXCLUDED.recall5_after,
                      training_samples_count=EXCLUDED.training_samples_count,
                      trained_at=EXCLUDED.trained_at, report=EXCLUDED.report,
                      adapter=EXCLUDED.adapter""",
                (
                    mv.version, mv.base_model_name, mv.model_path, mv.is_active,
                    mv.recall5_before, mv.recall5_after, mv.training_samples_count,
                    mv.trained_at,
                    None if mv.report is None else Jsonb(mv.report),
                    None if mv.adapter is None else Jsonb(mv.adapter),
                ),
            )

    def register_new(self, mv, conn=None):
        """Регистрация только новой версии (дообучение): под advisory-локом реестра
        до конца транзакции; занятый номер → ModelVersionExists. Upsert register
        (миграция) не меняется."""
        with self._tx(conn) as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtext('ml_model_registry'))")
            if c.execute("SELECT 1 FROM ml_model_registry WHERE version = %s",
                         (mv.version,)).fetchone():
                raise ModelVersionExists(f"версия модели {mv.version} уже есть в реестре")
            self.register(mv, conn=c)

    def active(self, conn=None):
        with self._tx(conn) as c:
            r = c.execute(f"SELECT {_MODEL_COLS} FROM ml_model_registry WHERE is_active").fetchone()
        return _mv(r) if r else None

    def get(self, version, conn=None):
        with self._tx(conn) as c:
            r = c.execute(
                f"SELECT {_MODEL_COLS} FROM ml_model_registry WHERE version = %s", (version,)
            ).fetchone()
        return _mv(r) if r else None

    def list(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                f"SELECT {_MODEL_COLS} FROM ml_model_registry ORDER BY version"
            ).fetchall()
        return [_mv(r) for r in rows]

    def activate(self, version, conn=None):
        with self._tx(conn) as c:
            exists = c.execute(
                "SELECT 1 FROM ml_model_registry WHERE version = %s", (version,)
            ).fetchone()
            if not exists:
                raise KeyError(version)
            c.execute("UPDATE ml_model_registry SET is_active = false WHERE is_active")
            c.execute("UPDATE ml_model_registry SET is_active = true WHERE version = %s", (version,))
            c.execute("SELECT pg_notify('model_changed', %s)", (str(version),))

    def next_version(self, conn=None):
        with self._tx(conn) as c:
            return c.execute(
                "SELECT coalesce(max(version), -1) + 1 FROM ml_model_registry"
            ).fetchone()[0]


class PgAppealStore(_PgBase):
    def create(self, appeal_text, document_id=None, ip_address=None, appeal_id=None,
               created_at=None, conn=None):
        if appeal_id:
            aid = ac.norm_uuid(appeal_id)
            if aid is None:
                raise ValueError(f"appeal_id не UUID: {appeal_id!r}")
        else:
            aid = str(uuid.uuid4())
        created = ac.aware(created_at) if created_at else ac.now_aware()
        with self._tx(conn) as c:
            c.execute(
                """INSERT INTO appeal_classifications
                   (id, document_id, appeal_text, ip_address, created_at)
                   VALUES (%s, %s, %s, %s::inet, %s)""",
                (aid, document_id, appeal_text, ac.norm_ip(ip_address), created),
            )
            c.execute(
                "INSERT INTO appeal_verifications (appeal_id, status) VALUES (%s, 'pending')",
                (aid,),
            )
        return aid

    def save_result(self, appeal_id, result, conn=None):
        aid = ac.norm_uuid(appeal_id)
        if aid is None:
            raise KeyError(appeal_id)
        with self._tx(conn) as c:
            cur = c.execute(
                """UPDATE appeal_classifications SET summary=%s, applicant_fio=%s,
                     applicant_email=%s, vid_obrascheniya=%s, tip_obrascheniya=%s,
                     is_ustnoe=%s, overall_confidence=%s, needs_verification=%s,
                     full_fallback_used=%s, candidates=%s, llm_provider=%s, llm_model=%s,
                     elapsed_seconds=coalesce(%s, elapsed_seconds)
                   WHERE id=%s""",
                (
                    result.summary, result.applicant_fio, result.applicant_email,
                    result.vid_obrascheniya, result.tip_obrascheniya, result.is_ustnoe,
                    result.overall_confidence, result.needs_verification,
                    result.full_fallback_used, Jsonb(result.candidates),
                    result.llm_provider, result.llm_model, result.elapsed_seconds, aid,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(appeal_id)
            c.execute("DELETE FROM appeal_questions WHERE appeal_id = %s", (aid,))
            with c.cursor() as cur2:
                cur2.executemany(
                    """INSERT INTO appeal_questions
                       (appeal_id, ordinal, question_text, selected_code, predmet_vedeniya,
                        confidence, reasoning, alternative_codes, verification_reasons)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            aid, q.ordinal, q.question_text, q.selected_code,
                            q.predmet_vedeniya, q.confidence, q.reasoning,
                            Jsonb(list(q.alternative_codes)), Jsonb(list(q.verification_reasons)),
                        )
                        for q in result.questions
                    ],
                )

    def get_text(self, appeal_id, conn=None):
        aid = ac.norm_uuid(appeal_id)
        if aid is None:
            return None
        with self._tx(conn) as c:
            r = c.execute(
                "SELECT appeal_text FROM appeal_classifications WHERE id = %s", (aid,)
            ).fetchone()
        return r[0] if r else None

    def set_verification(self, appeal_id, status, operator_codes, comment,
                         verified_by=None, verified_at=None, conn=None):
        if status not in ac.STATUSES:
            raise ValueError(f"неизвестный статус верификации: {status!r}")
        aid = ac.norm_uuid(appeal_id)
        if aid is None:
            return False
        if status == "pending":
            verified_at = None
        else:
            verified_at = ac.aware(verified_at) if verified_at else ac.now_aware()
        with self._tx(conn) as c:
            cur = c.execute(
                """UPDATE appeal_verifications SET status=%s, operator_codes=%s, comment=%s,
                     verified_at=%s, verified_by=%s WHERE appeal_id=%s""",
                (
                    status,
                    None if operator_codes is None else Jsonb(list(operator_codes)),
                    comment, verified_at, verified_by, aid,
                ),
            )
            return cur.rowcount > 0

    def as_log_entries(self, statuses=None, conn=None):
        with self._tx(conn) as c:
            sql = """SELECT a.id, a.created_at, a.appeal_text, a.candidates,
                            a.overall_confidence, v.status, v.verified_at,
                            v.operator_codes, v.comment
                     FROM appeal_classifications a
                     JOIN appeal_verifications v ON v.appeal_id = a.id"""
            params: tuple = ()
            if statuses is not None:
                sql += " WHERE v.status = ANY(%s)"
                params = (list(statuses),)
            rows = c.execute(sql + " ORDER BY a.created_at, a.id", params).fetchall()
            qrows = c.execute(
                """SELECT q.appeal_id, q.question_text, q.selected_code, q.confidence,
                          q.alternative_codes
                   FROM appeal_questions q ORDER BY q.appeal_id, q.ordinal"""
            ).fetchall() if rows else []
        by_appeal: dict[str, list] = {}
        for aid, text, code, conf, alts in qrows:
            by_appeal.setdefault(str(aid), []).append({
                "question_text": text,
                "selected_code": code,
                "confidence": ac.real(conf),
                "alternative_codes": alts or [],
            })
        out = []
        for aid, created, text, cands, conf, status, vat, ops, comment in rows:
            out.append({
                "id": str(aid),
                "timestamp": ac.local_iso(created),
                "appeal_text": text,
                "candidates": cands or [],
                "agent_questions": by_appeal.get(str(aid), []),
                "overall_confidence": ac.real(conf) or 0.0,
                "verification": {
                    "status": status,
                    "verified_at": ac.local_iso(vat),
                    "operator_codes": ops,
                    "comment": comment,
                },
            })
        return out

    def stats(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                "SELECT status, count(*) FROM appeal_verifications GROUP BY status"
            ).fetchall()
        counts = {s: 0 for s in ac.STATUSES}
        counts.update({s: n for s, n in rows})
        return {
            "total": sum(counts.values()),
            "verified": counts["confirmed"] + counts["corrected"],
            "pending": counts["pending"],
            "confirmed": counts["confirmed"],
            "corrected": counts["corrected"],
            "rejected": counts["rejected"],
        }

    def backoffice_stats(self, days=30, conn=None):
        """Агрегаты для бэк-офиса.

        Карточки с NULL overall_confidence (ещё не классифицированы) входят в
        total_classifications и в статусы, но не в гистограмму и не в avg_confidence.
        top_codes считаются только по confirmed/corrected; ip_stats - только по
        карточкам с известным ip (NULL исключается).
        """
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT a.created_at, a.overall_confidence, v.status, host(a.ip_address),
                          coalesce(array_agg(q.selected_code)
                                   FILTER (WHERE q.selected_code IS NOT NULL), '{}')
                   FROM appeal_classifications a
                   JOIN appeal_verifications v ON v.appeal_id = a.id
                   LEFT JOIN appeal_questions q ON q.appeal_id = a.id
                   GROUP BY a.id, v.status"""
            ).fetchall()
        return ac.compute_backoffice_stats(rows, days)

    def log_llm_request(self, appeal_id, success, attempt, elapsed, confidence, error,
                        candidates_count, created_at=None, conn=None):
        with self._tx(conn) as c:
            c.execute(
                """INSERT INTO llm_request_log
                   (appeal_id, success, attempt, elapsed_seconds, confidence,
                    candidates_count, error, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s, coalesce(%s, now()))""",
                (ac.norm_uuid(appeal_id), success, attempt, elapsed, confidence,
                 candidates_count, error, ac.aware(created_at) if created_at else None),
            )

    def ip_addresses(self, appeal_ids, conn=None):
        ids = [a for a in (ac.norm_uuid(x) for x in appeal_ids) if a]
        if not ids:
            return {}
        with self._tx(conn) as c:
            rows = c.execute(
                "SELECT id, host(ip_address) FROM appeal_classifications WHERE id = ANY(%s::uuid[])",
                (ids,),
            ).fetchall()
        return {str(r[0]): r[1] for r in rows}

    def llm_log_count(self, conn=None):
        with self._tx(conn) as c:
            return c.execute("SELECT count(*) FROM llm_request_log").fetchone()[0]

    def set_request_meta(self, appeal_id, ip_address, elapsed_seconds, conn=None):
        aid = ac.norm_uuid(appeal_id)
        if aid is None:
            return
        with self._tx(conn) as c:
            c.execute(
                """UPDATE appeal_classifications
                   SET ip_address = coalesce(%s::inet, ip_address),
                       elapsed_seconds = coalesce(%s, elapsed_seconds)
                   WHERE id = %s""",
                (ac.norm_ip(ip_address), elapsed_seconds, aid),
            )


_JOB_COLS = ("id, job_type, status, appeal_id, document_id, webhook_url, payload, result, "
             "error, attempts, created_at, started_at, finished_at")
_QUEUE_EXPR = "coalesce(payload->>'queue', 'classify')"


def _job(r) -> Job:
    return Job(
        id=r[0].strip(), job_type=r[1], status=r[2],
        appeal_id=None if r[3] is None else str(r[3]),
        document_id=r[4], webhook_url=r[5], payload=r[6] or {}, result=r[7], error=r[8],
        attempts=r[9], created_at=r[10], started_at=r[11], finished_at=r[12],
    )


class PgJobStore(_PgBase):
    def create(self, job_type, payload, appeal_id=None, document_id=None, webhook_url=None,
               max_queued=None, queue_class="classify", conn=None):
        job_id = uuid.uuid4().hex
        aid = ac.norm_uuid(appeal_id) if appeal_id else None
        with self._tx(conn) as c:
            if max_queued is not None:
                # Счёт и вставка в одной транзакции под advisory-локом: без него
                # два параллельных запроса оба увидят свободное место.
                c.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('jobs:' || %s || ':' || %s))",
                    (job_type, queue_class),
                )
                queued = c.execute(
                    f"""SELECT count(*) FROM jobs
                        WHERE status = 'queued' AND job_type = %s AND {_QUEUE_EXPR} = %s""",
                    (job_type, queue_class),
                ).fetchone()[0]
                if queued >= max_queued:
                    raise QueueFull(f"в очереди уже {queued} задач, лимит {max_queued}")
            row = c.execute(
                f"""INSERT INTO jobs (id, job_type, status, appeal_id, document_id, webhook_url,
                                      payload, created_at)
                    VALUES (%s, %s, 'queued', %s, %s, %s, %s, clock_timestamp())
                    RETURNING {_JOB_COLS}""",
                (job_id, job_type, aid, document_id, webhook_url,
                 Jsonb({**payload, "queue": queue_class})),
            ).fetchone()
        return _job(row)

    def get(self, job_id, conn=None):
        if not _ID_RE.match(job_id or ""):
            return None
        with self._tx(conn) as c:
            row = c.execute(f"SELECT {_JOB_COLS} FROM jobs WHERE id = %s", (job_id,)).fetchone()
        return _job(row) if row else None

    def claim(self, job_id, conn=None):
        if not _ID_RE.match(job_id or ""):
            return None
        with self._tx(conn) as c:
            row = c.execute(
                f"""UPDATE jobs SET status = 'running', attempts = attempts + 1,
                           started_at = clock_timestamp()
                    WHERE id = %s AND status IN ('queued', 'running')
                    RETURNING {_JOB_COLS}""",
                (job_id,),
            ).fetchone()
        return _job(row) if row else None

    def lock_owned(self, job_id, attempts, conn=None):
        """Попытка `attempts` всё ещё владеет задачей (running)? Строка блокируется.

        FOR UPDATE держит блокировку до конца транзакции вызывающего: claim
        повторной доставки ждёт commit и затем видит завершённую задачу.
        """
        if not _ID_RE.match(job_id or ""):
            return False
        with self._tx(conn) as c:
            row = c.execute(
                """SELECT 1 FROM jobs
                   WHERE id = %s AND status = 'running' AND attempts = %s
                   FOR UPDATE""",
                (job_id, attempts),
            ).fetchone()
        return row is not None

    def finish(self, job_id, result, error, conn=None, attempts=None):
        """Завершить задачу. attempts задан → только если это всё ещё та попытка."""
        if not _ID_RE.match(job_id or ""):
            return False
        with self._tx(conn) as c:
            row = c.execute(
                """UPDATE jobs SET status = %s, result = %s, error = %s,
                          finished_at = clock_timestamp()
                   WHERE id = %s AND status IN ('queued', 'running')
                     AND (%s::int IS NULL OR (status = 'running' AND attempts = %s))
                   RETURNING id""",
                ("error" if error is not None else "done",
                 None if result is None else Jsonb(result), error, job_id,
                 attempts, attempts),
            ).fetchone()
            if row is None:
                return False
            # Доставляется подписчикам при commit этой же транзакции.
            c.execute("SELECT pg_notify('job_done', %s)", (job_id,))
        return True

    def position(self, job_id, conn=None):
        if not _ID_RE.match(job_id or ""):
            return None
        with self._tx(conn) as c:
            row = c.execute(
                f"""SELECT rn FROM (
                      SELECT id, row_number() OVER (ORDER BY created_at, id) AS rn
                      FROM jobs
                      WHERE status = 'queued'
                        AND job_type = (SELECT job_type FROM jobs WHERE id = %s)
                        AND {_QUEUE_EXPR} = (SELECT {_QUEUE_EXPR} FROM jobs WHERE id = %s)
                    ) t WHERE id = %s""",
                (job_id, job_id, job_id),
            ).fetchone()
        return int(row[0]) if row else None

    def list(self, limit=50, job_type=None, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                f"""SELECT {_JOB_COLS} FROM jobs
                    WHERE (%s::text IS NULL OR job_type = %s)
                    ORDER BY created_at DESC, id DESC LIMIT %s""",
                (job_type, job_type, limit),
            ).fetchall()
        return [_job(r) for r in rows]

    def stats(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute("SELECT status, count(*) FROM jobs GROUP BY status").fetchall()
        counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
        counts.update({s: int(n) for s, n in rows})
        return counts

    def latest(self, job_type, conn=None):
        found = self.list(limit=1, job_type=job_type, conn=conn)
        return found[0] if found else None

    def has_active(self, job_type, conn=None):
        with self._tx(conn) as c:
            return c.execute(
                """SELECT EXISTS (SELECT 1 FROM jobs
                                  WHERE job_type = %s AND status IN ('queued', 'running'))""",
                (job_type,),
            ).fetchone()[0]

    def cleanup_expired(self, ttl_hours, conn=None):
        with self._tx(conn) as c:
            cur = c.execute(
                """DELETE FROM jobs
                   WHERE status IN ('done', 'error')
                     AND coalesce(finished_at, created_at) <= now() - make_interval(secs => %s)""",
                (ttl_hours * 3600,),
            )
            return cur.rowcount

    def import_job(self, job, conn=None):
        with self._tx(conn) as c:
            c.execute(
                """INSERT INTO jobs (id, job_type, status, appeal_id, document_id, webhook_url,
                                     payload, result, error, attempts, created_at, started_at,
                                     finished_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING""",
                (job.id, job.job_type, job.status, job.appeal_id, job.document_id,
                 job.webhook_url, Jsonb(job.payload),
                 None if job.result is None else Jsonb(job.result), job.error, job.attempts,
                 job.created_at, job.started_at, job.finished_at),
            )


class PgHistoricalStore(_PgBase):
    def add(self, records, source_file, conn=None):
        inserted = 0
        with self._tx(conn) as c:
            for r in records:
                cur = c.execute(
                    """INSERT INTO historical_appeals
                           (source_file, appeal_text, assigned_code, specialist, registration_date)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (source_file, r.get("appeal_text", ""), r.get("assigned_code", ""),
                     r.get("specialist"), r.get("date")),
                )
                inserted += cur.rowcount
        return inserted

    def count(self, conn=None):
        with self._tx(conn) as c:
            return c.execute("SELECT count(*) FROM historical_appeals").fetchone()[0]

    def all(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT appeal_text, assigned_code, specialist, registration_date, source_file
                   FROM historical_appeals ORDER BY id"""
            ).fetchall()
        return [
            {"appeal_text": r[0], "assigned_code": r[1], "specialist": r[2],
             "registration_date": r[3], "source_file": r[4]}
            for r in rows
        ]


class PgStorage(Storage):
    def __init__(self, connect: ConnectFn = db.transaction) -> None:
        self._connect = connect
        super().__init__(
            classifier=PgClassifierStore(connect),
            models=PgModelStore(connect),
            appeals=PgAppealStore(connect),
            jobs=PgJobStore(connect),
            historical=PgHistoricalStore(connect),
        )

    def transaction(self):
        """Одна транзакция: commit на выходе, rollback при исключении; отдаёт соединение."""
        return self._connect()

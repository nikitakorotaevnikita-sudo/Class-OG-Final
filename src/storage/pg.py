"""PostgreSQL-реализация хранилищ."""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator, Optional

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from core import config

from . import appeal_common as ac
from . import db
from .base import DuplicateError
from .models import (JOB_QUEUES, LOST_TRAINING_ERROR, WORKER_APP_PREFIX, ActiveJobExists,
                     ClassifierEntry, Job, ModelVersion, ModelVersionExists, QueueFull,
                     Storage, assign_sort_orders, check_job_queue, check_job_values,
                     worker_queues)

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
        """sort_order=None: у существующей записи сохраняется, новые получают max+1, ..."""
        with self._tx(conn) as c:
            stored = dict(c.execute(
                "SELECT code, sort_order FROM classifier_entries WHERE code = ANY(%s)",
                ([e.code for e in entries],),
            ).fetchall())
            top = c.execute("SELECT max(sort_order) FROM classifier_entries").fetchone()[0]
            orders = assign_sort_orders(entries, stored, top)
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
                         order)
                        for e, order in zip(entries, orders)
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
                # <=> — косинусное расстояние (нормирует сам). Векторы прода единичные
                # (normalize_embeddings=True), поэтому 1 - <=> равно прежнему скалярному
                # произведению numpy-поиска (паритет: verify_search_parity.py).
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

    def annotations_by_code(self, conn=None):
        """{code: [тексты аннотаций по порядку id]} одним запросом (без N+1)."""
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT code, search_text FROM classifier_vectors
                   WHERE source_type = 'annotation' ORDER BY id"""
            ).fetchall()
        out: dict[str, list[str]] = {}
        for code, text in rows:
            out.setdefault(code, []).append(text)
        return out

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
            # Сигнал для внешних систем (мониторинг). Воркеры его не слушают: они
            # сверяют активную версию в реестре перед каждой задачей.
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
            try:
                # Точка сохранения: дубль не обрывает транзакцию вызывающего.
                with c.transaction():
                    c.execute(
                        """INSERT INTO appeal_classifications
                           (id, document_id, appeal_text, ip_address, created_at)
                           VALUES (%s, %s, %s, %s::inet, %s)""",
                        (aid, document_id, appeal_text, ac.norm_ip(ip_address), created),
                    )
            except psycopg.errors.UniqueViolation as exc:
                raise DuplicateError(f"обращение {aid} уже существует") from exc
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
            # Вопросы только выбранных карточек (фильтр по статусам уже применён).
            qrows = c.execute(
                """SELECT q.appeal_id, q.question_text, q.selected_code, q.confidence,
                          q.alternative_codes
                   FROM appeal_questions q
                   WHERE q.appeal_id = ANY(%s::uuid[])
                   ORDER BY q.appeal_id, q.ordinal""",
                ([r[0] for r in rows],),
            ).fetchall() if rows else []
        by_appeal: dict[str, list] = {}
        for aid, text, code, conf, alts in qrows:
            by_appeal.setdefault(str(aid), []).append(ac.question_entry(text, code, conf, alts))
        return [
            ac.log_entry(aid, created, text, cands, conf, by_appeal.get(str(aid), []),
                         status, vat, ops, comment)
            for aid, created, text, cands, conf, status, vat, ops, comment in rows
        ]

    def stats(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                "SELECT status, count(*) FROM appeal_verifications GROUP BY status"
            ).fetchall()
        return ac.stats_payload(dict(rows))

    def backoffice_stats(self, days=30, conn=None):
        """Агрегаты для бэк-офиса (считаются в SQL, карточки целиком не читаются).

        Карточки с NULL overall_confidence (ещё не классифицированы) входят в
        total_classifications и в статусы, но не в гистограмму и не в avg_confidence.
        top_codes считаются только по confirmed/corrected; ip_stats - только по
        карточкам с известным ip (NULL исключается). daily_usage - по created_at
        карточек за окно (группировка по локальной дате процесса, как в memory).
        """
        real_conf = ac.REAL_SQL.format(col="overall_confidence")
        hist_idx = ac.HIST_INDEX_SQL.format(col="overall_confidence")
        with self._tx(conn) as c:
            status_counts = dict(c.execute(
                "SELECT status, count(*) FROM appeal_verifications GROUP BY status"
            ).fetchall())
            # sum в float8: та же арифметика, что сумма float в memory (граница округления)
            conf_sum, conf_n = c.execute(
                f"""SELECT coalesce(sum({real_conf}::float8), 0), count(overall_confidence)
                    FROM appeal_classifications"""
            ).fetchone()
            hist_counts = dict(c.execute(
                f"""SELECT {hist_idx}, count(*) FROM appeal_classifications
                    WHERE overall_confidence IS NOT NULL GROUP BY 1"""
            ).fetchall())
            code_counts = dict(c.execute(
                """SELECT q.selected_code, count(*)
                   FROM appeal_questions q
                   JOIN appeal_verifications v ON v.appeal_id = q.appeal_id
                   WHERE v.status IN ('confirmed', 'corrected')
                     AND q.selected_code IS NOT NULL AND q.selected_code <> ''
                   GROUP BY q.selected_code"""
            ).fetchall())
            ip_counts = dict(c.execute(
                """SELECT host(ip_address), count(*) FROM appeal_classifications
                   WHERE ip_address IS NOT NULL GROUP BY 1"""
            ).fetchall())
            recent = [r[0] for r in c.execute(
                "SELECT created_at FROM appeal_classifications WHERE created_at >= %s",
                (ac.window_start(days),),
            ).fetchall()]
        return ac.backoffice_payload(
            status_counts=status_counts, conf_sum=conf_sum, conf_n=conf_n,
            hist_counts=hist_counts, code_counts=code_counts, ip_counts=ip_counts,
            recent_created=recent, days=days,
        )

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

    def set_request_meta(self, appeal_id, ip_address, elapsed_seconds, only_missing=False,
                         conn=None):
        """only_missing: заполнить только пустые поля (живые значения не перетираются)."""
        aid = ac.norm_uuid(appeal_id)
        if aid is None:
            return
        if only_missing:
            sets = ("coalesce(ip_address, %s::inet)", "coalesce(elapsed_seconds, %s)")
        else:
            sets = ("coalesce(%s::inet, ip_address)", "coalesce(%s, elapsed_seconds)")
        with self._tx(conn) as c:
            c.execute(
                f"""UPDATE appeal_classifications
                    SET ip_address = {sets[0]}, elapsed_seconds = {sets[1]}
                    WHERE id = %s""",
                (ac.norm_ip(ip_address), elapsed_seconds, aid),
            )


_JOB_COLS = ("id, job_type, status, appeal_id, document_id, webhook_url, payload, result, "
             "error, attempts, created_at, started_at, finished_at, "
             "queue, available_at, locked_until, worker_id, delivered_at")


def _job(r) -> Job:
    return Job(
        id=r[0].strip(), job_type=r[1], status=r[2],
        appeal_id=None if r[3] is None else str(r[3]),
        document_id=r[4], webhook_url=r[5], payload=r[6] or {}, result=r[7], error=r[8],
        attempts=r[9], created_at=r[10], started_at=r[11], finished_at=r[12],
        queue=r[13], available_at=r[14], locked_until=r[15], worker_id=r[16],
        delivered_at=r[17],
    )


class PgJobStore(_PgBase):
    def create(self, job_type, payload, appeal_id=None, document_id=None, webhook_url=None,
               max_queued=None, queue_class="classify", conn=None):
        """Поставить задачу в очередь queue_class; NOTIFY job_queued уходит при commit."""
        check_job_values(job_type)
        check_job_queue(queue_class)
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
                        WHERE status = 'queued' AND job_type = %s AND queue = %s""",
                    (job_type, queue_class),
                ).fetchone()[0]
                if queued >= max_queued:
                    raise QueueFull(f"в очереди уже {queued} задач, лимит {max_queued}")
            try:
                # Точка сохранения: нарушение индекса не обрывает внешнюю транзакцию.
                with c.transaction():
                    row = c.execute(
                        f"""INSERT INTO jobs (id, job_type, status, appeal_id, document_id,
                                              webhook_url, payload, queue, created_at,
                                              available_at)
                            VALUES (%s, %s, 'queued', %s, %s, %s, %s, %s, clock_timestamp(),
                                    now())
                            RETURNING {_JOB_COLS}""",
                        (job_id, job_type, aid, document_id, webhook_url,
                         Jsonb({**payload, "queue": queue_class}), queue_class),
                    ).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                if exc.diag.constraint_name == "one_active_finetune":
                    raise ActiveJobExists("дообучение уже в очереди или выполняется") from exc
                raise
            # Будит воркеры этой очереди; доставляется при commit этой же транзакции.
            c.execute("SELECT pg_notify('job_queued', %s)", (queue_class,))
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
                          finished_at = clock_timestamp(), locked_until = NULL
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

    # --- очередь: захват, аренда, повтор, доставка ---
    def claim_next(self, queues, worker_id, lease_sec, conn=None):
        """Взять одну готовую задачу из очередей `queues` одним атомарным запросом.

        Сначала classify_sync, затем по available_at, created_at. SKIP LOCKED: параллельные
        воркеры не берут одну строку. Задача с истёкшей арендой (воркер умер) перехватывается
        с attempts+1 — кроме очереди training (её закрывает fail_lost_training).
        """
        with self._tx(conn) as c:
            row = c.execute(
                f"""UPDATE jobs SET status = 'running', attempts = attempts + 1,
                           started_at = coalesce(started_at, now()),
                           locked_until = now() + make_interval(secs => %s),
                           worker_id = %s
                    WHERE id = (
                      SELECT id FROM jobs
                      WHERE queue = ANY(%s)
                        AND ((status = 'queued' AND available_at <= now())
                             OR (status = 'running' AND queue <> 'training'
                                 AND locked_until < now()))
                      ORDER BY (queue = 'classify_sync') DESC, available_at, created_at, id
                      FOR UPDATE SKIP LOCKED
                      LIMIT 1)
                    RETURNING {_JOB_COLS}""",
                (float(lease_sec), worker_id, list(queues)),
            ).fetchone()
        return _job(row) if row else None

    def fail_lost_training(self, conn=None):
        """Дообучение с истёкшей арендой → error (не перезапускается); NOTIFY job_done."""
        with self._tx(conn) as c:
            rows = c.execute(
                f"""UPDATE jobs SET status = 'error', error = %s,
                           finished_at = now(), locked_until = NULL
                    WHERE queue = 'training' AND status = 'running'
                      AND locked_until < now()
                    RETURNING {_JOB_COLS}""",
                (LOST_TRAINING_ERROR,),
            ).fetchall()
            for r in rows:
                c.execute("SELECT pg_notify('job_done', %s)", (r[0].strip(),))
        return sorted((_job(r) for r in rows), key=lambda j: (j.created_at, j.id))

    def extend_lease(self, job_id, attempts, lease_sec, conn=None):
        """Продлить аренду; только владелец (running с тем же attempts)."""
        if not _ID_RE.match(job_id or ""):
            return False
        with self._tx(conn) as c:
            row = c.execute(
                """UPDATE jobs SET locked_until = now() + make_interval(secs => %s)
                   WHERE id = %s AND status = 'running' AND attempts = %s
                   RETURNING id""",
                (float(lease_sec), job_id, attempts),
            ).fetchone()
        return row is not None

    def release_for_retry(self, job_id, attempts, delay_sec, error, conn=None):
        """Вернуть задачу в очередь после сбоя: брать не раньше чем через delay_sec."""
        if not _ID_RE.match(job_id or ""):
            return False
        with self._tx(conn) as c:
            row = c.execute(
                """UPDATE jobs SET status = 'queued', locked_until = NULL, error = %s,
                          available_at = now() + make_interval(secs => %s)
                   WHERE id = %s AND status = 'running' AND attempts = %s
                   RETURNING id""",
                (error, float(delay_sec), job_id, attempts),
            ).fetchone()
        return row is not None

    def mark_delivered(self, job_id, conn=None):
        """Конечный результат отдан потребителю (только done/error, метка не сдвигается)."""
        if not _ID_RE.match(job_id or ""):
            return
        with self._tx(conn) as c:
            c.execute(
                """UPDATE jobs SET delivered_at = clock_timestamp()
                   WHERE id = %s AND status IN ('done', 'error') AND delivered_at IS NULL""",
                (job_id,),
            )

    def delete(self, job_id, conn=None):
        if not _ID_RE.match(job_id or ""):
            return
        with self._tx(conn) as c:
            c.execute("DELETE FROM jobs WHERE id = %s", (job_id,))

    def live_workers(self, queue, conn=None):
        """application_name живых соединений воркеров, обслуживающих очередь `queue`."""
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT application_name FROM pg_stat_activity
                   WHERE datname = current_database()
                     AND application_name LIKE %s
                   ORDER BY backend_start""",
                (WORKER_APP_PREFIX + "%",),
            ).fetchall()
        return [r[0] for r in rows if queue in worker_queues(r[0])]

    def position(self, job_id, conn=None):
        if not _ID_RE.match(job_id or ""):
            return None
        with self._tx(conn) as c:
            row = c.execute(
                """SELECT rn FROM (
                      SELECT id, row_number() OVER (ORDER BY created_at, id) AS rn
                      FROM jobs
                      WHERE status = 'queued'
                        AND job_type = (SELECT job_type FROM jobs WHERE id = %s)
                        AND queue = (SELECT queue FROM jobs WHERE id = %s)
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

    def stats(self, job_type=None, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT status, count(*) FROM jobs
                   WHERE (%s::text IS NULL OR job_type = %s) GROUP BY status""",
                (job_type, job_type),
            ).fetchall()
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

    def cleanup_expired(self, ttl_hours, delivered_grace_sec=None, conn=None):
        """Удалить конечные задачи: забранные раньше grace (кроме finetune) и все старше TTL.

        delivered_grace_sec=None → config.JOB_DELIVERED_GRACE_SEC.
        """
        if delivered_grace_sec is None:
            delivered_grace_sec = config.JOB_DELIVERED_GRACE_SEC
        with self._tx(conn) as c:
            cur = c.execute(
                """DELETE FROM jobs
                   WHERE status IN ('done', 'error')
                     AND (coalesce(finished_at, created_at) <= now() - make_interval(secs => %s)
                          OR (job_type <> 'finetune'
                              AND delivered_at <= now() - make_interval(secs => %s)))""",
                (float(ttl_hours) * 3600, float(delivered_grace_sec)),
            )
            return cur.rowcount

    def import_job(self, job, conn=None):
        check_job_values(job.job_type, job.status)
        # как backfill в schema.sql: очередь из payload, иначе classify
        queue = (job.payload or {}).get("queue")
        queue = queue if queue in JOB_QUEUES else "classify"
        with self._tx(conn) as c:
            c.execute(
                """INSERT INTO jobs (id, job_type, status, appeal_id, document_id, webhook_url,
                                     payload, result, error, attempts, created_at, started_at,
                                     finished_at, queue, available_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING""",
                (job.id, job.job_type, job.status, job.appeal_id, job.document_id,
                 job.webhook_url, Jsonb(job.payload),
                 None if job.result is None else Jsonb(job.result), job.error, job.attempts,
                 job.created_at, job.started_at, job.finished_at, queue,
                 job.available_at or job.created_at),
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


class PgMetaStore(_PgBase):
    """Служебные отметки (таблица schema_meta)."""

    def get(self, key, conn=None):
        with self._tx(conn) as c:
            row = c.execute("SELECT value FROM schema_meta WHERE key = %s", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value, conn=None):
        with self._tx(conn) as c:
            c.execute(
                """INSERT INTO schema_meta (key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                (key, value),
            )


class PgStorage(Storage):
    def __init__(self, connect: ConnectFn = db.transaction) -> None:
        self._connect = connect
        super().__init__(
            classifier=PgClassifierStore(connect),
            models=PgModelStore(connect),
            appeals=PgAppealStore(connect),
            jobs=PgJobStore(connect),
            historical=PgHistoricalStore(connect),
            meta=PgMetaStore(connect),
        )

    def transaction(self):
        """Одна транзакция: commit на выходе, rollback при исключении; отдаёт соединение."""
        return self._connect()

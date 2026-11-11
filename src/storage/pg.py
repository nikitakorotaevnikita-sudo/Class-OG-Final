"""PostgreSQL-реализация хранилищ."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator, Optional

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from . import db
from .models import ClassifierEntry, ModelVersion, Storage

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
                       (code, name, level, parent_code, full_path, children_count)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name,
                         level=EXCLUDED.level, parent_code=EXCLUDED.parent_code,
                         full_path=EXCLUDED.full_path, children_count=EXCLUDED.children_count""",
                    [
                        (e.code, e.name, e.level, e.parent_code, e.full_path, e.children_count)
                        for e in entries
                    ],
                )
        return len(entries)

    def load_entries(self, conn=None):
        with self._tx(conn) as c:
            rows = c.execute(
                """SELECT e.code, e.name, e.level, e.parent_code, e.full_path,
                          e.children_count, coalesce(b.search_text, '')
                   FROM classifier_entries e
                   LEFT JOIN LATERAL (
                     SELECT v.search_text FROM classifier_vectors v
                     JOIN ml_model_registry m ON m.version = v.model_version AND m.is_active
                     WHERE v.code = e.code AND v.source_type = 'base'
                     ORDER BY v.id LIMIT 1
                   ) b ON true
                   ORDER BY e.code"""
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
                       GROUP BY code ORDER BY similarity DESC LIMIT %s""",
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


class PgStorage(Storage):
    def __init__(self, connect: ConnectFn = db.transaction) -> None:
        super().__init__(classifier=PgClassifierStore(connect), models=PgModelStore(connect))

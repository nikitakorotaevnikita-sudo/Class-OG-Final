"""In-memory реализация хранилищ (тесты)."""

from __future__ import annotations

import dataclasses
import threading

import numpy as np

from .models import ClassifierEntry, ModelVersion, Storage


class MemoryModelStore:
    def __init__(self) -> None:
        self._items: dict[int, ModelVersion] = {}
        self._lock = threading.RLock()

    def register(self, mv):
        with self._lock:
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
        return sorted(best.items(), key=lambda kv: -kv[1])[:top_k]

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


class MemoryStorage(Storage):
    def __init__(self) -> None:
        models = MemoryModelStore()
        super().__init__(classifier=MemoryClassifierStore(models), models=models)

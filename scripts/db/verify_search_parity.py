"""Сверка плотного поиска: numpy (файлы VECTOR_DB_DIR) против хранилища (pgvector).

Для каждого запроса (обращения из --queries + --extra случайных обращений из БД)
сравнивает top-K кодов и их порядок; |Δsimilarity| должна быть < 1e-5.
Запуск из корня проекта (нужна БД с перенесёнными данными):
    python scripts/db/verify_search_parity.py --queries data/ii25_test.jsonl --extra 20
Печатает "parity OK (N/N)" либо первое расхождение и выходит с кодом 1.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np  # noqa: E402

TOL = 1e-5
TOP_K = 50


def numpy_topk(embeddings: np.ndarray, codes: list[str], query: np.ndarray, k: int):
    """Эталон: косинус по нормированным векторам, max по коду, порядок (-sim, code).

    Нормировка здесь явная: векторы прода (normalize_embeddings=True) уже единичные,
    и бывший numpy-поиск считал косинус простым скалярным произведением. pgvector
    (оператор <=>) нормирует сам, поэтому для единичных векторов значения совпадают.
    Возвращает (top-k [(code, sim)], {code: sim} по всем кодам).
    """
    q = np.asarray(query, dtype=np.float32)
    q = q / np.linalg.norm(q)
    norms = np.linalg.norm(embeddings, axis=1)
    sims = (embeddings @ q) / np.where(norms > 0, norms, 1.0)
    best: dict[str, float] = {}
    for code, sim in zip(codes, sims.tolist()):
        if code not in best or sim > best[code]:
            best[code] = sim
    ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:k], best


def compare_topk(np_top: list, db_top: list, np_all: dict, tol: float = TOL) -> Optional[str]:
    """None, если списки совпадают; иначе описание первого расхождения.

    Разный код на позиции допустим только при ничьей (similarity обоих кодов
    в пределах tol) — порядок равных значений зависит от округления float.
    Множества кодов тоже сверяются: коды без пары допустимы только при ничьей на
    границе top-K (similarity в пределах tol от последней), повторы в БД — нет.
    """
    if len(np_top) != len(db_top):
        return f"длина top: numpy {len(np_top)}, БД {len(db_top)}"
    for i, ((nc, ns), (dc, ds)) in enumerate(zip(np_top, db_top)):
        if abs(ns - ds) >= tol:
            return f"позиция {i}: similarity numpy {nc}={ns:.7f}, БД {dc}={ds:.7f}"
        if nc != dc:
            other = np_all.get(dc)
            if other is None or abs(other - ds) >= tol or abs(other - ns) >= tol:
                return f"позиция {i}: numpy {nc} ({ns:.7f}), БД {dc} ({ds:.7f})"
    np_codes, db_codes = [c for c, _ in np_top], [c for c, _ in db_top]
    if len(set(db_codes)) != len(db_codes):
        return "в top БД повторяются коды"
    only_np, only_db = set(np_codes) - set(db_codes), set(db_codes) - set(np_codes)
    if only_np or only_db:
        edge = np_top[-1][1]
        if any(np_all.get(c) is None or abs(np_all[c] - edge) >= tol
               for c in only_np | only_db):
            return (f"множества кодов top различаются: только numpy {sorted(only_np)}, "
                    f"только БД {sorted(only_db)}")
    return None


def _read_queries(path: Path) -> list[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                text = json.loads(line).get("appeal_text", "")
                if text:
                    out.append(text)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    from core import config
    from storage import get_storage

    p = argparse.ArgumentParser(description="Сверка numpy vs pgvector top-K")
    p.add_argument("--queries", default="data/ii25_test.jsonl")
    p.add_argument("--extra", type=int, default=20, help="случайных обращений из БД")
    p.add_argument("--vector-db-dir", default=config.VECTOR_DB_DIR)
    p.add_argument("--version", type=int, default=0)
    p.add_argument("--top-k", type=int, default=TOP_K)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    storage = get_storage()
    mv = storage.models.get(args.version)
    if mv is None:
        print(f"Версия модели {args.version} не найдена в ml_model_registry")
        return 1

    vdb = Path(args.vector_db_dir)
    embeddings = np.load(vdb / "embeddings.npy").astype(np.float32)
    with open(vdb / "metadata.json", encoding="utf-8") as f:
        codes = [m["code"] for m in json.load(f)]

    queries = _read_queries(Path(args.queries))
    pool = [e["appeal_text"] for e in storage.appeals.as_log_entries() if e.get("appeal_text")]
    random.Random(args.seed).shuffle(pool)
    queries += pool[:args.extra]

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(mv.model_path)
    prefix = "query: " if "e5" in mv.model_path.lower() else ""
    for n, text in enumerate(queries, 1):
        q = np.asarray(model.encode(prefix + text, normalize_embeddings=True), dtype=np.float32)
        np_top, np_all = numpy_topk(embeddings, codes, q, args.top_k)
        db_top = storage.classifier.search(q, args.version, args.top_k, False)
        diff = compare_topk(np_top, db_top, np_all)
        if diff:
            print(f"Расхождение на запросе {n}/{len(queries)}: {diff}")
            return 1
    print(f"parity OK ({len(queries)}/{len(queries)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

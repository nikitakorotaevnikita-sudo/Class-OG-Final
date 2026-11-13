"""
Построение векторов классификатора для версии модели в БД (classifier_vectors).

Для каждой записи классификатора:
  - базовая строка: search_text = build_search_text(...) (аннотации старого
    формата вшиты: correct_name подменяет название, category/note дописываются);
  - по строке на каждый текст аннотации (source_type='annotation').
Всё векторизуется моделью model_path (префикс "passage: " для e5-семейства).

Вызывается из дообучения (finetune) для новой версии модели. Версию 0 (текущие
прод-векторы) переносит scripts/db/migrate_from_files.py без пересчёта.
"""

import sys
from pathlib import Path

import numpy as np

# Корень импорта — src/. При запуске `python src/<пакет>/<файл>.py` Python кладёт
# в sys.path[0] папку самого скрипта: заменяем её на src, чтобы модули пакета
# не импортировались под двумя именами. `python -m <пакет>.<файл>` с PYTHONPATH=src
# и импорт из тестов работают без этого.
_SRC = Path(__file__).resolve().parents[1]
if sys.path and Path(sys.path[0] or ".").resolve() == Path(__file__).resolve().parent:
    sys.path[0] = str(_SRC)
elif str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from core import env_bootstrap  # noqa: F401  — .env до HF-библиотек

from sentence_transformers import SentenceTransformer
from storage.annotations_storage import build_search_text

BATCH_SIZE = 128


def uses_passage_prefix(model_path: str) -> bool:
    """Префикс "passage: " только для e5-семейства; BGE-M3 и др. современные
    модели префиксов не требуют (см. model card)."""
    return "e5" in str(model_path).lower()


def passage_text(text: str, model_path: str) -> str:
    return f"passage: {text}" if uses_passage_prefix(model_path) else text


def _conn_kw(conn) -> dict:
    """conn= передаётся только PostgreSQL-хранилищу (Memory его не принимает)."""
    return {} if conn is None else {"conn": conn}


def compute_vectors(storage, model_path: str) -> list[tuple[str, str, str, np.ndarray]]:
    """Строки (code, source_type, search_text, embedding) всех записей классификатора.

    Базовая строка — build_search_text с аннотациями; плюс строка на каждый
    (уникальный) текст аннотации. Ничего не пишет в хранилище.
    """
    entries = storage.classifier.load_entries()
    # Все аннотации одним запросом (без запроса на каждый код)
    by_code = storage.classifier.annotations_by_code()
    print(f"Построение векторов: {len(entries)} записей, "
          f"кодов с аннотациями: {len(by_code)}; модель {model_path}")

    rows: list[tuple[str, str, str]] = []
    for e in entries:
        # annotations_for отдаёт строки всех версий — один текст берём один раз
        anns = list(dict.fromkeys(by_code.get(e.code, [])))
        rows.append((e.code, "base", build_search_text(e.code, e.name, e.full_path, anns)))
        rows.extend((e.code, "annotation", text) for text in anns)

    model = SentenceTransformer(model_path)
    vectors = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        vectors.append(np.asarray(
            model.encode([passage_text(t, model_path) for _, _, t in batch],
                         normalize_embeddings=True),
            dtype=np.float32,
        ))
        done = min(start + BATCH_SIZE, len(rows))
        print(f"  [{done / len(rows) * 100:5.1f}%] {done}/{len(rows)}", end="\r")
    print()
    matrix = np.vstack(vectors) if vectors else np.zeros((0, 768), dtype=np.float32)
    return [(code, st, text, matrix[i]) for i, (code, st, text) in enumerate(rows)]


def build_database(storage, model_path: str, version: int, conn=None) -> int:
    """Пишет базовые и аннотационные векторы версии version. Возвращает число строк.

    Версия version уже должна быть в ml_model_registry (внешний ключ в БД).
    """
    rows = compute_vectors(storage, model_path)
    n = storage.classifier.add_vectors(version, rows, **_conn_kw(conn))
    print(f"Готово: {n} векторов для версии {version}")
    return n


def main(argv=None) -> int:
    """Ручная пересборка векторов зарегистрированной версии модели."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Построить векторы классификатора для версии модели (PostgreSQL)")
    parser.add_argument("--version", type=int, help="номер версии в ml_model_registry")
    parser.add_argument("--model-path", help="путь или имя модели эмбеддингов")
    args = parser.parse_args(argv)
    if args.version is None or not args.model_path:
        parser.print_usage()
        print(
            "Векторы классификатора хранятся в PostgreSQL (classifier_vectors).\n"
            "  Перенос текущей базы:        python scripts/db/migrate_from_files.py\n"
            "  Векторы новой версии модели строит дообучение (finetune).\n"
            "  Ручная пересборка:           python src/training/build_vectordb.py --version N --model-path P"
        )
        return 2

    from storage import get_storage

    storage = get_storage()
    refusal = _refusal(storage, args.version)
    if refusal:
        print(f"Отказ: {refusal}")
        return 2
    build_database(storage, args.model_path, args.version)
    return 0


def _refusal(storage, version: int):
    """Причина отказа ручной сборки (build_database только добавляет строки)."""
    if version == 0:
        return ("версия 0 — перенесённые прод-векторы (с адаптером); "
                "пересчёт испортил бы поиск")
    if storage.models.get(version) is None:
        return f"версии {version} нет в реестре моделей (ml_model_registry)"
    if storage.classifier.vectors_for_version(version):
        return (f"у версии {version} уже есть векторы; повторная сборка "
                f"продублировала бы строки")
    return None


if __name__ == "__main__":
    sys.exit(main())

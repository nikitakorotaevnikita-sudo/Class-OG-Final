"""Применяет схему БД (идемпотентно). Запуск из корня: python scripts/db/init_db.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import psycopg  # noqa: E402

from core import config  # noqa: E402
from storage import db  # noqa: E402


def main() -> None:
    with psycopg.connect(config.DATABASE_URL) as conn:
        with conn.transaction():
            db.apply_schema(conn)
    print("schema applied")


if __name__ == "__main__":
    main()

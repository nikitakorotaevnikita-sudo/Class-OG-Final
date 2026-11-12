"""Пул соединений закрывается при выходе процесса (CLI-скрипты не висят).

Без close() интерпретатор при завершении ждал потоки пула по 5 с на каждый
("couldn't stop thread pool-1-worker-N within 5.0 seconds") — миграция висела
~20 с после завершения работы.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from storage import db

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


class _FakePool:
    instances: list = []

    def __init__(self, *a, **kw):
        self.closed = False
        _FakePool.instances.append(self)

    def open(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def fake_pool(monkeypatch):
    _FakePool.instances = []
    registered = []
    monkeypatch.setattr(db, "ConnectionPool", _FakePool)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_atexit_registered", False)
    monkeypatch.setattr(db.atexit, "register", lambda fn: registered.append(fn))
    yield registered
    db._pool = None


def test_get_pool_registers_close_at_exit_once(fake_pool):
    pool = db.get_pool()
    db.close_pool()
    db.get_pool()  # пул пересоздан — регистрация не дублируется
    assert fake_pool == [db.close_pool]
    assert pool.closed is True


def test_close_pool_is_idempotent(fake_pool):
    db.get_pool()
    db.close_pool()
    db.close_pool()
    assert db._pool is None


def _run_script(code: str, url: str) -> tuple[subprocess.CompletedProcess, float]:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(SRC), "DATABASE_URL": url,
           "STORAGE_BACKEND": "postgres"}
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    return proc, time.monotonic() - started


@pytest.mark.db
def test_process_using_pool_exits_promptly():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан — тесты PostgreSQL пропущены")
    # register_vector не нужен (в public тестовой БД расширения может не быть)
    code = ("import config; config.DATABASE_URL = %r\n"
            "from storage import db\n"
            "db._configure = lambda conn: conn.commit()\n"
            "with db.transaction() as c:\n"
            "    print(c.execute('select 1').fetchone()[0])\n" % url)
    try:
        proc, elapsed = _run_script(code, url)
    except subprocess.TimeoutExpired:
        pytest.fail("процесс не завершился за 120 с")
    if proc.returncode != 0 and "OperationalError" in proc.stderr:
        pytest.skip(f"PostgreSQL недоступен: {proc.stderr[-200:]}")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1"
    assert "couldn't stop thread" not in proc.stderr
    assert elapsed < 15, f"выход занял {elapsed:.1f} с"

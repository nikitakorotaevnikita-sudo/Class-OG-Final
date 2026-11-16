"""scripts/check_db.py — адрес базы машины «только воркеры».

Главное: ни при каком вводе не печатать пароль и сам URL (libpq вставляет
введённую строку в текст ошибки), и записывать DATABASE_URL в .env так, чтобы
python-dotenv прочёл ровно то же значение (!, %, \\, @, :, кавычки).
"""

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_db.py"
SECRET = "S3cret"


def load():
    spec = importlib.util.spec_from_file_location("check_db_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(args, url=None, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONUTF8"] = "1"
    if url is not None:
        env["DATABASE_URL"] = url
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


MALFORMED = [
    f"postgresq://u:{SECRET}@h/db",                  # опечатка в схеме
    f"postgresql://u:{SECRET}%zz@127.0.0.1:1/db",    # неверная %-последовательность
    f"postgresql://u:{SECRET}@h:port{SECRET}/db",    # нечисловой порт
    f"host=h password={SECRET}",                     # не URL вовсе
    f"postgresql://u:{SECRET}@/db",                  # без хоста
]


@pytest.mark.parametrize("url", MALFORMED)
def test_check_never_prints_secret_or_url_for_malformed_input(url):
    proc = run(["check"], url)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert SECRET not in out and url not in out
    assert "DATABASE_URL" in out
    assert "Traceback" not in out


def test_check_unreachable_prints_host_port_only():
    port = closed_port()
    url = f"postgresql://u:{SECRET}@127.0.0.1:{port}/db"
    proc = run(["check"], url)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert f"127.0.0.1:{port}" in out
    assert SECRET not in out and url not in out
    assert out.isascii()


def test_check_auth_failure_reason(tmp_path):
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL не задан")
    mod = load()
    hp = mod.host_port(test_url).replace("localhost", "127.0.0.1")
    probe = socket.socket()
    probe.settimeout(1)
    try:
        probe.connect((hp.split(":")[0], int(hp.split(":")[1])))
    except OSError:
        pytest.skip("тестовый PostgreSQL недоступен")
    finally:
        probe.close()
    url = f"postgresql://classifier:{SECRET}@{hp}/classifier"
    proc = run(["check"], url)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "authentication failed" in out
    assert SECRET not in out


def test_check_ok_against_test_database():
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL не задан")
    proc = run(["check"], test_url)
    if proc.returncode == 1:
        pytest.skip("тестовый PostgreSQL недоступен")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_missing_url_is_exit_2():
    mod = load()
    assert mod.check("") == 2


# ── Запись в .env ────────────────────────────────────────────────────────────

ROUND_TRIP = [
    "postgresql://u:Pa!ss%25w@db.example:5434/classifier",
    "postgresql://u:a!b%21c%25@h:5432/db",
    "postgresql://u:back\\slash'quote\"x$y@h:5432/db",
]


@pytest.mark.parametrize("url", ROUND_TRIP)
def test_ask_writes_value_that_dotenv_reads_back_unchanged(tmp_path, url):
    from dotenv import dotenv_values

    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n# DATABASE_URL=commented\nDATABASE_URL=\nB=2\n", encoding="utf-8")
    proc = run(["ask", "--env-file", str(env_file), "--non-interactive"], url)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "Pa!ss" not in out and "a!b" not in out and "back" not in out
    for interpolate in (True, False):  # load_dotenv воркеров раскрывает ${...}
        assert dotenv_values(env_file, interpolate=interpolate)["DATABASE_URL"] == url
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "# DATABASE_URL=commented" in lines and "A=1" in lines and "B=2" in lines
    assert sum(1 for line in lines if line.startswith("DATABASE_URL=")) == 1


def test_ask_keeps_crlf_and_appends_when_missing(tmp_path):
    from dotenv import dotenv_values

    env_file = tmp_path / ".env"
    env_file.write_bytes(b"A=1\r\nB=2\r\n")
    url = "postgresql://u:x!y@h:5432/db"
    assert run(["ask", "--env-file", str(env_file), "--non-interactive"], url).returncode == 0
    raw = env_file.read_bytes()
    assert raw.count(b"\r\n") == 3 and b"\n" not in raw.replace(b"\r\n", b"")
    assert dotenv_values(env_file)["DATABASE_URL"] == url


@pytest.mark.parametrize("url", [f"postgresq://u:{SECRET}@h/db",
                                 f"postgresql://u:{SECRET}${{HOME}}@h:5432/db", ""])
def test_ask_rejects_bad_or_missing_url_without_printing_it(tmp_path, url):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n", encoding="utf-8")
    proc = run(["ask", "--env-file", str(env_file), "--non-interactive"], url)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert SECRET not in out
    assert "DATABASE_URL" not in env_file.read_text(encoding="utf-8")


def test_ask_non_interactive_keeps_current_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://u:k!p@h:5432/db\n", encoding="utf-8")
    proc = run(["ask", "--env-file", str(env_file), "--non-interactive"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "h:5432" in proc.stdout and "k!p" not in proc.stdout


def test_interactive_password_is_hidden_and_percent_encoded(tmp_path, monkeypatch, capsys):
    from psycopg import conninfo

    mod = load()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n", encoding="utf-8")
    password = "Pa!ss%w@:/ord"
    prompts = []
    rc = mod.run_ask(env_file, interactive=True,
                     input_fn=lambda p: prompts.append(p) or "postgresql://u@db.local:5432/c",
                     getpass_fn=lambda p: password)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert password not in out and "db.local:5432" in out
    url = mod.read_env_url(env_file)
    params = conninfo.conninfo_to_dict(url)
    assert params["password"] == password
    assert params["host"] == "db.local" and params["user"] == "u"


def test_interactive_enter_keeps_current(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://u:k!p@h:5432/db\n", encoding="utf-8")
    called = []
    rc = mod.run_ask(env_file, interactive=True, input_fn=lambda p: "",
                     getpass_fn=lambda p: called.append(p) or "")
    assert rc == 0 and not called
    assert mod.read_env_url(env_file) == "postgresql://u:k!p@h:5432/db"


def test_output_is_ascii_for_cmd():
    text = SCRIPT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "say(" in line and '"' in line:
            literal = line.split("say(", 1)[1]
            assert literal.isascii(), line

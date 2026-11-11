"""start.* local/migrate: адреса PostgreSQL/RabbitMQ и ожидание готовности API.

Без DATABASE_URL/RABBITMQ_URL config подключился бы к localhost:5432/5672 — на
машине с «родными» PostgreSQL/RabbitMQ это чужие серверы. Скрипты собирают URL
из учётных данных compose и портов, а при расхождении порта — останавливаются.
/health всегда отвечает 200, поэтому готовность — по "db":true и "broker":true.
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
START_SH = ROOT / "start.sh"
START_CMD = ROOT / "start.cmd"
STOP_CMD = ROOT / "stop.cmd"
BASH = shutil.which("bash")

CLEAN_ENV = ("DATABASE_URL", "RABBITMQ_URL", "POSTGRES_PORT", "RABBITMQ_PORT",
             "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
             "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS")


def run_start_fn(tmp_path: Path, env_text: str, body: str, extra_env=None):
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    unset = " ".join(CLEAN_ENV)
    script = textwrap.dedent(f"""
        unset {unset}
        source ./start.sh
        ENV_FILE='{env_file.as_posix()}'
        {body}
    """)
    import os

    env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
    env.update(extra_env or {})
    # Модули окружения выше снимаются ещё раз внутри скрипта; extra_env
    # выставляется после unset — через export в теле.
    return subprocess.run([BASH, "-c", script], cwd=str(ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


needs_bash = pytest.mark.skipif(BASH is None, reason="bash недоступен")


@needs_bash
def test_urls_built_from_compose_credentials_and_ports(tmp_path):
    proc = run_start_fn(tmp_path, (
        "POSTGRES_USER=u1\nPOSTGRES_PASSWORD=p1\nPOSTGRES_DB=d1\nPOSTGRES_PORT=5434\n"
        "RABBITMQ_DEFAULT_USER=u2\nRABBITMQ_DEFAULT_PASS=p2\nRABBITMQ_PORT=5673\n"),
        'resolve_service_urls; echo "DB=$DATABASE_URL"; echo "MQ=$RABBITMQ_URL"')
    assert proc.returncode == 0, proc.stderr
    assert "DB=postgresql://u1:p1@localhost:5434/d1" in proc.stdout
    assert "MQ=amqp://u2:p2@localhost:5673//" in proc.stdout
    # адреса печатаются, пароли — нет
    assert "localhost:5434" in proc.stdout and "localhost:5673" in proc.stdout
    printed = proc.stdout.replace("DB=postgresql://u1:p1@", "").replace("MQ=amqp://u2:p2@", "")
    assert "p1" not in printed and "p2" not in printed


@needs_bash
def test_urls_default_ports_when_nothing_configured(tmp_path):
    proc = run_start_fn(tmp_path, "",
                        'resolve_service_urls; echo "DB=$DATABASE_URL"; echo "MQ=$RABBITMQ_URL"')
    assert proc.returncode == 0, proc.stderr
    assert "DB=postgresql://classifier:classifier@localhost:5432/classifier" in proc.stdout
    assert "MQ=amqp://classifier:classifier@localhost:5672//" in proc.stdout


@needs_bash
def test_explicit_urls_with_matching_ports_are_kept(tmp_path):
    proc = run_start_fn(tmp_path, (
        "DATABASE_URL=postgresql://a:b@db.local:5434/x\nPOSTGRES_PORT=5434\n"
        "RABBITMQ_URL=amqp://a:b@mq.local:5673//\nRABBITMQ_PORT=5673\n"),
        'resolve_service_urls; echo "DB=$DATABASE_URL"; echo "MQ=$RABBITMQ_URL"')
    assert proc.returncode == 0, proc.stderr
    assert "DB=postgresql://a:b@db.local:5434/x" in proc.stdout
    assert "MQ=amqp://a:b@mq.local:5673//" in proc.stdout
    assert "db.local:5434" in proc.stdout and "mq.local:5673" in proc.stdout


@needs_bash
@pytest.mark.parametrize("env_text,needle", [
    ("DATABASE_URL=postgresql://a:b@localhost:5432/x\nPOSTGRES_PORT=5434\n", "POSTGRES_PORT=5434"),
    ("RABBITMQ_URL=amqp://a:b@localhost:5672//\nRABBITMQ_PORT=5673\n", "RABBITMQ_PORT=5673"),
])
def test_port_mismatch_fails_fast(tmp_path, env_text, needle):
    proc = run_start_fn(tmp_path, env_text, "resolve_service_urls; echo REACHED")
    assert proc.returncode == 1
    assert "REACHED" not in proc.stdout
    assert needle in proc.stderr


@needs_bash
def test_environment_overrides_env_file(tmp_path):
    proc = run_start_fn(tmp_path, "POSTGRES_PORT=5434\n",
                        'export POSTGRES_PORT=5440; resolve_service_urls; echo "DB=$DATABASE_URL"')
    assert proc.returncode == 0, proc.stderr
    assert "localhost:5440/" in proc.stdout


def test_start_sh_resolves_urls_in_local_and_migrate():
    text = START_SH.read_text(encoding="utf-8")
    run_migrate = text[text.index("run_migrate() {"):]
    run_migrate = run_migrate[:run_migrate.index("\n}\n")]
    assert "resolve_service_urls" in run_migrate  # migrate и local идут через run_migrate
    start_local = text[text.index("start_local() {"):]
    start_local = start_local[:start_local.index("\n}\n")]
    assert start_local.index("resolve_service_urls") < start_local.index("docker compose up")


# ── start.cmd (строковые проверки: cmd в тестах не запускается) ─────────────

def test_start_cmd_resolves_urls_before_migration_and_local_start():
    text = START_CMD.read_text(encoding="utf-8")
    assert ":resolve_urls" in text
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_PORT",
                "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS", "RABBITMQ_PORT"):
        assert key in text, key
    local = text[text.index("\n:local\n"):text.index("\n:migrate_only\n")]
    assert local.index("call :resolve_urls") < local.index("docker compose up")
    run_migrate = text[text.index("\n:run_migrate\n"):]
    run_migrate = run_migrate[:run_migrate.index("exit /b 0")]
    assert "call :resolve_urls" in run_migrate
    assert "postgresql://" in text and "amqp://" in text
    assert "ERROR: DATABASE_URL points to port" in text
    assert "ERROR: RABBITMQ_URL points to port" in text


@pytest.mark.parametrize("script", [START_SH, START_CMD])
def test_wait_health_requires_db_and_broker(script):
    text = script.read_text(encoding="utf-8")
    body = text[text.index("wait_health"):]
    if script is START_SH:
        assert '"db":true' in body and '"broker":true' in body
    else:
        assert "db.:true" in body and "broker.:true" in body


# ── stop.cmd local: процессы по командной строке, а не по заголовку окна ────

def test_stop_cmd_kills_project_python_by_command_line():
    text = STOP_CMD.read_text(encoding="utf-8")
    local = text[text.index("\n:local\n"):text.index("\n:help\n")]
    assert "Win32_Process" in local
    assert "queueing\\.celery_app|src\\.api_server:app|uvicorn" in local
    assert "taskkill /F /T /PID" in local
    assert "%~dp0" in text and "PROJ" in local
    # заголовок окна — запасной шаг для оставшихся окон cmd
    assert local.index("Win32_Process") < local.index("WINDOWTITLE eq classifier-*")

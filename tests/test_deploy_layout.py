"""Раскладка развёртывания: compose-стек, скрипты start/stop, .env.example.

Сервис разрезан на postgres + rabbitmq + migrate + api + воркеры. Здесь
фиксируется то, что легко сломать правкой «на глазок»: состав сервисов,
монтирование init-SQL, отсутствие ПДн в api, запрет `down -v` (том БД хранит
тексты обращений) и ASCII в исполняемых строках .cmd (консоль CP1251).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = ROOT / "docker-compose.yml"
START_SH = ROOT / "start.sh"
STOP_SH = ROOT / "stop.sh"
START_CMD = ROOT / "start.cmd"
STOP_CMD = ROOT / "stop.cmd"
SH_SCRIPTS = [START_SH, STOP_SH]
CMD_SCRIPTS = [START_CMD, STOP_CMD]
ALL_SCRIPTS = SH_SCRIPTS + CMD_SCRIPTS

EXPECTED_SERVICES = {"postgres", "rabbitmq", "migrate", "api",
                     "worker-classify", "worker-training"}
APP_SERVICES = ["migrate", "api", "worker-classify", "worker-training"]

BASH = shutil.which("bash")
DOCKER = shutil.which("docker")


def compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def services() -> dict:
    return compose()["services"]


def volumes_of(name: str) -> list[str]:
    return [str(v).replace(" ", "") for v in services()[name].get("volumes", [])]


def env_of(name: str) -> dict:
    env = services()[name].get("environment", {})
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return env


def command_of(name: str) -> str:
    cmd = services()[name].get("command", "")
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


# ── Состав стека ────────────────────────────────────────────────────────────

def test_compose_services():
    svc = services()
    assert set(svc) == EXPECTED_SERVICES
    assert svc["worker-training"].get("profiles") == ["training"]
    for script in ALL_SCRIPTS:
        text = script.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r"down\s+(-\S+\s+)*-v\b", text), (
            f"{script.name}: `down -v` удаляет том pgdata с ПДн"
        )
        assert "--volumes" not in text, f"{script.name}: --volumes удаляет том с ПДн"


def test_compose_mounts_init_sql():
    pg = services()["postgres"]
    assert pg["image"] == "pgvector/pgvector:pg16"
    vols = volumes_of("postgres")
    assert "./deploy/db/init:/docker-entrypoint-initdb.d:ro" in vols
    assert "pgdata:/var/lib/postgresql/data" in vols
    assert "pgdata" in compose().get("volumes", {})
    assert "pg_isready" in str(pg["healthcheck"]["test"])
    for sql in ("001_extensions.sql", "002_schema.sql"):
        assert (ROOT / "deploy" / "db" / "init" / sql).is_file()


def test_rabbitmq_service():
    rmq = services()["rabbitmq"]
    assert rmq["image"] == "rabbitmq:3.13-management"
    assert "rabbitmq-diagnostics -q ping" in " ".join(map(str, rmq["healthcheck"]["test"]))
    ports = " ".join(map(str, rmq.get("ports", [])))
    assert "5672" in ports and "15672" in ports


def test_rabbitmq_consumer_timeout_covers_long_tasks():
    """acks_late + стандартные 30 мин consumer_timeout = повторная доставка дообучения."""
    conf = ROOT / "deploy" / "rabbitmq" / "rabbitmq.conf"
    lines = [ln.strip() for ln in conf.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert "consumer_timeout = 86400000" in lines
    assert ("./deploy/rabbitmq/rabbitmq.conf:/etc/rabbitmq/conf.d/20-classifier.conf:ro"
            in volumes_of("rabbitmq"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "consumer_timeout = 86400000" in readme


def test_env_example_disables_broker_heartbeat():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^BROKER_HEARTBEAT_SEC=0\s*$", text, re.M)


def test_migrate_runs_once_after_postgres():
    mig = services()["migrate"]
    assert mig["build"] == "."
    assert mig["restart"] == "no"
    cmd = command_of("migrate")
    assert "scripts/db/init_db.py" in cmd and "scripts/db/migrate_from_files.py" in cmd
    assert cmd.index("init_db.py") < cmd.index("migrate_from_files.py")
    assert mig["depends_on"]["postgres"]["condition"] == "service_healthy"
    vols = volumes_of("migrate")
    assert "./data:/app/data:ro" in vols
    assert "./models:/app/models" in vols


def test_api_waits_for_migration_and_has_no_pii_or_model_mounts():
    api = services()["api"]
    assert api["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert api["depends_on"]["rabbitmq"]["condition"] == "service_healthy"
    assert "${HOST_PORT:-8010}:8005" in [str(p) for p in api["ports"]]
    vols = " ".join(volumes_of("api"))
    for forbidden in (".jsonl", "huggingface", "./models", "./data"):
        assert forbidden not in vols, f"api не должен монтировать {forbidden}"


def test_classify_worker_command_and_mounts():
    cmd = command_of("worker-classify")
    assert "celery -A queueing.celery_app worker" in cmd
    assert "-Q classify_sync,classify" in cmd
    assert "${CLASSIFY_CONCURRENCY:-2}" in cmd
    vols = " ".join(volumes_of("worker-classify"))
    assert "/root/.cache/huggingface" in vols
    assert "./models:/app/models" in vols
    assert "allowed_codes_top69.json" in vols


def test_training_worker_is_single_and_optional():
    cmd = command_of("worker-training")
    assert "-Q training" in cmd and "-c 1" in cmd
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "# deploy:" in raw or "#   resources:" in raw or "# GPU" in raw, (
        "закомментированный GPU-блок для training-воркера"
    )


@pytest.mark.parametrize("name", APP_SERVICES)
def test_app_services_share_env_and_point_at_compose_hosts(name):
    svc = services()[name]
    env_file = svc.get("env_file")
    assert env_file == ".env" or env_file == [".env"]
    assert "./.env:/app/.env" in volumes_of(name)
    env = env_of(name)
    assert "@postgres:5432/" in env["DATABASE_URL"]
    assert "@rabbitmq:5672/" in env["RABBITMQ_URL"]
    assert env["PYTHONPATH"] == "/app/src"
    assert env["STORAGE_BACKEND"] == "postgres"


@pytest.mark.parametrize("name", ["api", "worker-classify", "worker-training"])
def test_app_services_see_the_host(name):
    """RX / LLM на той же машине: api ходит в RX, воркеры — в LLM."""
    assert "host.docker.internal:host-gateway" in services()[name].get("extra_hosts", [])


def test_infra_ports_bound_to_loopback_by_default():
    """В БД ПДн, пароли по умолчанию classifier — наружу только осознанно."""
    pg = [str(p) for p in services()["postgres"]["ports"]]
    assert pg == ["${POSTGRES_BIND:-127.0.0.1}:${POSTGRES_PORT:-5432}:5432"]
    rmq = [str(p) for p in services()["rabbitmq"]["ports"]]
    assert rmq == ["${RABBITMQ_BIND:-127.0.0.1}:${RABBITMQ_PORT:-5672}:5672",
                   "${RABBITMQ_BIND:-127.0.0.1}:${RABBITMQ_MGMT_PORT:-15672}:15672"]


# ── Образ ───────────────────────────────────────────────────────────────────

def test_dockerfile_copies_scripts_and_deploy():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^COPY\s+scripts/\s+\./scripts/", text, re.MULTILINE)
    assert re.search(r"^COPY\s+deploy/\s+\./deploy/", text, re.MULTILINE)
    assert "ARG PYTHON_VERSION" in text
    assert 'CMD ["uvicorn", "src.api_server:app"' in text


# ── Скрипты запуска ─────────────────────────────────────────────────────────

def test_cmd_files_ascii_executable_lines():
    for script in CMD_SCRIPTS:
        for no, line in enumerate(script.read_bytes().split(b"\n"), 1):
            stripped = line.strip().upper()
            if stripped.startswith(b"REM") or stripped.startswith(b"::"):
                continue
            assert line.isascii(), f"{script.name}:{no}: не-ASCII в исполняемой строке"


def test_cmd_files_keep_crlf():
    for script in CMD_SCRIPTS:
        raw = script.read_bytes()
        assert b"\r\n" in raw
        assert re.search(rb"[^\r]\n", raw) is None, f"{script.name}: строки без CR"


def test_sh_files_have_lf_and_strict_mode():
    for script in SH_SCRIPTS:
        raw = script.read_bytes()
        assert b"\r\n" not in raw, f"{script.name} сохранён с CRLF"
        assert "set -euo pipefail" in raw.decode("utf-8")


def test_start_sh_has_modes():
    text = START_SH.read_text(encoding="utf-8")
    for mode in ("docker)", "local)", "migrate)"):
        assert mode in text, f"start.sh: нет режима {mode}"
    assert "--training" in text
    assert "run/" in text and ".pid" in text


def test_start_cmd_has_modes():
    text = START_CMD.read_text(encoding="utf-8")
    for mode in ("docker", "local", "migrate", "--training"):
        assert mode in text
    assert "--pool=solo" in text
    assert "CLASSIFY_WORKERS" in text
    assert 'start "classifier-' in text


def test_start_scripts_wait_for_health():
    for script in (START_SH, START_CMD):
        text = script.read_text(encoding="utf-8")
        assert "/health" in text, f"{script.name}: нет ожидания /health"


def test_stop_scripts_use_plain_down():
    for script in (STOP_SH, STOP_CMD):
        text = script.read_text(encoding="utf-8")
        downs = re.findall(r"^\s*docker compose\b.*\bdown\b.*$", text, re.MULTILINE)
        assert downs, f"{script.name}: нет docker compose ... down"
        assert all(not re.search(r"\s-v\b|--volumes", line) for line in downs)
    assert "taskkill" in STOP_CMD.read_text(encoding="utf-8")
    assert ".pid" in STOP_SH.read_text(encoding="utf-8")


@pytest.mark.skipif(BASH is None, reason="bash недоступен")
@pytest.mark.parametrize("script", SH_SCRIPTS, ids=lambda p: p.name)
def test_sh_syntax(script):
    proc = subprocess.run([BASH, "-n", script.name], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr


# ── Конфигурация и Git ──────────────────────────────────────────────────────

def test_run_dir_is_ignored():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "run/" in lines


@pytest.mark.parametrize("key", [
    "STORAGE_BACKEND", "DATABASE_URL", "DB_POOL_MAX", "RABBITMQ_URL",
    "CLASSIFY_CONCURRENCY", "CLASSIFY_WORKERS", "SYNC_WAIT_TIMEOUT_SEC",
    "JOB_MAX_ATTEMPTS", "ENABLE_ANNOTATION_VECTORS", "WEBHOOK_ALLOWED_HOSTS",
    "WEBHOOK_SECRET", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "POSTGRES_PORT", "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS",
    "POSTGRES_BIND", "RABBITMQ_BIND", "RABBITMQ_PORT", "RABBITMQ_MGMT_PORT",
])
def test_env_example_declares_key_once(key):
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert len(re.findall(rf"^{key}=", text, re.MULTILINE)) == 1, key


def test_docker_compose_config_is_valid():
    if DOCKER is None:
        pytest.skip("docker CLI недоступен")
    if not (ROOT / ".env").exists():
        pytest.skip("нет .env — compose требует его для env_file")
    proc = subprocess.run([DOCKER, "compose", "config", "-q"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 and "is not a docker command" in proc.stderr:
        pytest.skip("нет плагина docker compose")
    assert proc.returncode == 0, proc.stderr
    proc = subprocess.run([DOCKER, "compose", "--profile", "training", "config", "-q"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr

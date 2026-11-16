"""start.* / stop.*: режимы запуска, адрес PostgreSQL и ожидание готовности API.

Режимы: all (по умолчанию; local — прежнее имя), api (без воркеров), worker (только
воркеры, база на другой машине), docker [--no-workers], migrate; флаг --training.

Без DATABASE_URL config подключился бы к localhost:5432 — на машине с «родным»
PostgreSQL это чужой сервер. Скрипты собирают URL из учётных данных compose и
порта, а при расхождении порта — останавливаются. /health всегда отвечает 200,
поэтому готовность — по "db":true (и "agent_ready":true, когда воркеры поднимаются).
"""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
START_SH = ROOT / "start.sh"
START_CMD = ROOT / "start.cmd"
STOP_SH = ROOT / "stop.sh"
STOP_CMD = ROOT / "stop.cmd"
BASH = shutil.which("bash")

CLEAN_ENV = ("DATABASE_URL", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD",
             "POSTGRES_DB", "CLASSIFY_WORKERS", "API_PORT", "HOST_PORT")


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
    env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
    env.update(extra_env or {})
    # Модули окружения выше снимаются ещё раз внутри скрипта; extra_env
    # выставляется после unset — через export в теле.
    return subprocess.run([BASH, "-c", script], cwd=str(ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


needs_bash = pytest.mark.skipif(BASH is None, reason="bash недоступен")


@needs_bash
def test_url_built_from_compose_credentials_and_port(tmp_path):
    proc = run_start_fn(tmp_path, (
        "POSTGRES_USER=u1\nPOSTGRES_PASSWORD=p1\nPOSTGRES_DB=d1\nPOSTGRES_PORT=5434\n"),
        'resolve_service_urls; echo "DB=$DATABASE_URL"')
    assert proc.returncode == 0, proc.stderr
    assert "DB=postgresql://u1:p1@localhost:5434/d1" in proc.stdout
    # адрес печатается, пароль — нет
    assert "localhost:5434" in proc.stdout
    printed = proc.stdout.replace("DB=postgresql://u1:p1@", "")
    assert "p1" not in printed


@needs_bash
def test_url_default_port_when_nothing_configured(tmp_path):
    proc = run_start_fn(tmp_path, "", 'resolve_service_urls; echo "DB=$DATABASE_URL"')
    assert proc.returncode == 0, proc.stderr
    assert "DB=postgresql://classifier:classifier@localhost:5432/classifier" in proc.stdout


@needs_bash
def test_explicit_url_with_matching_port_is_kept(tmp_path):
    proc = run_start_fn(tmp_path, (
        "DATABASE_URL=postgresql://a:b@db.local:5434/x\nPOSTGRES_PORT=5434\n"),
        'resolve_service_urls; echo "DB=$DATABASE_URL"')
    assert proc.returncode == 0, proc.stderr
    assert "DB=postgresql://a:b@db.local:5434/x" in proc.stdout
    assert "db.local:5434" in proc.stdout


@needs_bash
def test_port_mismatch_fails_fast(tmp_path):
    proc = run_start_fn(tmp_path,
                        "DATABASE_URL=postgresql://a:b@localhost:5432/x\nPOSTGRES_PORT=5434\n",
                        "resolve_service_urls; echo REACHED")
    assert proc.returncode == 1
    assert "REACHED" not in proc.stdout
    assert "POSTGRES_PORT=5434" in proc.stderr


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

def cmd_section(text: str, label: str, end_label: str) -> str:
    return text[text.index(f"\n:{label}\n"):text.index(f"\n:{end_label}\n")]


def test_start_cmd_resolves_urls_before_migration_and_local_start():
    text = START_CMD.read_text(encoding="utf-8")
    assert ":resolve_urls" in text
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_PORT"):
        assert key in text, key
    local = cmd_section(text, "local", "worker")
    assert local.index("call :resolve_urls") < local.index("docker compose up")
    run_migrate = text[text.index("\n:run_migrate\n"):]
    run_migrate = run_migrate[:run_migrate.index("exit /b 0")]
    assert "call :resolve_urls" in run_migrate
    assert "postgresql://" in text
    assert "ERROR: DATABASE_URL points to port" in text


# ── Режимы ──────────────────────────────────────────────────────────────────

def test_start_sh_knows_all_modes_and_local_is_all():
    text = START_SH.read_text(encoding="utf-8")
    assert 'MODE="all"' in text  # по умолчанию — всё
    parse = text[text.index("while [ $# -gt 0 ]"):text.index("\ndone\n")]
    for mode in ("all", "api", "worker", "docker", "migrate"):
        assert mode in parse, mode
    assert "local) MODE=all" in parse
    assert "--no-workers" in parse and "--training" in parse and "--port" in parse
    dispatch = text[text.rindex('case "$MODE" in'):]
    for branch in ("all)", "api)", "worker)", "docker)", "migrate)"):
        assert branch in dispatch, branch


def test_start_cmd_knows_all_modes_and_local_is_all():
    text = START_CMD.read_text(encoding="utf-8")
    assert 'set "MODE=all"' in text  # по умолчанию — всё
    for mode in ("all", "api", "worker", "docker", "migrate"):
        assert f'if /i "%~1"=="{mode}"' in text, mode
    assert 'if /i "%~1"=="local"      ( set "MODE=all"' in text
    assert 'if /i "%~1"=="--no-workers"' in text
    for mode, label in (("all", "local"), ("api", "local"), ("worker", "worker"),
                        ("docker", "docker"), ("migrate", "migrate_only")):
        assert f'if /i "%MODE%"=="{mode}"' in text and f"goto :{label}" in text, mode


def test_start_cmd_api_mode_starts_no_workers():
    local = cmd_section(START_CMD.read_text(encoding="utf-8"), "local", "worker")
    assert "call :start_workers" in local
    # воркеры только в режиме all
    guard = local[:local.index("call :start_workers")].rsplit("\n", 3)[-3:]
    assert any('"%MODE%"=="all"' in line for line in guard), guard
    assert "worker.run" not in local
    # api ждёт только БД, all — ещё и воркеры
    assert "call :wait_health !PORT! !NEED_AGENT!" in local
    assert 'set "NEED_AGENT=0"' in local and 'set "NEED_AGENT=1"' in local


def test_start_cmd_worker_mode_needs_database_url_and_no_docker():
    text = START_CMD.read_text(encoding="utf-8")
    worker = cmd_section(text, "worker", "migrate_only")
    assert "docker" not in worker.lower()
    assert "run_migrate" not in worker and "init_db" not in worker
    assert "uvicorn" not in worker and "resolve_urls" not in worker
    assert "DATABASE_URL" in worker
    assert "ERROR: DATABASE_URL is not set" in worker
    assert "call :check_db" in worker and "call :start_workers" in worker
    assert worker.index("call :check_db") < worker.index("call :start_workers")
    check = text[text.index("\n:check_db\n"):]
    check = check[:check.index("\nexit /b 0\n")]
    assert "connect_timeout=5" in check
    assert "!DB_HOST!:!DB_PORT!" in check  # хост:порт, не URL с паролем
    assert "%DATABASE_URL%" not in check and "!DATABASE_URL!" not in check


def test_start_cmd_workers_run_the_queue_module_in_titled_windows():
    text = START_CMD.read_text(encoding="utf-8")
    workers = text[text.index("\n:start_workers\n"):]
    workers = workers[:workers.index("\nexit /b 0\n")]
    assert 'start "classifier-worker-%%i"' in workers
    assert "-m worker.run --queues classify_sync,classify" in workers
    assert 'start "classifier-worker-training"' in workers
    assert "-m worker.run --queues training" in workers
    assert "CLASSIFY_WORKERS" in workers
    assert "PYTHONPATH" in workers


def test_start_cmd_docker_no_workers_starts_only_postgres_migrate_api():
    docker = cmd_section(START_CMD.read_text(encoding="utf-8"), "docker", "local")
    assert "docker compose up -d --build postgres migrate api" in docker
    assert "docker compose %PROFILE% up -d --build" in docker
    assert "call :wait_health !PORT! !NEED_AGENT!" in docker


@pytest.mark.parametrize("script", [START_SH, START_CMD])
def test_wait_health_requires_db_and_optionally_agent(script):
    text = script.read_text(encoding="utf-8")
    body = text[text.index("wait_health"):]
    if script is START_SH:
        assert '"db":true' in body and '"agent_ready":true' in body
        assert "jobs" in body or "задачи" in body  # предупреждение «воркеров нет»
    else:
        assert "db.:true" in body and "agent_ready.:true" in body
        assert "no workers are running" in body
    assert "broker" not in text


@pytest.mark.parametrize("script", [START_SH, START_CMD, STOP_SH, STOP_CMD])
def test_scripts_do_not_mention_the_old_broker(script):
    text = script.read_text(encoding="utf-8").lower()
    for word in ("rabbit", "celery", "amqp", "broker", "acks_late"):
        assert word not in text, word


# ── stop.cmd: процессы по командной строке, а не по заголовку окна ──────────

def test_stop_cmd_modes():
    text = STOP_CMD.read_text(encoding="utf-8")
    assert 'if "%MODE%"=="" set "MODE=all"' in text
    for mode in ("all", "local", "api", "worker", "docker"):
        assert f'if /i "%MODE%"=="{mode}"' in text, mode


def stop_pattern(section: str) -> str:
    line = next(s for s in section.splitlines() if s.startswith('set "PAT='))
    return line[len('set "PAT='):-1]


def test_stop_cmd_kills_project_python_by_command_line():
    text = STOP_CMD.read_text(encoding="utf-8")
    procs = cmd_section(text, "stop_procs", "local")
    assert "Win32_Process" in procs and "$env:PAT" in procs
    assert "taskkill /F /T /PID" in procs
    assert "StartsWith($root" in procs  # только процессы ЭТОЙ папки проекта
    assert "%~dp0" in text and "$env:PROJ" in procs
    local = cmd_section(text, "local", "worker")
    pattern = stop_pattern(local)
    assert "worker\\.run" in pattern and "api\\.api_server:app" in pattern
    # заголовок окна — запасной шаг для оставшихся окон cmd
    assert local.index("call :stop_procs") < local.index("WINDOWTITLE eq classifier-*")
    # остановка контейнеров проекта: stop (не down), том не трогается
    assert "docker compose --profile training stop" in local


def test_stop_cmd_worker_mode_touches_only_workers():
    text = STOP_CMD.read_text(encoding="utf-8")
    worker = cmd_section(text, "worker", "help")
    pattern = stop_pattern(worker)
    assert "worker\\.run" in pattern
    assert "api_server" not in pattern and "uvicorn" not in pattern
    assert "call :stop_procs" in worker
    assert "docker" not in worker.lower()
    # без запасного шага по заголовку: заголовок не говорит, из какой папки окно
    assert "WINDOWTITLE" not in worker


# ── start.sh: режимы на заглушках (docker, venv python, curl) ───────────────

def start_sandbox(tmp_path: Path, health: str, env_text: str = ""):
    """Копия start.sh/stop.sh + install_common.sh в tmp: run/*.pid пишутся туда,
    а не в настоящий проект. docker, curl и venv/bin/python — заглушки."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "install_common.sh", tmp_path / "scripts")
    for name in ("start.sh", "stop.sh"):
        shutil.copy(ROOT / name, tmp_path / name)
    calls = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    log = f'echo "$(basename "$0") $*" >> "{calls.as_posix()}"\n'
    stubs = {
        tmp_path / "venv" / "bin" / "python": log,
        bindir / "docker": log,
        bindir / "curl": log + f"printf '%s' '{health}'\n",
    }
    for path, body in stubs.items():
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    return calls


def run_sandbox(tmp_path: Path, args: str, extra: str = ""):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
    # sleep 1: фоновые заглушки (nohup) успевают дописать calls.log
    script = f'export PATH="$PWD/bin:$PATH"; {extra} bash ./start.sh {args}; rc=$?; sleep 1; exit $rc'
    return subprocess.run([BASH, "-c", script], cwd=str(tmp_path), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


def read_calls(calls: Path) -> str:
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


@needs_bash
def test_start_sh_api_mode_has_no_workers_and_waits_only_for_db(tmp_path):
    calls = start_sandbox(tmp_path, '{"status":"ok","agent_ready":false,"db":true}',
                          "API_PORT=18999\n")
    proc = run_sandbox(tmp_path, "api")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    log = read_calls(calls)
    assert "docker compose up -d --wait postgres" in log
    assert "init_db.py" in log and "migrate_from_files.py" in log
    assert "uvicorn api.api_server:app" in log
    assert "worker.run" not in log
    assert "воркеров нет" in out  # предупреждение: задачи копятся в очереди
    assert (tmp_path / "run" / "api.pid").exists()
    assert not list((tmp_path / "run").glob("worker-*.pid"))


@needs_bash
def test_start_sh_all_mode_starts_classify_workers_and_training(tmp_path):
    calls = start_sandbox(tmp_path, '{"status":"ok","agent_ready":true,"db":true}',
                          "API_PORT=18998\nCLASSIFY_WORKERS=3\n")
    proc = run_sandbox(tmp_path, "local --training")  # local == all
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    log = read_calls(calls)
    assert log.count("-m worker.run --queues classify_sync,classify") == 3, log
    assert log.count("-m worker.run --queues training") == 1, log
    for n in (1, 2, 3):
        assert (tmp_path / "run" / f"worker-classify-{n}.pid").exists()
    assert (tmp_path / "run" / "worker-training.pid").exists()


@needs_bash
def test_start_sh_worker_mode_needs_database_url(tmp_path):
    calls = start_sandbox(tmp_path, "{}", "CLASSIFY_WORKERS=2\n")
    proc = run_sandbox(tmp_path, "worker")
    assert proc.returncode != 0
    assert "DATABASE_URL" in proc.stderr
    assert "worker.run" not in read_calls(calls)


@needs_bash
def test_start_sh_worker_mode_starts_only_workers(tmp_path):
    calls = start_sandbox(tmp_path, "{}",
                          "DATABASE_URL=postgresql://u:s3cret@db.example:5434/c\n"
                          "CLASSIFY_WORKERS=2\nPOSTGRES_PORT=5999\n")
    proc = run_sandbox(tmp_path, "worker --training")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    log = read_calls(calls)
    assert "docker" not in log
    assert "init_db.py" not in log and "migrate_from_files.py" not in log
    assert "uvicorn" not in log and "curl" not in log
    assert "connect_timeout=5" in log  # проверка, что база отвечает
    assert log.count("-m worker.run --queues classify_sync,classify") == 2, log
    assert log.count("-m worker.run --queues training") == 1, log
    # адрес печатается, пароль — нет; порт POSTGRES_PORT не сверяется
    assert "db.example:5434" in out
    assert "s3cret" not in out


@needs_bash
def test_start_sh_worker_mode_stops_when_database_does_not_answer(tmp_path):
    calls = start_sandbox(tmp_path, "{}", "DATABASE_URL=postgresql://u:s3cret@db.example:5434/c\n")
    py = tmp_path / "venv" / "bin" / "python"
    py.write_text(py.read_text(encoding="utf-8") + "exit 1\n", encoding="utf-8", newline="\n")
    proc = run_sandbox(tmp_path, "worker")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "db.example:5434" in out and "s3cret" not in out
    assert "worker.run" not in read_calls(calls)


@needs_bash
def test_start_sh_docker_no_workers(tmp_path):
    calls = start_sandbox(tmp_path, '{"status":"ok","agent_ready":false,"db":true}',
                          "HOST_PORT=18997\n")
    proc = run_sandbox(tmp_path, "docker --no-workers")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "docker compose up -d --build postgres migrate api" in read_calls(calls)
    assert "воркеров нет" in out


@needs_bash
def test_stop_sh_worker_stops_only_workers(tmp_path):
    calls = start_sandbox(tmp_path, "{}")
    run = tmp_path / "run"
    run.mkdir()
    # Маркер собирается из частей: пока дочерний процесс не сделал exec, его
    # командная строка — текст скрипта, и цельный маркер дал бы ложное «наш».
    script = textwrap.dedent("""
        export PATH="$PWD/bin:$PATH"
        M1=worker; M2=api_server
        # trap + wait: TERM завершает процесс сразу; вывод в /dev/null — осиротевший
        # sleep не держит каналы subprocess открытыми.
        FAKE='trap "exit 0" TERM; sleep 60 & wait'
        bash -c "$FAKE" "$M1.run" >/dev/null 2>&1 & echo $! > run/worker-classify-1.pid
        bash -c "$FAKE" "$M2:app" >/dev/null 2>&1 & echo $! > run/api.pid
        sleep 1
        bash ./stop.sh worker
        kill -0 "$(cat run/api.pid)" 2>/dev/null && echo "API=alive" || echo "API=dead"
        [ -f run/worker-classify-1.pid ] && echo "WPID=left" || echo "WPID=gone"
        kill "$(cat run/api.pid)" 2>/dev/null
        exit 0
    """)
    proc = subprocess.run([BASH, "-c", script], cwd=str(tmp_path), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    out = proc.stdout + proc.stderr
    assert "API=alive" in proc.stdout, out
    assert "WPID=gone" in proc.stdout, out
    assert "docker" not in read_calls(calls)


def test_stop_sh_modes():
    text = STOP_SH.read_text(encoding="utf-8")
    assert 'MODE="${1:-all}"' in text
    dispatch = text[text.rindex('case "$MODE" in'):]
    assert "all|local|api) stop_local" in dispatch
    assert "worker) stop_workers" in dispatch
    assert "docker) stop_docker" in dispatch
    stop_local = text[text.index("stop_local() {"):]
    stop_local = stop_local[:stop_local.index("\n}\n")]
    assert "docker compose --profile training stop" in stop_local


# ── .gitattributes: пакетные файлы всегда с CRLF ────────────────────────────

def test_batch_files_checked_out_with_crlf_regardless_of_autocrlf():
    """eol=crlf в .gitattributes: клон с core.autocrlf=false и zip-архив GitHub
    тоже получают CRLF (cmd.exe с LF ошибается в метках и goto)."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git недоступен")
    files = ["start.cmd", "stop.cmd", "launch.bat", "install.bat",
             "install_offline.bat", "scripts/setup_infra.cmd"]
    out = subprocess.run([git, "check-attr", "text", "eol", "--", *files], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    for name in files:
        assert f"{name}: eol: crlf" in out, out
        assert f"{name}: text: set" in out, out


@pytest.mark.parametrize("script", ["start.cmd", "start.sh"])
def test_start_scripts_check_that_docker_engine_responds(script):
    """Зависший движок Docker Desktop: понятная ошибка до docker compose up."""
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "docker info" in text
    assert "does not respond" in text or "не отвечает" in text

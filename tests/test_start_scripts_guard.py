"""start.* / stop.*: режимы запуска, адрес PostgreSQL и ожидание готовности API.

Режимы: all (по умолчанию; local — прежнее имя), api (без воркеров), worker (только
воркеры, база на другой машине), docker [--no-workers], migrate; флаг --training.

Адрес PostgreSQL скрипты не собирают и не экспортируют: его строят сами процессы
Python (core.config, из POSTGRES_* с %-кодированием), а host:port и сверку порта
с POSTGRES_PORT печатает scripts/check_db.py resolve (расхождение — остановка). /health всегда отвечает 200,
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


needs_bash = pytest.mark.skipif(BASH is None, reason="bash недоступен")


# Адрес PostgreSQL главного сервера (all/api/migrate) скрипты не собирают и не
# экспортируют: пароль с ! @ : / # % или пробелом cmd/shell испортили бы. Его
# строят сами процессы Python (core.config → service_urls, с %-кодированием), а
# host:port и сверку с POSTGRES_PORT печатает scripts/check_db.py resolve.

def test_start_sh_resolve_delegates_to_check_db():
    text = START_SH.read_text(encoding="utf-8")
    body = text[text.index("resolve_service_urls() {"):]
    body = body[:body.index("\n}\n")]
    assert '"$VENV_PY" scripts/check_db.py resolve' in body
    for access in SH_URL_ACCESS + ("POSTGRES_PASSWORD", "POSTGRES_USER", "postgresql://"):
        assert access not in body, access
    for access in ("export DATABASE_URL", "POSTGRES_PASSWORD"):
        assert access not in text, access


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
    local = cmd_section(text, "local", "worker")
    assert local.index("call :resolve_urls") < local.index("docker compose up")
    run_migrate = text[text.index("\n:run_migrate\n"):]
    run_migrate = run_migrate[:run_migrate.index("exit /b 0")]
    assert "call :resolve_urls" in run_migrate
    resolve = text[text.index("\n:resolve_urls\n"):]
    resolve = resolve[:resolve.index("\nexit /b 0\n")]
    assert "%PY% scripts\\check_db.py resolve" in resolve
    assert "errorlevel 1" in resolve


def test_start_cmd_never_builds_or_exports_database_url():
    code = "\n".join(line for line in START_CMD.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("::"))
    for access in CMD_URL_ACCESS + ("POSTGRES_PASSWORD", "POSTGRES_USER", ":url_hostport",
                                    "@localhost"):
        assert access not in code, access


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


# Чтение DATABASE_URL в shell/cmd: в cmd «!» в пароле съедает отложенное
# раскрытие, а вывод ошибок libpq показывал бы пароль. В режиме worker адрес
# трогает только scripts/check_db.py (и сами воркеры через core.config).
CMD_URL_ACCESS = ("env_or_file DATABASE_URL", "!DATABASE_URL!", "%DATABASE_URL%",
                  'set "DATABASE_URL', "url_hostport DATABASE_URL", "read_env DATABASE_URL")
SH_URL_ACCESS = ("env_or_file DATABASE_URL", "$DATABASE_URL", "${DATABASE_URL",
                 "export DATABASE_URL", "get_env_key DATABASE_URL", "url_hostport")


def test_start_cmd_worker_mode_leaves_database_url_to_the_helper():
    text = START_CMD.read_text(encoding="utf-8")
    worker = cmd_section(text, "worker", "migrate_only")
    assert "docker" not in worker.lower()
    assert "run_migrate" not in worker and "init_db" not in worker
    assert "uvicorn" not in worker and "resolve_urls" not in worker
    for access in CMD_URL_ACCESS:
        assert access not in worker, access
    assert "%PY% scripts\\check_db.py check" in worker
    assert worker.index("check_db.py check") < worker.index("call :start_workers")
    assert ":check_db" not in text and "connect_timeout" not in text
    # --port в режиме worker — предупреждение, как в docker
    assert "if defined PORT_ARG" in worker and "ignored in worker mode" in worker
    workers = text[text.index("\n:start_workers\n"):]
    workers = workers[:workers.index("\nexit /b 0\n")]
    for access in CMD_URL_ACCESS:
        assert access not in workers, access


def test_start_sh_worker_mode_leaves_database_url_to_the_helper():
    text = START_SH.read_text(encoding="utf-8")
    for name in ("start_worker_mode", "start_workers"):
        body = text[text.index(f"{name}() {{"):]
        body = body[:body.index("\n}\n")]
        for access in SH_URL_ACCESS:
            assert access not in body, (name, access)
    worker = text[text.index("start_worker_mode() {"):]
    worker = worker[:worker.index("\n}\n")]
    assert '"$VENV_PY" scripts/check_db.py check' in worker
    assert "PORT_ARG" in worker and "warn" in worker


def test_start_cmd_warns_about_workers_already_running():
    text = START_CMD.read_text(encoding="utf-8")
    workers = text[text.index("\n:start_workers\n"):]
    workers = workers[:workers.index("\nexit /b 0\n")]
    assert "Win32_Process" in workers and "worker\\.run" in workers
    assert "StartsWith($root" in workers  # только эта папка проекта
    assert "already running" in workers
    assert workers.index("already running") < workers.index('start "classifier-worker-')


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


def fail_db_check(tmp_path: Path, code: int) -> None:
    """Заглушка python: проверка базы (scripts/check_db.py) завершается с кодом code."""
    py = tmp_path / "venv" / "bin" / "python"
    py.write_text(py.read_text(encoding="utf-8")
                  + f'case "$*" in *check_db.py*) exit {code} ;; esac\n',
                  encoding="utf-8", newline="\n")


@needs_bash
def test_start_sh_api_mode_exports_no_database_url(tmp_path):
    """Пароль с ! @ : / # % и пробелом: скрипт его не трогает, процессам Python
    DATABASE_URL не экспортируется (они строят адрес сами)."""
    calls = start_sandbox(tmp_path, '{"status":"ok","agent_ready":false,"db":true}',
                          "API_PORT=18997\nPOSTGRES_PORT=5434\n"
                          "POSTGRES_PASSWORD='p!a@b:c/d#e%f g'\n")
    py = tmp_path / "venv" / "bin" / "python"
    py.write_text(py.read_text(encoding="utf-8")
                  + f'echo "DBURL=[${{DATABASE_URL:-}}]" >> "{calls.as_posix()}"\n',
                  encoding="utf-8", newline="\n")
    proc = run_sandbox(tmp_path, "api")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    log = read_calls(calls)
    assert "scripts/check_db.py resolve" in log
    assert log.index("check_db.py resolve") < log.index("docker compose up")
    assert "DBURL=[]" in log and "DBURL=[postgresql" not in log
    assert "p!a@b" not in out


@needs_bash
def test_start_sh_port_mismatch_from_check_db_stops_before_docker(tmp_path):
    calls = start_sandbox(tmp_path, "{}", "API_PORT=18996\n")
    fail_db_check(tmp_path, 2)
    proc = run_sandbox(tmp_path, "api")
    assert proc.returncode != 0
    log = read_calls(calls)
    assert "scripts/check_db.py resolve" in log
    assert "docker compose up" not in log and "init_db.py" not in log


@needs_bash
@pytest.mark.parametrize("code", [1, 2])  # 1 — база не отвечает, 2 — адреса нет / неверен
def test_start_sh_worker_mode_stops_when_db_check_fails(tmp_path, code):
    calls = start_sandbox(tmp_path, "{}", "CLASSIFY_WORKERS=2\n")
    fail_db_check(tmp_path, code)
    proc = run_sandbox(tmp_path, "worker")
    assert proc.returncode != 0
    assert "проверка базы не прошла" in proc.stderr
    log = read_calls(calls)
    assert "scripts/check_db.py check" in log
    assert "worker.run" not in log


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
    assert "scripts/check_db.py check" in log  # проверка, что база отвечает
    assert log.index("check_db.py") < log.index("worker.run")
    assert log.count("-m worker.run --queues classify_sync,classify") == 2, log
    assert log.count("-m worker.run --queues training") == 1, log
    # адрес shell не печатает; порт POSTGRES_PORT не сверяется
    assert "s3cret" not in out and "db.example" not in out


@needs_bash
def test_start_sh_worker_mode_warns_that_port_is_ignored(tmp_path):
    start_sandbox(tmp_path, "{}", "CLASSIFY_WORKERS=1\n")
    proc = run_sandbox(tmp_path, "worker --port 9999")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "--port 9999" in out and "не действует" in out


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

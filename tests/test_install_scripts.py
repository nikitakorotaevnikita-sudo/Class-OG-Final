"""Проверка bash-установщиков: синтаксис и правка .env.

Правка `.env` — самое опасное место установщика: он трогает файл с ключами.
Функции из `scripts/install_common.sh` проверяются в отдельном каталоге, чтобы
случайно не задеть настоящий `.env` проекта.
"""

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ["install.sh", "install_offline.sh", "launch.sh", "scripts/install_common.sh"]

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash недоступен")


def run_bash(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize("name", SCRIPTS)
def test_syntax_is_valid(name):
    proc = subprocess.run([BASH, "-n", name], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"{name}: {proc.stderr}"


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_crlf_line_endings(name):
    """CRLF в shell-скрипте на Linux даёт «bad interpreter: No such file or directory»."""
    assert b"\r\n" not in (ROOT / name).read_bytes(), f"{name} сохранён с CRLF"


@pytest.fixture
def sandbox(tmp_path):
    """Каталог с копией общей библиотеки — правки .env идут только здесь."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "install_common.sh", tmp_path / "scripts")
    return tmp_path


def env_helper(body: str) -> str:
    return textwrap.dedent(f"""
        set -u
        . scripts/install_common.sh
        {body}
    """)


def test_set_env_key_appends_when_missing(sandbox):
    (sandbox / ".env").write_text("LLM_PROVIDER=ario\n", encoding="utf-8")
    run_bash(env_helper('set_env_key CUSTOM_LLM_MODEL "gpt-oss-20b"'), sandbox)
    content = (sandbox / ".env").read_text(encoding="utf-8")
    assert "LLM_PROVIDER=ario" in content
    assert "CUSTOM_LLM_MODEL=gpt-oss-20b" in content


def test_set_env_key_replaces_active_line(sandbox):
    (sandbox / ".env").write_text("LLM_PROVIDER=ario\nARIO_MODEL=old\n", encoding="utf-8")
    run_bash(env_helper('set_env_key ARIO_MODEL "Qwen/Qwen3.8-27B-Ario"'), sandbox)
    lines = (sandbox / ".env").read_text(encoding="utf-8").splitlines()
    assert "ARIO_MODEL=Qwen/Qwen3.8-27B-Ario" in lines
    assert "ARIO_MODEL=old" not in lines
    # Ключ не должен продублироваться.
    assert sum(1 for line in lines if line.startswith("ARIO_MODEL=")) == 1


def test_set_env_key_keeps_commented_lines(sandbox):
    """Закомментированные варианты — документация в .env, их трогать нельзя."""
    (sandbox / ".env").write_text(
        "# EMBEDDING_MODEL=BAAI/bge-m3\nEMBEDDING_MODEL=intfloat/multilingual-e5-base\n",
        encoding="utf-8")
    run_bash(env_helper('set_env_key EMBEDDING_MODEL "offline_bundle/models/multilingual-e5-base"'),
             sandbox)
    content = (sandbox / ".env").read_text(encoding="utf-8")
    assert "# EMBEDDING_MODEL=BAAI/bge-m3" in content
    assert "EMBEDDING_MODEL=offline_bundle/models/multilingual-e5-base" in content
    assert "EMBEDDING_MODEL=intfloat/multilingual-e5-base" not in content


def test_set_env_key_survives_slashes_and_ampersands(sandbox):
    """Значения с / и & ломали бы sed-подстановку — поэтому внутри awk."""
    (sandbox / ".env").write_text("RX_ODATA_URL=\n", encoding="utf-8")
    tricky = "http://rx.local/integration/odata?a=1&b=2"
    run_bash(env_helper(f'set_env_key RX_ODATA_URL "{tricky}"'), sandbox)
    assert f"RX_ODATA_URL={tricky}" in (sandbox / ".env").read_text(encoding="utf-8")


def test_get_env_key_ignores_comments(sandbox):
    (sandbox / ".env").write_text("# VECTOR_DB_DIR=data/vector_db\nVECTOR_DB_DIR=data/adapted\n",
                                  encoding="utf-8")
    proc = run_bash(env_helper('get_env_key VECTOR_DB_DIR'), sandbox)
    assert proc.stdout.strip() == "data/adapted"


def test_vdb_dir_defaults_when_not_configured(sandbox):
    (sandbox / ".env").write_text("LLM_PROVIDER=ario\n", encoding="utf-8")
    proc = run_bash(env_helper('vdb_dir'), sandbox)
    assert proc.stdout.strip() == "data/vector_db"


def test_vdb_dir_reads_configured_value(sandbox):
    (sandbox / ".env").write_text("VECTOR_DB_DIR=data/vector_db_adapted_v3\n", encoding="utf-8")
    proc = run_bash(env_helper('vdb_dir'), sandbox)
    assert proc.stdout.strip() == "data/vector_db_adapted_v3"


def test_ask_uses_default_in_noninteractive_mode(sandbox):
    proc = run_bash(env_helper('NONINTERACTIVE=1; ask PORT_VALUE "Порт" "8010"; echo "=$PORT_VALUE"'),
                    sandbox)
    assert "=8010" in proc.stdout


def test_ask_prefers_environment_value(sandbox):
    """В неинтерактивном режиме значения приходят из окружения — как в CI."""
    proc = run_bash(env_helper('NONINTERACTIVE=1; LLM_PROVIDER=custom; '
                               'ask LLM_PROVIDER "Провайдер" "ario"; echo "=$LLM_PROVIDER"'),
                    sandbox)
    assert "=custom" in proc.stdout


def test_launch_requires_venv(tmp_path):
    """launch.sh без установленного venv должен внятно отказать, а не упасть."""
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "launch.sh", tmp_path / "launch.sh")
    (tmp_path / ".env").write_text("VECTOR_DB_DIR=data/vector_db\n", encoding="utf-8")
    proc = run_bash("bash launch.sh server 8010", tmp_path)
    assert proc.returncode != 0
    assert "install.sh" in (proc.stdout + proc.stderr)


# ── pid из run/*.pid: проверка командной строки перед kill ──────────────────

def test_pid_is_classifier_checks_command_line(sandbox):
    """После перезагрузки pid из run/*.pid может принадлежать чужому процессу:
    start.sh / stop.sh не должны его «узнавать» (и убивать)."""
    # Маркер собирается из частей: пока дочерний процесс не сделал exec, его
    # командная строка — текст этого скрипта, и цельный маркер дал бы ложное «наш».
    proc = run_bash(env_helper(f'''
        sleep 30 & other=$!
        M1=queueing; bash -c 'sleep 30; :' "$M1.celery_app" & ours=$!
        sleep 1
        pid_is_classifier "$other" && echo "OTHER=yes" || echo "OTHER=no"
        pid_is_classifier "$ours" && echo "OURS=yes" || echo "OURS=no"
        pid_is_classifier 999999 && echo "DEAD=yes" || echo "DEAD=no"
        pid_is_classifier "" && echo "EMPTY=yes" || echo "EMPTY=no"
        kill "$other" "$ours" 2>/dev/null
    '''), sandbox)
    assert "OTHER=no" in proc.stdout, proc.stdout + proc.stderr
    assert "OURS=yes" in proc.stdout, proc.stdout + proc.stderr
    assert "DEAD=no" in proc.stdout and "EMPTY=no" in proc.stdout


# ── setup_infra: порты, PostgreSQL + RabbitMQ в Docker, миграция ────────────

def infra_sandbox(sandbox, docker_body: str):
    """Заглушки: venv/bin/python, docker и start.sh пишут вызовы в calls.log."""
    calls = sandbox / "calls.log"
    bindir = sandbox / "bin"
    bindir.mkdir()
    (sandbox / "venv" / "bin").mkdir(parents=True)
    stubs = {
        sandbox / "venv" / "bin" / "python": 'echo "python $*" >> calls.log\n',
        bindir / "docker": f'echo "docker $*" >> calls.log\n{docker_body}\n',
        sandbox / "start.sh": 'echo "start.sh $*" >> calls.log\n',
    }
    for path, body in stubs.items():
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    (sandbox / ".env").write_text("A=1\n", encoding="utf-8")
    return calls, bindir


def run_setup_infra(sandbox, bindir, args="", only_stubs=False):
    # $PWD/bin, а не путь Windows: «C:/...» в PATH разрезалось бы по двоеточию.
    # only_stubs: в PATH только заглушки — настоящий docker хоста не виден.
    rest = "" if only_stubs else ":$PATH"
    script = env_helper(f'export PATH="$PWD/{bindir.name}{rest}"; setup_infra {args}; '
                        'echo "RC=$?"; echo "FAILED=${CHECK_FAILED:-0}"')
    return run_bash(script, sandbox)


def test_setup_infra_happy_path(sandbox):
    calls, bindir = infra_sandbox(sandbox, "exit 0")
    proc = run_setup_infra(sandbox, bindir)
    log = calls.read_text(encoding="utf-8")
    assert "RC=0" in proc.stdout and "FAILED=0" in proc.stdout, proc.stdout + proc.stderr
    assert "python scripts/setup_infra_env.py --env-file .env" in log
    assert "docker compose up -d --wait postgres rabbitmq" in log
    assert "start.sh migrate" in log
    # порты выбираются до запуска контейнеров, миграция — после
    assert log.index("setup_infra_env.py") < log.index("compose up") < log.index("start.sh migrate")


def test_setup_infra_without_docker_is_soft(sandbox):
    calls, bindir = infra_sandbox(sandbox, "exit 0")
    (bindir / "docker").unlink()
    proc = run_setup_infra(sandbox, bindir, only_stubs=True)
    out = proc.stdout + proc.stderr
    assert "RC=0" in proc.stdout and "FAILED=0" in proc.stdout, out
    assert "./start.sh migrate" in out
    log = calls.read_text(encoding="utf-8")
    assert "setup_infra_env.py" in log  # порты пишутся и без Docker
    assert "start.sh migrate" not in log


def test_setup_infra_engine_not_running_is_soft(sandbox):
    calls, bindir = infra_sandbox(sandbox, '[ "$1" = "info" ] && exit 1; exit 0')
    proc = run_setup_infra(sandbox, bindir)
    out = proc.stdout + proc.stderr
    assert "RC=0" in proc.stdout and "FAILED=0" in proc.stdout, out
    assert "Docker" in out and "./start.sh migrate" in out
    assert "compose up" not in calls.read_text(encoding="utf-8")


def test_setup_infra_offline_without_images_skips(sandbox):
    calls, bindir = infra_sandbox(sandbox, '[ "$1" = "image" ] && exit 1; exit 0')
    proc = run_setup_infra(sandbox, bindir, "--offline")
    out = proc.stdout + proc.stderr
    assert "RC=0" in proc.stdout and "FAILED=0" in proc.stdout, out
    assert "docker load" in out and "docker save" in out
    log = calls.read_text(encoding="utf-8")
    assert "docker image inspect pgvector/pgvector:pg16 rabbitmq:3.13-management" in log
    assert "compose up" not in log


def test_setup_infra_compose_failure_marks_install(sandbox):
    calls, bindir = infra_sandbox(sandbox, '[ "$2" = "up" ] && exit 1; exit 0')
    proc = run_setup_infra(sandbox, bindir)
    assert "RC=0" in proc.stdout and "FAILED=1" in proc.stdout, proc.stdout + proc.stderr
    assert "start.sh migrate" not in calls.read_text(encoding="utf-8")


@pytest.mark.parametrize("name,offline", [("install.sh", False), ("install_offline.sh", True)])
def test_installers_run_setup_infra(name, offline):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert ("setup_infra --offline" in text) is offline
    assert "setup_infra" in text
    # шаг инфраструктуры — после настройки .env
    assert text.index("set_env_key LLM_PROVIDER") < text.index("setup_infra")


def test_launch_sh_default_port_prints_a_real_newline():
    r"""printf '%s<перевод строки>' — перевод строки должен быть \n, а не разрыв строки в кавычках."""
    for name in ("launch.sh", "start.sh", "stop.sh", "install.sh", "install_offline.sh",
                 "scripts/install_common.sh"):
        for no, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bprintf\s+'", line):
                assert line.count("'") >= 2, f"{name}:{no}: строка printf не закрыта: {line}"

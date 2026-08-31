"""Проверка bash-установщиков: синтаксис и правка .env.

Правка `.env` — самое опасное место установщика: он трогает файл с ключами.
Функции из `scripts/install_common.sh` проверяются в отдельном каталоге, чтобы
случайно не задеть настоящий `.env` проекта.
"""

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

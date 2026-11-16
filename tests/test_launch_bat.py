"""`launch.bat` — Windows-аналог `launch.sh`, и расходиться они не должны.

Батник отстал от остального проекта: поднимал сервис на 8000, тогда как везде
(docker-compose, установщики, инструкции, стенд) закреплён 8010, и запускал
uvicorn с `--reload`, который на рабочем сервисе не нужен. В меню выбора модели
не было провайдера `custom` — того самого, через который подключается модель
Заказчика.

Порт должен задаваться руками: аргументом при запуске либо `API_PORT` в `.env`.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BAT = (ROOT / "launch.bat").read_text(encoding="utf-8", errors="replace")
SH = (ROOT / "launch.sh").read_text(encoding="utf-8")


# ── Порт ────────────────────────────────────────────────────────────────────

def test_default_port_is_8010():
    assert "8010" in BAT
    assert "--port 8000" not in BAT, "8000 остался от старой версии"


def test_no_stray_8000_as_service_port():
    """8000 допустим только как пример чужого endpoint, но не как наш порт."""
    for line in BAT.splitlines():
        if "8000" in line and "uvicorn" in line.lower():
            pytest.fail(f"сервис всё ещё поднимается на 8000: {line.strip()}")


def test_port_can_be_given_as_an_argument():
    assert re.search(r"%~?2|%PORT_ARG%|%2", BAT), "нет приёма порта аргументом"


def test_port_can_be_set_in_env_file():
    assert "API_PORT" in BAT, "порт должен настраиваться и через .env"


def test_menu_offers_another_port():
    assert "порт" in BAT.lower() or "port" in BAT.lower()
    assert BAT.count("8010") >= 2, "порт по умолчанию нужен и в меню, и в запуске"


def test_env_example_and_launchers_agree_on_the_port():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "API_PORT=8010" in env_example
    assert "API_PORT" in SH, "launch.sh должен читать тот же ключ"


# ── Не отстать от остального проекта ────────────────────────────────────────

def test_no_reload_flag():
    """--reload перезапускает сервис на каждую правку файла — не для стенда."""
    assert "--reload" not in BAT


def test_custom_provider_is_offered():
    """Через `custom` подключается модель Заказчика (vLLM) — без него меню врёт."""
    assert "custom" in BAT.lower()


def test_every_supported_provider_is_in_the_menu():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from classifier.classifier_agent import SUPPORTED_LLM_PROVIDERS

    missing = [p for p in SUPPORTED_LLM_PROVIDERS if p not in BAT.lower()]
    assert not missing, f"в меню нет провайдеров: {missing}"


def test_points_at_the_current_installer():
    """setup.ps1 больше не единственный путь установки — есть install.bat."""
    assert "install.bat" in BAT
    assert "setup.ps1" not in BAT


def test_stale_ario_model_name_is_gone():
    """Набор моделей на endpoint меняется — конкретное имя в меню устаревает."""
    assert "Qwen3.6-35B-A3B" not in BAT


def test_batch_file_keeps_crlf():
    """cmd.exe спотыкается на LF в некоторых конструкциях — батник держим в CRLF."""
    raw = (ROOT / "launch.bat").read_bytes()
    assert b"\r\n" in raw
    assert re.search(rb"[^\r]\n", raw) is None, "есть строки без CR"


# ── Неинтерактивный запуск ──────────────────────────────────────────────────

def test_pause_only_when_a_human_is_at_the_keyboard():
    """`launch.bat server` из другого скрипта не должен виснуть на «нажмите...»."""
    assert "if defined INTERACTIVE" in BAT
    assert "INTERACTIVE=1" in BAT


def test_provider_is_known_before_arguments_are_parsed():
    """В режиме `server` строка про провайдера печаталась пустой."""
    dispatch = BAT.index("Command-line mode")
    assert BAT.index('set "CURRENT_LLM=') < dispatch


def test_empty_menu_input_does_not_loop():
    """При конце ввода `set /p` оставляет выбор пустым — меню крутилось вечно."""
    assert 'if "%CHOICE%"=="" goto :server' in BAT


# ── Новый стек: PostgreSQL + воркеры ────────────────────────────────────────

def test_server_mode_delegates_to_start_all():
    """Классифицируют воркеры — один uvicorn без них ничего не обработает."""
    assert 'start.cmd" all --port' in BAT
    assert "start.sh all --port" in SH
    assert "celery" not in BAT.lower() and "celery" not in SH.lower()
    assert "rabbit" not in BAT.lower() and "rabbit" not in SH.lower()


# Пункт меню → чем он заканчивается в launch.bat (метка) и в launch.sh (ветка case).
MENU = {
    "1": (":server", 'run_server all "$port"'),
    "2": (":server_api", 'run_server api "$port"'),
    "3": (":server_worker", "run_server worker"),
    "4": (":ask_port", 'run_server all "$srv_port"'),
    "5": (":check", "check_offline.py"),
    "6": (":tests", "pytest"),
    "7": (":classify", "classify_manual.py"),
    "8": (":operator", "operator_cli.py"),
    "9": (":select_llm", "select_llm"),
}


def test_menus_match_item_for_item():
    bat_items = re.findall(r"^echo   \[(\d)\] ", BAT, re.MULTILINE)
    menu_sh = SH[SH.index("menu() {"):]
    sh_items = re.findall(r"printf '   \[(\d)\] ", menu_sh[:menu_sh.index("ask choice")])
    bat_menu = BAT[BAT.index("\n:menu\n"):BAT.index("\n:ask_port\n")]
    assert bat_items[:len(MENU)] == list(MENU), bat_items
    assert sh_items == list(MENU), sh_items
    assert "Select (1-9) [1]" in BAT and '"Выбор (1-9)" "1"' in SH
    for no, (label, sh_action) in MENU.items():
        assert f'if "%CHOICE%"=="{no}" goto {label}' in bat_menu, no
        branch = re.search(rf"^\s*{no}(\|\"\")?\) (.*)$", menu_sh, re.MULTILINE)
        assert branch and sh_action in branch.group(2), (no, branch and branch.group(0))


def test_bat_menu_titles_name_the_three_start_modes():
    assert "[1] Start everything" in BAT
    assert "[2] Start without workers" in BAT
    assert "[3] Start workers only" in BAT
    assert "[1] Запустить всё" in SH
    assert "[2] Запустить без воркеров" in SH
    assert "[3] Запустить только воркеры" in SH


def test_mode_labels_call_start_with_the_mode():
    api = BAT[BAT.index("\n:server_api\n"):]
    assert 'start.cmd" api --port !PORT!' in api[:api.index("goto :end")]
    worker = BAT[BAT.index("\n:server_worker\n"):]
    assert 'start.cmd" worker' in worker[:worker.index("goto :end")]
    assert "--port" not in worker[:worker.index("goto :end")]


def test_missing_file_vector_db_does_not_block_startup():
    """Векторы живут в PostgreSQL: проверка embeddings.npy больше не нужна."""
    assert "embeddings.npy" not in BAT
    assert "embeddings.npy" not in SH

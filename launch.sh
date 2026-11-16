#!/usr/bin/env bash
# Запуск классификатора на Linux. Аналог launch.bat — меню и команды совпадают.
#
#   ./launch.sh                 меню
#   ./launch.sh server [порт]   локальный стек через start.sh local (порт 8010)
#   ./launch.sh check           проверка установки
#   ./launch.sh tests           тесты
#   ./launch.sh operator        режим оператора (верификация + дообучение)
#   ./launch.sh classify        разовая классификация из консоли
#
# Порт: аргумент запуска, затем API_PORT в .env, затем 8010.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

[ -x "$VENV_PY" ] || die "нет venv — сначала ./install.sh (или ./install_offline.sh)"

DEFAULT_PORT=8010

# Порт, закреплённый в .env (API_PORT), иначе 8010.
default_port() {
    local configured
    configured="$(get_env_key API_PORT | tr -d '[:space:]')"
    printf '%s\n' "${configured:-$DEFAULT_PORT}"
}

current_llm() {
    local provider
    provider="$(get_env_key LLM_PROVIDER | tr -d '[:space:]')"
    printf '%s\n' "${provider:-groq}"
}

# Векторы классификатора в PostgreSQL, классифицируют Celery-воркеры:
# «server» поднимает весь локальный стек через start.sh local (PostgreSQL и
# RabbitMQ в Docker, миграция, API и воркеры из venv).
run_server() {
    local port="${1:-$(default_port)}"
    say "server запускает ./start.sh local (API + воркеры, БД в Docker)"
    say "провайдер LLM: $(current_llm)"
    exec bash ./start.sh local --port "$port"
}

# Меню выбора провайдера LLM: меняет только LLM_PROVIDER в .env.
select_llm() {
    local choice new=""
    while [ -z "$new" ]; do
        banner "Выбор провайдера LLM"
        printf '   [1] ario    — Directum360, основной для проекта\n'
        printf '   [2] custom  — свой OpenAI-совместимый endpoint Заказчика (vLLM)\n'
        printf '   [3] ollama  — локальная модель\n'
        printf '   [4] groq    — облако, нужен интернет\n'
        printf '   [5] gemini  — облако, нужен интернет\n'
        printf '   Имя модели и адрес задаются в .env или в бэк-офисе (вкладка «Настройки»).\n'
        printf '   Это меню переключает только провайдера.\n\n'
        choice=""
        ask choice "Выбор (1-5)" ""
        case "$choice" in
            1) new=ario ;;
            2) new=custom ;;
            3) new=ollama ;;
            4) new=groq ;;
            5) new=gemini ;;
            *) warn "нет такого пункта"
               # без клавиатуры (ввод из файла/CI) не крутиться бесконечно
               [ -t 0 ] || { warn "провайдер не изменён"; return 0; } ;;
        esac
    done
    set_env_key LLM_PROVIDER "$new"
    ok "провайдер LLM: $new"
}

menu() {
    local choice port
    while true; do
        port="$(default_port)"
        banner "Классификатор обращений граждан"
        printf '   [1] Запустить сервис (порт %s)\n' "$port"
        printf '   [2] Запустить сервис на другом порту\n'
        printf '   [3] Проверить установку\n'
        printf '   [4] Тесты\n'
        printf '   [5] Разовая классификация (ввести текст обращения)\n'
        printf '   [6] Режим оператора (верификация + дообучение)\n'
        printf '   [7] Выбрать провайдера LLM\n'
        printf '   Текущий LLM: %s\n\n' "$(current_llm)"
        choice=""
        ask choice "Выбор (1-7)" "1"
        case "$choice" in
            1|"") run_server "$port" ;;
            2) local srv_port=""; ask srv_port "Порт" "$port"; run_server "$srv_port" ;;
            3) exec "$VENV_PY" scripts/check_offline.py --stage post --online ;;
            4) exec "$VENV_PY" -m pytest tests/ -q --ignore=tests/e2e ;;
            5) exec "$VENV_PY" src/cli/classify_manual.py ;;
            6) exec "$VENV_PY" src/cli/operator_cli.py ;;
            7) select_llm ;;
            *) warn "нет такого пункта"; [ -t 0 ] || exit 2 ;;
        esac
    done
}

case "${1:-menu}" in
    server)   run_server "${2:-}" ;;
    check)    exec "$VENV_PY" scripts/check_offline.py --stage post --online ;;
    tests)    exec "$VENV_PY" -m pytest tests/ -q --ignore=tests/e2e ;;
    operator) exec "$VENV_PY" src/cli/operator_cli.py ;;
    classify) exec "$VENV_PY" src/cli/classify_manual.py ;;
    menu)     menu ;;
    -h|--help) sed -n '2,12p' "$0" ;;
    *) printf 'Неизвестная команда: %s (см. --help)\n' "$1" >&2; exit 2 ;;
esac

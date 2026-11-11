#!/usr/bin/env bash
# Запуск прототипа на Linux. Аналог launch.bat.
#
#   ./launch.sh                 меню
#   ./launch.sh server [порт]   локальный стек через start.sh local (порт 8010)
#   ./launch.sh check           проверка установки
#   ./launch.sh tests           тесты классификатора
#   ./launch.sh operator        режим оператора (верификация)
#   ./launch.sh classify        разовая классификация из консоли

set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

[ -x "$VENV_PY" ] || die "нет venv — сначала ./install.sh (или ./install_offline.sh)"

# Порт: аргумент запуска, затем API_PORT из .env, затем 8010.
default_port() {
    local configured
    configured="$(get_env_key API_PORT)"
    printf '%s
' "${configured:-8010}"
}

# Векторы классификатора теперь в PostgreSQL, классифицируют Celery-воркеры:
# «server» поднимает весь локальный стек через start.sh local (PostgreSQL и
# RabbitMQ в Docker, миграция, API и воркеры из venv).
run_server() {
    local port="${1:-$(default_port)}"
    say "server теперь запускает ./start.sh local (API + воркеры, БД в Docker)"
    say "провайдер LLM: $(get_env_key LLM_PROVIDER)"
    exec bash ./start.sh local --port "$port"
}

case "${1:-menu}" in
    server)   run_server "${2:-}" ;;
    check)    exec "$VENV_PY" scripts/check_offline.py --stage post --online ;;
    tests)    exec "$VENV_PY" -m pytest tests/ -q --ignore=tests/e2e ;;
    operator) exec "$VENV_PY" src/operator_cli.py ;;
    classify) exec "$VENV_PY" src/classify_manual.py ;;
    menu)
        banner "Классификатор обращений граждан"
        printf '   [1] Запустить сервис (порт 8010)\n'
        printf '   [2] Запустить сервис на другом порту\n'
        printf '   [3] Проверить установку\n'
        printf '   [4] Тесты\n'
        printf '   [5] Режим оператора\n'
        printf '   [6] Разовая классификация\n\n'
        ask CHOICE "Выбор (1-6)" "1"
        case "$CHOICE" in
            2) ask SRV_PORT "Порт" "8010"; run_server "$SRV_PORT" ;;
            3) exec "$VENV_PY" scripts/check_offline.py --stage post --online ;;
            4) exec "$VENV_PY" -m pytest tests/ -q --ignore=tests/e2e ;;
            5) exec "$VENV_PY" src/operator_cli.py ;;
            6) exec "$VENV_PY" src/classify_manual.py ;;
            *) run_server 8010 ;;
        esac
        ;;
    -h|--help) sed -n '2,11p' "$0" ;;
    *) printf 'Неизвестная команда: %s (см. --help)\n' "$1" >&2; exit 2 ;;
esac

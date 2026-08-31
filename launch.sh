#!/usr/bin/env bash
# Запуск прототипа на Linux. Аналог launch.bat.
#
#   ./launch.sh                 меню
#   ./launch.sh server [порт]   API-сервис (по умолчанию 8010)
#   ./launch.sh check           проверка установки
#   ./launch.sh tests           тесты классификатора
#   ./launch.sh operator        режим оператора (верификация)
#   ./launch.sh classify        разовая классификация из консоли

set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

[ -x "$VENV_PY" ] || die "нет venv — сначала ./install.sh (или ./install_offline.sh)"

VDB="$(vdb_dir)"
# Каталог базы настраивается через VECTOR_DB_DIR: боевая сборка лежит в
# data/vector_db_adapted_v3, поэтому проверять фиксированный путь нельзя.
[ -f "$VDB/embeddings.npy" ] \
    || die "векторная база не найдена в $VDB — запустить установщик или src/build_vectordb.py"

run_server() {
    local port="${1:-8010}"
    say "провайдер LLM: $(get_env_key LLM_PROVIDER)"
    say "векторная база: $VDB"
    printf '   http://0.0.0.0:%s  (Ctrl+C — остановить)\n\n' "$port"
    exec "$VENV_PY" -m uvicorn src.api_server:app --host 0.0.0.0 --port "$port"
}

case "${1:-menu}" in
    server)   run_server "${2:-8010}" ;;
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

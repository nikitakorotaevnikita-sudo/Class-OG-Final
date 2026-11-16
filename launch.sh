#!/usr/bin/env bash
# Запуск классификатора на Linux. Аналог launch.bat — меню и команды совпадают.
#
#   ./launch.sh                 меню
#   ./launch.sh server [порт]   всё через start.sh all (порт 8010)
#   ./launch.sh api [порт]      без воркеров через start.sh api
#   ./launch.sh worker          только воркеры через start.sh worker
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

# Векторы классификатора в PostgreSQL, классифицируют воркеры очереди.
# run_server РЕЖИМ [порт]:
#   all    — всё через start.sh all (PostgreSQL в Docker, миграция, API и воркеры из venv);
#   api    — без воркеров (воркеры на других машинах, задачи ждут в очереди);
#   worker — только воркеры, база — DATABASE_URL из .env этой машины.
run_server() {
    local mode="$1" port="${2:-}"
    if [ -z "$port" ]; then port="$(default_port)"; fi
    say "провайдер LLM: $(current_llm)"
    case "$mode" in
        all)
            say "запуск всего: ./start.sh all (API + воркеры, БД в Docker)"
            exec bash ./start.sh all --port "$port" ;;
        api)
            say "запуск без воркеров: ./start.sh api (API, БД в Docker)"
            exec bash ./start.sh api --port "$port" ;;
        worker)
            say "запуск только воркеров: ./start.sh worker (без Docker)"
            exec bash ./start.sh worker ;;
    esac
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
        printf '   [1] Запустить всё: API + воркеры (порт %s)\n' "$port"
        printf '   [2] Запустить без воркеров: только API (порт %s)\n' "$port"
        printf '   [3] Запустить только воркеры (база — DATABASE_URL в .env)\n'
        printf '   [4] Запустить всё на другом порту\n'
        printf '   [5] Проверить установку\n'
        printf '   [6] Тесты\n'
        printf '   [7] Разовая классификация (ввести текст обращения)\n'
        printf '   [8] Режим оператора (верификация + дообучение)\n'
        printf '   [9] Выбрать провайдера LLM\n'
        printf '   Текущий LLM: %s\n\n' "$(current_llm)"
        choice=""
        ask choice "Выбор (1-9)" "1"
        case "$choice" in
            1|"") run_server all "$port" ;;
            2) run_server api "$port" ;;
            3) run_server worker ;;
            4) local srv_port=""; ask srv_port "Порт" "$port"; run_server all "$srv_port" ;;
            5) exec "$VENV_PY" scripts/check_offline.py --stage post --online ;;
            6) exec "$VENV_PY" -m pytest tests/ -q --ignore=tests/e2e ;;
            7) exec "$VENV_PY" src/cli/classify_manual.py ;;
            8) exec "$VENV_PY" src/cli/operator_cli.py ;;
            9) select_llm ;;
            *) warn "нет такого пункта"; [ -t 0 ] || exit 2 ;;
        esac
    done
}

case "${1:-menu}" in
    server)   run_server all "${2:-}" ;;
    api)      run_server api "${2:-}" ;;
    worker)   run_server worker ;;
    check)    exec "$VENV_PY" scripts/check_offline.py --stage post --online ;;
    tests)    exec "$VENV_PY" -m pytest tests/ -q --ignore=tests/e2e ;;
    operator) exec "$VENV_PY" src/cli/operator_cli.py ;;
    classify) exec "$VENV_PY" src/cli/classify_manual.py ;;
    menu)     menu ;;
    -h|--help) sed -n '2,14p' "$0" ;;
    *) printf 'Неизвестная команда: %s (см. --help)\n' "$1" >&2; exit 2 ;;
esac

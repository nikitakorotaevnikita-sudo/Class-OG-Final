#!/usr/bin/env bash
# Запуск классификатора на Linux. Аналог start.cmd.
#
#   ./start.sh [docker]             весь стек в Docker (по умолчанию)
#   ./start.sh local [--port N]     PostgreSQL и RabbitMQ в Docker, API и воркеры из venv
#   ./start.sh migrate              только схема БД + импорт файлов (БД уже должна работать)
#
#   --training                      дополнительно поднять воркер дообучения
#
# Остановка: ./stop.sh [docker|local]. Логи локального режима — run/*.log.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

MODE="docker"
TRAINING=0
PORT_ARG=""

usage() { sed -n '2,10p' "$0"; }

while [ $# -gt 0 ]; do
    case "$1" in
        docker|local|migrate) MODE="$1" ;;
        --training) TRAINING=1 ;;
        --port)
            [ $# -ge 2 ] || { printf 'После --port нужен номер порта
' >&2; exit 2; }
            shift; PORT_ARG="$1" ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Неизвестный аргумент: %s (см. --help)\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

# Значение ключа из .env без хвостового комментария; иначе — значение по умолчанию.
env_value() {
    local value
    value="$(get_env_key "$1" | sed 's/[[:space:]]#.*$//; s/[[:space:]]*$//')"
    printf '%s\n' "${value:-$2}"
}

# Compose читает .env (env_file и монтирование). Без файла Docker создаст на
# его месте каталог, а бэк-офису станет некуда писать настройки.
ensure_env() {
    if [ ! -f .env ]; then
        cp .env.example .env
        warn ".env не найден — создан из .env.example. Заполнить ключи LLM и пароли, затем перезапустить."
    fi
}

require_docker() {
    command -v docker >/dev/null 2>&1 || die "docker не найден — установить Docker Engine с плагином compose"
    docker compose version >/dev/null 2>&1 || die "нет плагина docker compose (нужен Compose v2)"
}

require_venv() {
    [ -x "$VENV_PY" ] || die "нет venv — сначала ./install.sh (или ./install_offline.sh)"
}

# Ждёт GET /health: 60 попыток по 2 секунды.
wait_health() {
    local url="http://localhost:$1/health" i
    say "жду готовности API: $url"
    for i in $(seq 1 60); do
        if command -v curl >/dev/null 2>&1; then
            curl -fsS -o /dev/null --max-time 5 "$url" 2>/dev/null && { ok "API отвечает: $url"; return 0; }
        else
            wget -q -O /dev/null -T 5 "$url" 2>/dev/null && { ok "API отвечает: $url"; return 0; }
        fi
        sleep 2
    done
    die "API не ответил за 120 с. Docker: docker compose logs api; локально: run/api.log"
}

# Модули из src/ импортируются верхним уровнем (import config, queueing.celery_app).
export_pythonpath() {
    export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
}

run_migrate() {
    require_venv
    export_pythonpath
    step 1 "Схема БД"
    "$VENV_PY" scripts/db/init_db.py
    step 2 "Импорт файлового хранилища в БД (повторный запуск безопасен)"
    "$VENV_PY" scripts/db/migrate_from_files.py \
        || die "сверка миграции не сошлась — смотреть таблицу выше; сервис не запускаю"
}

# start_bg ИМЯ команда...: фоновый процесс, pid в run/ИМЯ.pid, лог в run/ИМЯ.log.
start_bg() {
    local name="$1" pidfile="run/$1.pid"
    shift
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        warn "$name уже запущен (pid $(cat "$pidfile")) — пропускаю"
        return 0
    fi
    nohup "$@" >>"run/$name.log" 2>&1 &
    echo $! >"$pidfile"
    say "$name: pid $!, лог run/$name.log"
}

start_docker() {
    require_docker
    ensure_env
    local profile=()
    if [ "$TRAINING" = "1" ]; then profile=(--profile training); fi
    banner "Запуск стека в Docker"
    docker compose ${profile[@]+"${profile[@]}"} up -d --build
    wait_health "$(env_value HOST_PORT 8010)"
    say "бэк-офис: http://localhost:$(env_value HOST_PORT 8010)/backoffice"
}

start_local() {
    require_docker
    require_venv
    ensure_env
    banner "Локальный запуск: инфраструктура в Docker, сервис из venv"
    step 0 "PostgreSQL и RabbitMQ"
    docker compose up -d --wait postgres rabbitmq
    run_migrate

    local port host concurrency
    port="${PORT_ARG:-$(env_value API_PORT 8010)}"
    host="$(env_value API_HOST 0.0.0.0)"
    concurrency="$(env_value CLASSIFY_CONCURRENCY 2)"
    mkdir -p run

    step 3 "API и воркеры"
    start_bg api "$VENV_PY" -m uvicorn src.api_server:app --host "$host" --port "$port"
    # prefork: один процесс-мастер, CLASSIFY_CONCURRENCY дочерних классификаций.
    start_bg worker-classify "$VENV_PY" -m celery -A queueing.celery_app worker \
        -Q classify_sync,classify --pool=prefork -c "$concurrency" -n "classify@%h" --loglevel=INFO
    if [ "$TRAINING" = "1" ]; then
        start_bg worker-training "$VENV_PY" -m celery -A queueing.celery_app worker \
            -Q training --pool=prefork -c 1 -n "training@%h" --loglevel=INFO
    fi
    wait_health "$port"
    say "бэк-офис: http://localhost:$port/backoffice   остановка: ./stop.sh local"
}

case "$MODE" in
    docker) start_docker ;;
    local) start_local ;;
    migrate) ensure_env; run_migrate; ok "миграция завершена" ;;
esac

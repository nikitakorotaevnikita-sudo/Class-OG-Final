#!/usr/bin/env bash
# Запуск классификатора на Linux. Аналог start.cmd.
#
#   ./start.sh [docker]             весь стек в Docker (по умолчанию)
#   ./start.sh local [--port N]     PostgreSQL и RabbitMQ в Docker, API и воркеры из venv
#   ./start.sh migrate              только схема БД + импорт файлов (БД уже должна работать)
#
#   --training                      дополнительно поднять воркер дообучения
#   (--port только для local; в docker порт API задаёт HOST_PORT в .env)
#
# Остановка: ./stop.sh [docker|local]. Логи локального режима — run/*.log.

set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

MODE="docker"
TRAINING=0
PORT_ARG=""

usage() { sed -n '2,11p' "$0"; }

while [ $# -gt 0 ]; do
    case "$1" in
        docker|local|migrate) MODE="$1" ;;
        --training) TRAINING=1 ;;
        --port)
            [ $# -ge 2 ] || { printf 'После --port нужен номер порта\n' >&2; exit 2; }
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
    # CLI на месте, но движок может быть остановлен или зависнуть
    # (Docker Desktop отвечает "Internal Server Error for API route ...").
    docker info >/dev/null 2>&1 || die "движок Docker не отвечает: запустить (или перезапустить) Docker Desktop / сервис docker и повторить"
}

require_venv() {
    [ -x "$VENV_PY" ] || die "нет venv — сначала ./install.sh (или ./install_offline.sh)"
}

# Ждёт GET /health: 60 попыток по 2 секунды. /health всегда отвечает 200 —
# готовность определяется по телу: нужны "db":true и "broker":true.
wait_health() {
    local url="http://localhost:$1/health" i body seen=0 db=0 mq=0
    say "жду готовности API: $url"
    for i in $(seq 1 60); do
        if command -v curl >/dev/null 2>&1; then
            body="$(curl -fsS --max-time 5 "$url" 2>/dev/null)" || body=""
        else
            body="$(wget -q -O - -T 5 "$url" 2>/dev/null)" || body=""
        fi
        if [ -n "$body" ]; then
            seen=1
            case "$body" in *'"db":true'*) db=1 ;; *) db=0 ;; esac
            case "$body" in *'"broker":true'*) mq=1 ;; *) mq=0 ;; esac
            if [ "$db$mq" = "11" ]; then
                ok "API отвечает, БД и брокер доступны: $url"
                return 0
            fi
        fi
        sleep 2
    done
    if [ "$seen" = "1" ]; then
        [ "$db" = "1" ] || warn "API не видит PostgreSQL (\"db\": false)"
        [ "$mq" = "1" ] || warn "API не видит RabbitMQ (\"broker\": false)"
        die "API отвечает, но не готов за 120 с. Docker: docker compose logs api; локально: run/api.log"
    fi
    die "API не ответил за 120 с. Docker: docker compose logs api; локально: run/api.log"
}

# Значение: переменная окружения, иначе активная строка .env, иначе по умолчанию.
env_or_file() {
    local current="${!1:-}"
    if [ -n "$current" ]; then
        printf '%s\n' "$current"
    else
        env_value "$1" "$2"
    fi
}

# host:port из URL вида scheme://user:pass@host:port/...; без порта — порт по умолчанию.
url_hostport() {
    local rest="${1#*://}"
    rest="${rest##*@}"
    rest="${rest%%/*}"
    case "$rest" in
        *:*) printf '%s\n' "$rest" ;;
        *) printf '%s:%s\n' "$rest" "$2" ;;
    esac
}

# DATABASE_URL / RABBITMQ_URL для local и migrate. Не заданы (ни в окружении, ни в
# .env) — собираются из учётных данных compose и портов POSTGRES_PORT /
# RABBITMQ_PORT: иначе config взял бы localhost:5432/5672, где может работать
# чужой («родной») PostgreSQL или RabbitMQ. Заданы, но порт расходится с
# POSTGRES_PORT / RABBITMQ_PORT — остановка с понятной ошибкой.
resolve_service_urls() {
    local db_url mq_url pg_port_set mq_port_set db_hp mq_hp
    db_url="$(env_or_file DATABASE_URL "")"
    mq_url="$(env_or_file RABBITMQ_URL "")"
    pg_port_set="$(env_or_file POSTGRES_PORT "")"
    mq_port_set="$(env_or_file RABBITMQ_PORT "")"

    if [ -z "$db_url" ]; then
        db_url="postgresql://$(env_or_file POSTGRES_USER classifier):$(env_or_file POSTGRES_PASSWORD classifier)@localhost:${pg_port_set:-5432}/$(env_or_file POSTGRES_DB classifier)"
    fi
    db_hp="$(url_hostport "$db_url" 5432)"
    if [ -n "$pg_port_set" ] && [ "${db_hp##*:}" != "$pg_port_set" ]; then
        die "DATABASE_URL указывает на порт ${db_hp##*:}, а POSTGRES_PORT=$pg_port_set — исправить одно из них в .env (иначе подключение уйдёт к другому PostgreSQL)"
    fi

    if [ -z "$mq_url" ]; then
        mq_url="amqp://$(env_or_file RABBITMQ_DEFAULT_USER classifier):$(env_or_file RABBITMQ_DEFAULT_PASS classifier)@localhost:${mq_port_set:-5672}//"
    fi
    mq_hp="$(url_hostport "$mq_url" 5672)"
    if [ -n "$mq_port_set" ] && [ "${mq_hp##*:}" != "$mq_port_set" ]; then
        die "RABBITMQ_URL указывает на порт ${mq_hp##*:}, а RABBITMQ_PORT=$mq_port_set — исправить одно из них в .env (иначе подключение уйдёт к другому RabbitMQ)"
    fi

    export DATABASE_URL="$db_url" RABBITMQ_URL="$mq_url"
    say "PostgreSQL: $db_hp   RabbitMQ: $mq_hp"
}

# src/ — корень импорта, модули разложены по пакетам (from core import config, queueing.celery_app).
export_pythonpath() {
    export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
}

# run_migrate ПЕРВЫЙ_ШАГ ВСЕГО_ШАГОВ — два шага: схема и импорт (номера в выводе
# сквозные: local — 2/4 и 3/4, migrate — 1/2 и 2/2).
run_migrate() {
    local first="${1:-1}" total="${2:-2}"
    require_venv
    export_pythonpath
    resolve_service_urls
    step "$first/$total" "Схема БД"
    "$VENV_PY" scripts/db/init_db.py
    first=$((first + 1))
    step "$first/$total" "Импорт файлового хранилища в БД (после успешного переноса — пропуск)"
    "$VENV_PY" scripts/db/migrate_from_files.py \
        || die "сверка миграции не сошлась — смотреть таблицу выше; сервис не запускаю"
}

# Кто-то уже слушает 127.0.0.1:ПОРТ (например, api docker-стека на 8010)?
port_in_use() {
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# Наш процесс из pid-файла жив (номер не перешёл чужому процессу)?
pidfile_alive() {
    [ -f "run/$1.pid" ] && pid_is_classifier "$(cat "run/$1.pid" 2>/dev/null)"
}

# start_bg ИМЯ команда...: фоновый процесс, pid в run/ИМЯ.pid, лог в run/ИМЯ.log.
start_bg() {
    local name="$1" pidfile="run/$1.pid"
    shift
    if [ -f "$pidfile" ] && pid_is_classifier "$(cat "$pidfile" 2>/dev/null)"; then
        warn "$name уже запущен (pid $(cat "$pidfile")) — пропускаю"
        return 0
    fi
    # pid-файл от прошлого запуска: процесса нет или номер занят чужим процессом.
    rm -f "$pidfile"
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
    if [ -n "$PORT_ARG" ]; then
        warn "--port $PORT_ARG в режиме docker не действует: порт API задаёт HOST_PORT в .env ($(env_value HOST_PORT 8010))"
    fi
    docker compose ${profile[@]+"${profile[@]}"} up -d --build
    wait_health "$(env_value HOST_PORT 8010)"
    say "бэк-офис: http://localhost:$(env_value HOST_PORT 8010)/backoffice"
}

start_local() {
    require_docker
    require_venv
    ensure_env
    banner "Локальный запуск: инфраструктура в Docker, сервис из venv"
    resolve_service_urls  # до docker compose: ошибка портов — сразу
    step "1/4" "PostgreSQL и RabbitMQ"
    docker compose up -d --wait postgres rabbitmq
    run_migrate 2 4

    local port host concurrency
    port="${PORT_ARG:-$(env_value API_PORT 8010)}"
    host="$(env_value API_HOST 0.0.0.0)"
    concurrency="$(env_value CLASSIFY_CONCURRENCY 2)"
    mkdir -p run

    step "4/4" "API и воркеры"
    # Порт занят не нашим API (обычно — api docker-стека: ./start.sh публикует
    # HOST_PORT=8010): uvicorn упал бы в фоне, а /health ответил бы чужой сервис.
    if ! pidfile_alive api && port_in_use "$port"; then
        die "порт $port уже занят. Если запущен docker-стек — остановить: ./stop.sh; иначе другой порт: ./start.sh local --port N"
    fi
    start_bg api "$VENV_PY" -m uvicorn api.api_server:app --app-dir src --host "$host" --port "$port"
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

# Подключение через `source` (тесты функций) — без запуска.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

case "$MODE" in
    docker) start_docker ;;
    local) start_local ;;
    migrate) ensure_env; run_migrate 1 2; ok "миграция завершена" ;;
esac

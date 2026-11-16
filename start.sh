#!/usr/bin/env bash
# Запуск классификатора на Linux. Аналог start.cmd.
#
#   ./start.sh [all] [--port N]     PostgreSQL в Docker, API и воркеры из venv (по умолчанию;
#                                   local — прежнее имя этого режима)
#   ./start.sh api [--port N]       PostgreSQL в Docker, API — без воркеров (воркеры на других машинах)
#   ./start.sh worker               только воркеры; база — DATABASE_URL из .env (другая машина)
#   ./start.sh docker [--no-workers]  весь стек в Docker (--no-workers: postgres, migrate, api)
#   ./start.sh migrate              только схема БД + импорт файлов (БД уже должна работать)
#
#   --training                      дополнительно поднять воркер дообучения (all, worker, docker)
#   (--port для all/api; в docker порт API задаёт HOST_PORT в .env)
#
# Остановка: ./stop.sh [all|api|worker|docker]. Логи процессов из venv — run/*.log.

set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

MODE="all"
TRAINING=0
NO_WORKERS=0
PORT_ARG=""

usage() { sed -n '2,15p' "$0"; }

while [ $# -gt 0 ]; do
    case "$1" in
        all|api|worker|docker|migrate) MODE="$1" ;;
        local) MODE=all ;;
        --training) TRAINING=1 ;;
        --no-workers) NO_WORKERS=1 ;;
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

# wait_health ПОРТ НУЖНЫ_ВОРКЕРЫ — ждёт GET /health. /health всегда отвечает 200 —
# готовность определяется по телу: "db":true, а при НУЖНЫ_ВОРКЕРЫ=1 ещё и
# "agent_ready":true (воркер классификации загрузил модель и слушает очередь;
# поэтому и ожидание дольше: 150 попыток по 2 с против 60).
# НУЖНЫ_ВОРКЕРЫ=0 (режимы api и docker --no-workers) — только БД и предупреждение.
wait_health() {
    local url="http://localhost:$1/health" need_agent="${2:-1}" tries=60 i body
    local seen=0 db=0 agent=0
    if [ "$need_agent" = "1" ]; then tries=150; fi
    say "жду готовности API: $url"
    for i in $(seq 1 "$tries"); do
        if command -v curl >/dev/null 2>&1; then
            body="$(curl -fsS --max-time 5 "$url" 2>/dev/null)" || body=""
        else
            body="$(wget -q -O - -T 5 "$url" 2>/dev/null)" || body=""
        fi
        if [ -n "$body" ]; then
            seen=1
            case "$body" in *'"db":true'*) db=1 ;; *) db=0 ;; esac
            case "$body" in *'"agent_ready":true'*) agent=1 ;; *) agent=0 ;; esac
            if [ "$db" = "1" ] && [ "$need_agent" != "1" ]; then
                ok "API отвечает, БД доступна: $url"
                warn "воркеров нет — задачи копятся в очереди, пока не подключится воркер (./start.sh worker на машине воркеров)"
                return 0
            fi
            if [ "$db$agent" = "11" ]; then
                ok "API отвечает, БД доступна, воркер классификации готов: $url"
                return 0
            fi
        fi
        sleep 2
    done
    if [ "$seen" = "1" ]; then
        [ "$db" = "1" ] || warn "API не видит PostgreSQL (\"db\": false)"
        if [ "$need_agent" = "1" ] && [ "$agent" != "1" ]; then
            warn "нет готового воркера классификации (\"agent_ready\": false) — смотреть run/worker-classify-*.log или docker compose logs worker-classify"
        fi
        die "API отвечает, но не готов за $((tries * 2)) с. Docker: docker compose logs api; локально: run/api.log"
    fi
    die "API не ответил за $((tries * 2)) с. Docker: docker compose logs api; локально: run/api.log"
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

# DATABASE_URL для all, api и migrate. Не задан (ни в окружении, ни в .env) —
# собирается из учётных данных compose и порта POSTGRES_PORT: иначе config взял
# бы localhost:5432, где может работать чужой («родной») PostgreSQL. Задан, но
# порт расходится с POSTGRES_PORT — остановка с понятной ошибкой.
# В режиме worker не применяется: там адрес задан явно, обычно на другой хост.
resolve_service_urls() {
    local db_url pg_port_set db_hp
    db_url="$(env_or_file DATABASE_URL "")"
    pg_port_set="$(env_or_file POSTGRES_PORT "")"

    if [ -z "$db_url" ]; then
        db_url="postgresql://$(env_or_file POSTGRES_USER classifier):$(env_or_file POSTGRES_PASSWORD classifier)@localhost:${pg_port_set:-5432}/$(env_or_file POSTGRES_DB classifier)"
    fi
    db_hp="$(url_hostport "$db_url" 5432)"
    if [ -n "$pg_port_set" ] && [ "${db_hp##*:}" != "$pg_port_set" ]; then
        die "DATABASE_URL указывает на порт ${db_hp##*:}, а POSTGRES_PORT=$pg_port_set — исправить одно из них в .env (иначе подключение уйдёт к другому PostgreSQL)"
    fi

    export DATABASE_URL="$db_url"
    say "PostgreSQL: $db_hp"
}

# src/ — корень импорта, модули разложены по пакетам (from core import config, worker.run).
export_pythonpath() {
    export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
}

# run_migrate ПЕРВЫЙ_ШАГ ВСЕГО_ШАГОВ — два шага: схема и импорт (номера в выводе
# сквозные: all/api — 2/4 и 3/4, migrate — 1/2 и 2/2).
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

# Воркеры очереди из venv: CLASSIFY_WORKERS процессов классификации (один процесс —
# одна задача, модель загружается один раз на процесс) и воркер дообучения при --training.
start_workers() {
    local workers i
    workers="$(env_value CLASSIFY_WORKERS 2)"
    case "$workers" in ''|*[!0-9]*) workers=2 ;; esac
    if [ "$workers" -lt 1 ]; then workers=1; fi
    mkdir -p run
    export_pythonpath
    for i in $(seq 1 "$workers"); do
        start_bg "worker-classify-$i" "$VENV_PY" -m worker.run --queues classify_sync,classify
    done
    if [ "$TRAINING" = "1" ]; then
        start_bg worker-training "$VENV_PY" -m worker.run --queues training
    fi
}

start_docker() {
    require_docker
    ensure_env
    local profile=() need_agent=1
    if [ "$TRAINING" = "1" ]; then profile=(--profile training); fi
    banner "Запуск стека в Docker"
    if [ -n "$PORT_ARG" ]; then
        warn "--port $PORT_ARG в режиме docker не действует: порт API задаёт HOST_PORT в .env ($(env_value HOST_PORT 8010))"
    fi
    if [ "$NO_WORKERS" = "1" ]; then
        if [ "$TRAINING" = "1" ]; then warn "--training вместе с --no-workers не действует"; fi
        need_agent=0
        docker compose up -d --build postgres migrate api
    else
        docker compose ${profile[@]+"${profile[@]}"} up -d --build
    fi
    wait_health "$(env_value HOST_PORT 8010)" "$need_agent"
    say "бэк-офис: http://localhost:$(env_value HOST_PORT 8010)/backoffice"
}

# Режимы all и api: PostgreSQL в Docker, миграция, API из venv; в all — и воркеры.
start_local() {
    require_docker
    require_venv
    ensure_env
    local port host need_agent=0
    if [ "$MODE" = "all" ]; then
        banner "Запуск всего: PostgreSQL в Docker, API и воркеры из venv"
    else
        banner "Запуск без воркеров: PostgreSQL в Docker, API из venv"
        if [ "$TRAINING" = "1" ]; then warn "--training в режиме api не действует (воркеров нет)"; fi
    fi
    resolve_service_urls  # до docker compose: ошибка порта — сразу
    step "1/4" "PostgreSQL"
    docker compose up -d --wait postgres
    run_migrate 2 4

    port="${PORT_ARG:-$(env_value API_PORT 8010)}"
    host="$(env_value API_HOST 0.0.0.0)"
    mkdir -p run

    if [ "$MODE" = "all" ]; then
        step "4/4" "API и воркеры"
    else
        step "4/4" "API"
    fi
    # Порт занят не нашим API (обычно — api docker-стека: ./start.sh docker публикует
    # HOST_PORT=8010): uvicorn упал бы в фоне, а /health ответил бы чужой сервис.
    if ! pidfile_alive api && port_in_use "$port"; then
        die "порт $port уже занят. Если запущен docker-стек — остановить: ./stop.sh docker; иначе другой порт: ./start.sh $MODE --port N"
    fi
    start_bg api "$VENV_PY" -m uvicorn api.api_server:app --app-dir src --host "$host" --port "$port"
    if [ "$MODE" = "all" ]; then
        need_agent=1
        start_workers
    fi
    wait_health "$port" "$need_agent"
    say "бэк-офис: http://localhost:$port/backoffice   остановка: ./stop.sh $MODE"
}

# Режим worker: только воркеры, без Docker, миграции и API. Адрес базы — в .env
# этой машины (или в окружении), обычно на другом хосте; сверка с POSTGRES_PORT
# здесь не делается. Скрипт адрес не читает и не печатает: проверку делает
# scripts/check_db.py, воркеры читают .env сами (core.config).
start_worker_mode() {
    require_venv
    ensure_env
    banner "Только воркеры: база данных на главном сервере"
    if [ -n "$PORT_ARG" ]; then
        warn "--port $PORT_ARG в режиме worker не действует: у воркеров нет HTTP-порта"
    fi
    export_pythonpath
    "$VENV_PY" scripts/check_db.py check \
        || die "проверка базы не прошла — причина выше; исправить адрес базы в .env этой машины (postgresql://пользователь:пароль@хост:порт/база)"
    start_workers
    say "логи: run/worker-*.log   остановка: ./stop.sh worker"
}

# Подключение через `source` (тесты функций) — без запуска.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

if [ "$NO_WORKERS" = "1" ] && [ "$MODE" != "docker" ]; then
    warn "--no-workers действует только в режиме docker (без воркеров из venv: ./start.sh api)"
fi

case "$MODE" in
    all) start_local ;;
    api) start_local ;;
    worker) start_worker_mode ;;
    docker) start_docker ;;
    migrate) ensure_env; run_migrate 1 2; ok "миграция завершена" ;;
esac

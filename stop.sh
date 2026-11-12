#!/usr/bin/env bash
# Остановка классификатора на Linux. Аналог stop.cmd.
#
#   ./stop.sh [docker]   остановить и удалить контейнеры стека (по умолчанию)
#   ./stop.sh local      остановить API и воркеры из run/*.pid, затем PostgreSQL и RabbitMQ
#
# Том pgdata НЕ удаляется никогда: в нём тексты обращений (ПДн по 59-ФЗ),
# результаты, верификации и реестр моделей. Поэтому ключ -v у down не используется.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

MODE="${1:-docker}"

# Мягкая остановка (TERM): uvicorn и celery дорабатывают текущее. Через 30 с — KILL.
# Задача, прерванная KILL, не потеряется: acks_late вернёт её в очередь.
stop_pidfile() {
    local pidfile="$1" name pid i
    name="$(basename "$pidfile" .pid)"
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        say "$name: процесс не найден"
        rm -f "$pidfile"
        return 0
    fi
    # Номер из pid-файла после перезагрузки мог достаться чужому процессу:
    # убиваем только наш API / воркер (по командной строке).
    if ! pid_is_classifier "$pid"; then
        warn "$name: pid $pid принадлежит другому процессу — не трогаю, pid-файл удалён"
        rm -f "$pidfile"
        return 0
    fi
    kill -TERM "$pid" 2>/dev/null || true
    for i in $(seq 1 30); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        warn "$name (pid $pid) не завершился за 30 с — KILL"
        kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
    ok "$name остановлен"
}

stop_local() {
    local found=0 pidfile
    for pidfile in run/*.pid; do
        [ -e "$pidfile" ] || continue
        found=1
        stop_pidfile "$pidfile"
    done
    [ "$found" = "1" ] || say "pid-файлов в run/ нет — локальные процессы не запускались"
    if command -v docker >/dev/null 2>&1; then
        # stop, а не down: контейнеры и том с данными остаются.
        docker compose stop postgres rabbitmq
    fi
}

stop_docker() {
    command -v docker >/dev/null 2>&1 || die "docker не найден"
    # --profile training — чтобы остановить и воркер дообучения, если он поднят.
    # Без -v: том pgdata с ПДн обязан пережить остановку. Удалять его — только
    # осознанно и вручную (docker volume rm), после выгрузки данных.
    docker compose --profile training down
}

case "$MODE" in
    docker) stop_docker ;;
    local) stop_local ;;
    -h|--help) sed -n '2,8p' "$0" ;;
    *) printf 'Неизвестный режим: %s (docker | local)\n' "$MODE" >&2; exit 2 ;;
esac

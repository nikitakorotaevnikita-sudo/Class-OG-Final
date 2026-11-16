#!/usr/bin/env bash
# Остановка классификатора на Linux. Аналог stop.cmd.
#
#   ./stop.sh [all]      остановить API и воркеры из run/*.pid, затем контейнеры проекта
#                        (docker compose stop; по умолчанию; local и api — то же самое)
#   ./stop.sh worker     остановить только воркеры из run/worker-*.pid (машина воркеров)
#   ./stop.sh docker     остановить и удалить контейнеры стека
#
# Том pgdata НЕ удаляется никогда: в нём тексты обращений (ПДн по 59-ФЗ),
# результаты, верификации и реестр моделей. Поэтому ключ -v у down не используется.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

MODE="${1:-all}"

# Мягкая остановка (TERM): uvicorn и воркер дорабатывают текущее. Через 30 с — KILL.
# Задача, прерванная KILL, не потеряется: по истечении аренды её возьмёт другой воркер.
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

# stop_pidfiles ШАБЛОН — все run/ШАБЛОН.pid; 1, если ни одного не было.
stop_pidfiles() {
    local found=1 pidfile
    for pidfile in run/$1.pid; do
        [ -e "$pidfile" ] || continue
        found=0
        stop_pidfile "$pidfile"
    done
    return "$found"
}

stop_local() {
    stop_pidfiles '*' || say "pid-файлов в run/ нет — процессы из venv не запускались"
    if command -v docker >/dev/null 2>&1; then
        # stop, а не down: контейнеры и том с данными остаются. Все сервисы
        # проекта — и postgres режимов all/api, и стек, поднятый ./start.sh docker.
        docker compose --profile training stop
    fi
}

# Только воркеры этой папки проекта (run/worker-*.pid); API и Docker не трогаются.
stop_workers() {
    stop_pidfiles 'worker-*' || say "pid-файлов воркеров в run/ нет — воркеры не запускались"
}

stop_docker() {
    command -v docker >/dev/null 2>&1 || die "docker не найден"
    # --profile training — чтобы остановить и воркер дообучения, если он поднят.
    # Без -v: том pgdata с ПДн обязан пережить остановку. Удалять его — только
    # осознанно и вручную (docker volume rm), после выгрузки данных.
    docker compose --profile training down
}

case "$MODE" in
    all|local|api) stop_local ;;
    worker) stop_workers ;;
    docker) stop_docker ;;
    -h|--help) sed -n '2,11p' "$0" ;;
    *) printf 'Неизвестный режим: %s (all | api | worker | docker)\n' "$MODE" >&2; exit 2 ;;
esac

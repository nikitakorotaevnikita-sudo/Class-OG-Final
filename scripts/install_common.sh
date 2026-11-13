# shellcheck shell=bash
# Общие функции установщиков Linux: install.sh и install_offline.sh.
# Подключается через `source scripts/install_common.sh` из корня проекта.

# Цвета только когда вывод идёт в терминал — в логе systemd/CI они мешают.
if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
else
    C_OK=''; C_WARN=''; C_ERR=''; C_OFF=''
fi

ENV_FILE="${ENV_FILE:-.env}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"

say()  { printf '   %s\n' "$*"; }
ok()   { printf '   %sOK%s: %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '   %sWARN%s: %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '\n   %sERROR%s: %s\n\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

step() {
    printf '\n[%s] %s\n' "$1" "$2"
}

banner() {
    printf '\n=====================================================\n'
    printf '  %s\n' "$1"
    printf '=====================================================\n'
}

# ── Python ────────────────────────────────────────────────────────────────────

# Ищет интерпретатор 3.11/3.12/3.13. На 3.14 нет колёс sentence-transformers,
# на 3.10 и ниже проект не проверялся.
find_python() {
    local candidate version
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        version=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || continue
        case "$version" in
            3.11|3.12|3.13)
                PYTHON="$candidate"
                PYTHON_VERSION="$version"
                return 0
                ;;
        esac
    done
    return 1
}

# ── Виртуальное окружение ─────────────────────────────────────────────────────

# venv, скопированный с другой машины, нерабочий: лаунчеры в bin/ содержат
# абсолютный путь к создавшему их интерпретатору. Проверяем и пересоздаём.
ensure_venv() {
    if [ -x venv/bin/python ]; then
        if venv/bin/python -m pip --version >/dev/null 2>&1; then
            ok "venv на месте и работает"
            return 0
        fi
        warn "существующий venv нерабочий — пересоздаю"
        rm -rf venv
    fi

    if ! "$PYTHON" -m venv venv 2>/tmp/venv-error.log; then
        say "$(cat /tmp/venv-error.log)"
        die "не удалось создать venv.
   На Debian/Ubuntu модуль venv лежит в отдельном пакете:
       sudo apt install python${PYTHON_VERSION}-venv
   На RHEL-совместимых (Astra, RedOS, ALT):
       sudo dnf install python3-virtualenv"
    fi
    [ -x venv/bin/python ] || die "venv создан, но venv/bin/python отсутствует"
    ok "venv создан ($PYTHON, $PYTHON_VERSION)"
}

VENV_PY="venv/bin/python"

# ── .env ──────────────────────────────────────────────────────────────────────

# Заменяет активную строку KEY=... либо дописывает её в конец.
# Через awk, а не sed: значения содержат / и & — в sed их пришлось бы экранировать.
set_env_key() {
    local key="$1" value="$2"
    touch "$ENV_FILE"
    awk -v k="$key" -v v="$value" '
        index($0, k "=") == 1 { print k "=" v; found = 1; next }
        { print }
        END { if (!found) print k "=" v }
    ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
}

# Значение активной строки KEY= из .env (пусто, если строки нет).
get_env_key() {
    local key="$1"
    [ -f "$ENV_FILE" ] || return 0
    awk -v k="$key" 'index($0, k "=") == 1 { sub("^" k "=", ""); print; exit }' "$ENV_FILE"
}

# ask VAR "приглашение" ["значение по умолчанию"]
# В неинтерактивном режиме берёт уже установленную переменную окружения
# или значение по умолчанию — так скрипт годится и для CI.
ask() {
    local var="$1" prompt="$2" default="${3:-}" current answer
    eval "current=\${$var:-}"
    if [ -n "$current" ]; then
        say "$prompt: $current (из окружения)"
        return 0
    fi
    if [ "$NONINTERACTIVE" = "1" ]; then
        eval "$var=\$default"
        say "$prompt: ${default:-<пусто>}"
        return 0
    fi
    if [ -n "$default" ]; then
        printf '   %s [%s]: ' "$prompt" "$default"
    else
        printf '   %s: ' "$prompt"
    fi
    IFS= read -r answer || answer=''
    [ -n "$answer" ] || answer="$default"
    eval "$var=\$answer"
}

# ── Векторная база ────────────────────────────────────────────────────────────

# Каталог базы из .env; по умолчанию data/vector_db.
vdb_dir() {
    local configured
    configured="$(get_env_key VECTOR_DB_DIR)"
    if [ -n "$configured" ]; then
        printf '%s\n' "$configured"
    else
        printf 'data/vector_db\n'
    fi
}

# ── Проверки окружения ───────────────────────────────────────────────────────

require_project_root() {
    [ -f requirements.txt ] && [ -d src ] \
        || die "запускать из корня проекта (рядом должны быть requirements.txt и src/)"
}

warn_if_root() {
    [ "$(id -u)" = "0" ] || return 0
    warn "скрипт запущен от root — файлы проекта получат владельца root.
        Если сервис будет работать под отдельной учётной записью,
        установку лучше делать от неё же."
}

# ── Процессы из run/*.pid ────────────────────────────────────────────────────

# Командная строка процесса (пусто, если его нет). /proc есть в Linux и в Git Bash.
pid_cmdline() {
    local pid="$1"
    if [ -r "/proc/$pid/cmdline" ]; then
        tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true
        return 0
    fi
    ps -o args= -p "$pid" 2>/dev/null || true
}

# pid из run/*.pid — это наш API или воркер? После перезагрузки тот же номер
# может достаться чужому процессу: «узнавать» и убивать его нельзя.
pid_is_classifier() {
    local pid="$1" cmd
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    kill -0 "$pid" 2>/dev/null || return 1
    cmd="$(pid_cmdline "$pid")"
    case "$cmd" in
        *api_server:app*|*queueing.celery_app*) return 0 ;;
    esac
    return 1
}

# ── Инфраструктура: PostgreSQL + RabbitMQ в Docker ──────────────────────────

INFRA_IMAGES="pgvector/pgvector:pg16 rabbitmq:3.13-management"

# Подсказка, когда поднять инфраструктуру сейчас не вышло.
infra_later() {
    say "позже, когда Docker будет работать: ./start.sh migrate"
    say "(или сразу ./launch.sh server — ./start.sh local поднимет PostgreSQL и RabbitMQ сам)"
}

# setup_infra [--offline]: свободные порты в .env (scripts/setup_infra_env.py),
# docker compose up postgres rabbitmq с ожиданием healthcheck, миграция
# (./start.sh migrate; повторный запуск — по отметке в БД, без переноса).
# Нет Docker или он не запущен — не ошибка установки: подсказка и выход.
# --offline: образов нет локально — как их загрузить, без попытки скачать.
# Сбой compose или миграции отмечается в CHECK_FAILED.
setup_infra() {
    local offline="${1:-}"
    if ! "$VENV_PY" scripts/setup_infra_env.py --env-file "$ENV_FILE"; then
        warn "порты PostgreSQL/RabbitMQ не выбраны — см. ошибку выше, исправить POSTGRES_PORT / RABBITMQ_PORT / RABBITMQ_MGMT_PORT в .env и повторить"
        CHECK_FAILED=1
        return 0
    fi
    if ! command -v docker >/dev/null 2>&1; then
        warn "Docker не установлен — PostgreSQL и RabbitMQ не подняты (нужен Docker Engine с плагином compose)"
        infra_later
        return 0
    fi
    if ! docker info >/dev/null 2>&1; then
        warn "Docker Engine не запущен (или нет прав на docker.sock) — PostgreSQL и RabbitMQ не подняты"
        infra_later
        return 0
    fi
    if ! docker compose version >/dev/null 2>&1; then
        warn "нет плагина docker compose (нужен Compose v2) — PostgreSQL и RabbitMQ не подняты"
        infra_later
        return 0
    fi
    if [ "$offline" = "--offline" ]; then
        # shellcheck disable=SC2086
        if ! docker image inspect $INFRA_IMAGES >/dev/null 2>&1; then
            warn "нет образов $INFRA_IMAGES — без интернета их не скачать"
            say "на машине с интернетом:"
            say "    docker pull pgvector/pgvector:pg16 && docker pull rabbitmq:3.13-management"
            say "    docker save -o infra-images.tar $INFRA_IMAGES"
            say "здесь:  docker load -i infra-images.tar"
            infra_later
            return 0
        fi
    fi
    say "docker compose up -d --wait postgres rabbitmq"
    if ! docker compose up -d --wait postgres rabbitmq; then
        warn "PostgreSQL / RabbitMQ не поднялись — см. docker compose logs postgres rabbitmq"
        infra_later
        CHECK_FAILED=1
        return 0
    fi
    ok "PostgreSQL и RabbitMQ работают"
    if bash ./start.sh migrate; then
        ok "схема и перенос данных в PostgreSQL готовы"
    else
        warn "миграция не прошла — см. вывод выше; повторить: ./start.sh migrate"
        CHECK_FAILED=1
    fi
    return 0
}

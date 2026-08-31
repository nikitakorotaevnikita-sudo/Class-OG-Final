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

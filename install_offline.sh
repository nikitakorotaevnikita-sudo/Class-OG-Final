#!/usr/bin/env bash
# Установка на Linux-стенд БЕЗ интернета. Аналог install_offline.bat.
# Всё берётся из offline_bundle/: колёса, модель эмбеддингов, готовая база.
#
#   ./install_offline.sh
#   ./install_offline.sh --non-interactive
#
# Комплект собирается на машине с интернетом:
#   python scripts/make_offline_bundle.py --python-version 3.11 --platform manylinux2014_x86_64

set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PORT="${PORT:-8010}"
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --non-interactive) NONINTERACTIVE=1 ;;
        --force)           FORCE=1 ;;
        --port)            shift; PORT="${1:-8010}" ;;
        -h|--help)         sed -n '2,14p' "$0"; exit 0 ;;
        *)                 printf 'Неизвестный аргумент: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

export NONINTERACTIVE="${NONINTERACTIVE:-0}"
# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

banner "Class OG Final — установка без интернета (Linux)"
require_project_root
warn_if_root

[ -d offline_bundle/wheels ] || die "нет offline_bundle/wheels.
   Комплект собирается на машине С интернетом:
       python scripts/make_offline_bundle.py --python-version 3.11 --platform manylinux2014_x86_64"

# ── 1. Python ─────────────────────────────────────────────────────────────────
step "1/6" "Поиск Python 3.11 / 3.12 / 3.13"
find_python || die "не найден Python 3.11, 3.12 или 3.13"
ok "$PYTHON ($PYTHON_VERSION)"

# ── 2. Проверка комплекта ДО установки ────────────────────────────────────────
step "2/6" "Проверка комплекта"
# Колёса скомпилированных пакетов привязаны к ABI интерпретатора: комплект,
# собранный под 3.13, на 3.11 не встанет. Ловим это здесь, а не в середине pip.
if ! "$PYTHON" scripts/check_offline.py --stage pre; then
    if [ "$FORCE" = "1" ]; then
        warn "проверка не пройдена, продолжаю из-за --force"
    elif [ "$NONINTERACTIVE" = "1" ]; then
        die "проверка не пройдена. Проблемы с .env установщик исправит на шаге 4;
   для остальных случаев пересобрать комплект под нужный Python.
   Продолжить вопреки проверке: --force"
    else
        printf '\n   Проблемы с .env будут исправлены на шаге 4.\n'
        ask CONTINUE_ANYWAY "Продолжить? (y/n)" "n"
        case "$CONTINUE_ANYWAY" in
            y|Y|yes|да) : ;;
            *) die "установка прервана" ;;
        esac
    fi
fi

# ── 3. venv ───────────────────────────────────────────────────────────────────
step "3/6" "Виртуальное окружение"
ensure_venv

# ── 4. Зависимости из локальных колёс ─────────────────────────────────────────
step "4/6" "Зависимости из offline_bundle/wheels"
# --no-index гарантирует, что pip не пойдёт в сеть: если колеса не хватает,
# установка честно падает, а не «тихо» скачивает его с PyPI.
"$VENV_PY" -m pip install --no-index --find-links=offline_bundle/wheels --upgrade pip --quiet 2>/dev/null
if ! "$VENV_PY" -m pip install --no-index --find-links=offline_bundle/wheels -r requirements.txt; then
    die "установка из колёс не удалась.
   Обычная причина — колёса собраны под другой минор Python.
   Пересобрать на машине с интернетом:
       python scripts/make_offline_bundle.py --python-version $PYTHON_VERSION --platform manylinux2014_x86_64"
fi
ok "зависимости установлены, сеть не использовалась"

# ── 5. .env: офлайн-флаги, локальная модель, LLM в своей сети ────────────────
step "5/6" "Настройки (.env)"
if [ ! -f "$ENV_FILE" ]; then
    if [ -f offline_bundle/env.stand.example ]; then
        cp offline_bundle/env.stand.example "$ENV_FILE"
        say "создан .env из offline_bundle/env.stand.example"
    else
        cp .env.example "$ENV_FILE"
        say "создан .env из .env.example"
    fi
fi

if [ "$NONINTERACTIVE" != "1" ]; then
    printf '\n   Провайдер LLM. На изолированном стенде работают только свои:\n'
    printf '     [1] custom — OpenAI-совместимый endpoint в вашей сети\n'
    printf '     [2] ollama — модель на этой же машине\n'
    printf '     [3] ario   — Directum360, ТРЕБУЕТ ИНТЕРНЕТА\n\n'
    ask PROVIDER_CHOICE "Выбор (1-3)" "1"
    case "$PROVIDER_CHOICE" in
        2) LLM_PROVIDER=ollama ;;
        3) LLM_PROVIDER=ario ;;
        *) LLM_PROVIDER=custom ;;
    esac
fi
LLM_PROVIDER="${LLM_PROVIDER:-custom}"
set_env_key LLM_PROVIDER "$LLM_PROVIDER"
ok "провайдер: $LLM_PROVIDER"

case "$LLM_PROVIDER" in
    custom)
        ask CUSTOM_LLM_BASE_URL "Адрес endpoint (например http://10.0.0.5:8000/v1)" ""
        ask CUSTOM_LLM_MODEL    "Имя модели, как её отдаёт сервер" ""
        ask CUSTOM_LLM_API_KEY  "Ключ (Enter — без ключа)" ""
        [ -n "${CUSTOM_LLM_BASE_URL:-}" ] && set_env_key CUSTOM_LLM_BASE_URL "$CUSTOM_LLM_BASE_URL"
        [ -n "${CUSTOM_LLM_MODEL:-}" ]    && set_env_key CUSTOM_LLM_MODEL "$CUSTOM_LLM_MODEL"
        [ -n "${CUSTOM_LLM_API_KEY:-}" ]  && set_env_key CUSTOM_LLM_API_KEY "$CUSTOM_LLM_API_KEY"
        ;;
    ollama)
        ask OLLAMA_BASE_URL "OLLAMA_BASE_URL" "http://localhost:11434/v1"
        set_env_key OLLAMA_BASE_URL "$OLLAMA_BASE_URL"
        ;;
    ario)
        warn "ario недоступен без интернета — проверить связь после установки"
        ask ARIO_BASE_URL "ARIO_BASE_URL" "https://llm.ario.directum360.ru/v1"
        ask ARIO_API_KEY  "ARIO_API_KEY" ""
        set_env_key ARIO_BASE_URL "$ARIO_BASE_URL"
        [ -n "${ARIO_API_KEY:-}" ] && set_env_key ARIO_API_KEY "$ARIO_API_KEY"
        ;;
esac

printf '\n   Directum RX — только для вызовов по document_id.\n'
ask RX_ODATA_URL "RX OData URL" ""
ask RX_USER      "RX пользователь" ""
ask RX_PASSWORD  "RX пароль" ""
[ -n "${RX_ODATA_URL:-}" ] && set_env_key RX_ODATA_URL "$RX_ODATA_URL"
[ -n "${RX_USER:-}" ]      && set_env_key RX_USER "$RX_USER"
[ -n "${RX_PASSWORD:-}" ]  && set_env_key RX_PASSWORD "$RX_PASSWORD"

# Флаги офлайна и локальная модель — не спрашиваем, это суть офлайн-установки.
# HF_HUB_OFFLINE читается один раз, при импорте huggingface_hub: без него
# sentence-transformers пойдёт в сеть и упадёт с FileMetadataError, хотя файлы
# модели лежат рядом.
set_env_key HF_HUB_OFFLINE 1
set_env_key TRANSFORMERS_OFFLINE 1
set_env_key EMBEDDING_MODEL offline_bundle/models/multilingual-e5-base
set_env_key ENABLE_EMBEDDING_ADAPTER false
[ -f offline_bundle/models/multilingual-e5-base/config.json ] \
    || die "модель отсутствует в offline_bundle/models/ — пересобрать комплект"
chmod 600 "$ENV_FILE" 2>/dev/null
ok ".env настроен на локальную модель и запрет сети"

# ── 6. База + итоговая проверка ───────────────────────────────────────────────
step "6/6" "Векторная база и проверка"
VDB="$(vdb_dir)"
if [ -f "$VDB/embeddings.npy" ]; then
    ok "база на месте: $VDB"
elif [ -f offline_bundle/vector_db_prebuilt/embeddings.npy ]; then
    mkdir -p "$VDB"
    cp offline_bundle/vector_db_prebuilt/embeddings.npy \
       offline_bundle/vector_db_prebuilt/metadata.json "$VDB/"
    ok "готовая база скопирована в $VDB"
else
    say "готовой базы нет — считаю локальной моделью, 10-15 минут"
    "$VENV_PY" src/build_vectordb.py || die "не удалось собрать векторную базу"
fi

"$VENV_PY" scripts/check_offline.py --stage post || {
    warn "проверка нашла проблемы — см. выше"
    CHECK_FAILED=1
}

banner "Установка завершена"
printf '   Запуск:      ./launch.sh server %s\n' "$PORT"
printf '   Проверка:    curl http://127.0.0.1:%s/health\n\n' "$PORT"
exit "${CHECK_FAILED:-0}"

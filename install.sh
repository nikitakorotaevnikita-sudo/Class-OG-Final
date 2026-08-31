#!/usr/bin/env bash
# Установка прототипа классификатора обращений на Linux.
# Аналог install.bat: venv, зависимости, диалог по .env, проверка.
#
#   ./install.sh                    обычная установка (torch для CPU)
#   ./install.sh --gpu              torch с CUDA (образ втрое тяжелее)
#   ./install.sh --non-interactive  без вопросов: значения из окружения
#   ./install.sh --systemd          дополнительно собрать unit-файл
#
# Неинтерактивный режим читает те же имена, что и .env:
#   LLM_PROVIDER, ARIO_API_KEY, ARIO_BASE_URL, CUSTOM_LLM_BASE_URL,
#   CUSTOM_LLM_MODEL, CUSTOM_LLM_API_KEY, OLLAMA_BASE_URL,
#   RX_ODATA_URL, RX_USER, RX_PASSWORD, PORT

set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

TORCH_FLAVOUR=cpu
MAKE_SYSTEMD=0
PORT="${PORT:-8010}"

while [ $# -gt 0 ]; do
    case "$1" in
        --gpu)             TORCH_FLAVOUR=cuda ;;
        --non-interactive) NONINTERACTIVE=1 ;;
        --systemd)         MAKE_SYSTEMD=1 ;;
        --port)            shift; PORT="${1:-8010}" ;;
        -h|--help)         sed -n '2,20p' "$0"; exit 0 ;;
        *)                 printf 'Неизвестный аргумент: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

export NONINTERACTIVE="${NONINTERACTIVE:-0}"
# shellcheck source=scripts/install_common.sh
. scripts/install_common.sh

banner "Class OG Final — установка (Linux)"
require_project_root
warn_if_root

# ── 1. Python ─────────────────────────────────────────────────────────────────
step "1/6" "Поиск Python 3.11 / 3.12 / 3.13"
find_python || die "не найден Python 3.11, 3.12 или 3.13.
   Установить, например:  sudo apt install python3.11 python3.11-venv"
ok "$PYTHON ($PYTHON_VERSION)"

# ── 2. venv ───────────────────────────────────────────────────────────────────
step "2/6" "Виртуальное окружение"
ensure_venv

# ── 3. Зависимости ────────────────────────────────────────────────────────────
step "3/6" "Зависимости"
"$VENV_PY" -m pip install --upgrade pip --quiet || warn "pip не обновился, продолжаю"

if [ "$TORCH_FLAVOUR" = "cpu" ]; then
    # По умолчанию pip тянет сборку torch с CUDA — около 2.5 ГБ и бесполезную
    # на сервере без карты. CPU-сборка занимает порядка 200 МБ.
    say "torch: сборка для CPU (для CUDA запустить с --gpu)"
    "$VENV_PY" -m pip install torch --index-url https://download.pytorch.org/whl/cpu \
        || die "не удалось поставить torch для CPU"
fi

"$VENV_PY" -m pip install -r requirements.txt || die "установка зависимостей не удалась"
ok "зависимости установлены"

# ── 4. .env ───────────────────────────────────────────────────────────────────
step "4/6" "Настройки (.env)"
if [ ! -f "$ENV_FILE" ]; then
    cp .env.example "$ENV_FILE"
    say "создан .env из .env.example"
fi

if [ "$NONINTERACTIVE" != "1" ]; then
    printf '\n   Провайдер LLM:\n'
    printf '     [1] ario   — Directum360, Qwen3.6-35B-A3B (нужен доступ в интернет)\n'
    printf '     [2] custom — свой OpenAI-совместимый endpoint (vLLM, LM Studio, gpt-oss)\n'
    printf '     [3] ollama — модель на этой же машине\n'
    printf '     [4] groq / [5] gemini\n\n'
    ask PROVIDER_CHOICE "Выбор (1-5)" "1"
    case "$PROVIDER_CHOICE" in
        2) LLM_PROVIDER=custom ;;
        3) LLM_PROVIDER=ollama ;;
        4) LLM_PROVIDER=groq ;;
        5) LLM_PROVIDER=gemini ;;
        *) LLM_PROVIDER=ario ;;
    esac
fi
LLM_PROVIDER="${LLM_PROVIDER:-ario}"
set_env_key LLM_PROVIDER "$LLM_PROVIDER"
ok "провайдер: $LLM_PROVIDER"

case "$LLM_PROVIDER" in
    ario)
        ask ARIO_BASE_URL "ARIO_BASE_URL" "https://llm.ario.directum360.ru/v1"
        ask ARIO_API_KEY  "ARIO_API_KEY (Enter — оставить как есть)" ""
        set_env_key ARIO_BASE_URL "$ARIO_BASE_URL"
        [ -n "${ARIO_API_KEY:-}" ] && set_env_key ARIO_API_KEY "$ARIO_API_KEY"
        ;;
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
    groq)
        ask GROQ_API_KEY "GROQ_API_KEY (gsk_...)" ""
        [ -n "${GROQ_API_KEY:-}" ] && set_env_key GROQ_API_KEY "$GROQ_API_KEY"
        ;;
    gemini)
        ask GEMINI_API_KEY "GEMINI_API_KEY" ""
        [ -n "${GEMINI_API_KEY:-}" ] && set_env_key GEMINI_API_KEY "$GEMINI_API_KEY"
        ;;
esac

# Интеграция с RX: на основном пути RX присылает текст обращения, и креды
# не нужны вовсе. Они требуются только для вызовов по document_id.
printf '\n   Directum RX — только для вызовов по document_id.\n'
printf '   Если RX присылает текст обращения в запросе, оставить пустым.\n\n'
ask RX_ODATA_URL "RX OData URL" ""
ask RX_USER      "RX пользователь" ""
ask RX_PASSWORD  "RX пароль" ""
[ -n "${RX_ODATA_URL:-}" ] && set_env_key RX_ODATA_URL "$RX_ODATA_URL"
[ -n "${RX_USER:-}" ]      && set_env_key RX_USER "$RX_USER"
if [ -n "${RX_PASSWORD:-}" ]; then
    set_env_key RX_PASSWORD "$RX_PASSWORD"
else
    warn "пароль RX пуст — вызовы по document_id вернут 502"
fi

chmod 600 "$ENV_FILE" 2>/dev/null && ok ".env закрыт правами 600 (в нём ключи)"

# ── 5. Векторная база ─────────────────────────────────────────────────────────
step "5/6" "Векторная база"
VDB="$(vdb_dir)"
if [ -f "$VDB/embeddings.npy" ]; then
    ok "база на месте: $VDB"
elif [ -f offline_bundle/vector_db_prebuilt/embeddings.npy ]; then
    mkdir -p "$VDB"
    cp offline_bundle/vector_db_prebuilt/embeddings.npy \
       offline_bundle/vector_db_prebuilt/metadata.json "$VDB/"
    ok "готовая база скопирована из комплекта в $VDB"
else
    say "базы нет — собираю локально, это 10-15 минут"
    "$VENV_PY" src/build_vectordb.py || die "не удалось собрать векторную базу"
    ok "база собрана"
fi

# ── 6. Проверка ───────────────────────────────────────────────────────────────
step "6/6" "Проверка установки"
"$VENV_PY" scripts/check_offline.py --stage post --online || {
    warn "проверка нашла проблемы — см. выше"
    CHECK_FAILED=1
}

# ── systemd (по флагу) ────────────────────────────────────────────────────────
if [ "$MAKE_SYSTEMD" = "1" ]; then
    UNIT=deploy/classifier.service
    mkdir -p deploy
    sed -e "s|@WORKDIR@|$PWD|g" \
        -e "s|@USER@|$(id -un)|g" \
        -e "s|@PORT@|$PORT|g" \
        deploy/classifier.service.template > "$UNIT"
    ok "unit собран: $UNIT"
    printf '\n   Установить сервис:\n'
    printf '     sudo cp %s /etc/systemd/system/classifier.service\n' "$UNIT"
    printf '     sudo systemctl daemon-reload\n'
    printf '     sudo systemctl enable --now classifier\n'
fi

banner "Установка завершена"
printf '   Запуск:      ./launch.sh server %s\n' "$PORT"
printf '   Проверка:    curl http://127.0.0.1:%s/health\n' "$PORT"
printf '   Бэк-офис:    http://<адрес-машины>:%s/backoffice\n\n' "$PORT"
exit "${CHECK_FAILED:-0}"

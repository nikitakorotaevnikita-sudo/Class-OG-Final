"""Чтение и запись настроек LLM и Directum RX из бэк-офиса.

Источник истины остаётся прежним — файл `.env` в корне проекта. Модуль умеет:

* отдать текущие значения (секреты — в маскированном виде);
* записать изменения в `.env`, сохранив порядок строк, комментарии и кодировку;
* применить изменения в already-running процессе, не дожидаясь перезапуска.

Горячее применение нужно потому, что модули проекта импортируют константы
через `from config import X` — то есть держат собственную копию значения.
Одного `setattr(config, ...)` мало: перепривязываем имя во всех загруженных
модулях проекта, которые его импортировали.
"""

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
SRC_DIR = Path(__file__).parent

# Значение, которое фронт получает вместо секрета. Если поле вернулось с этим
# значением (или пустым) — пользователь его не трогал, перезаписывать не нужно.
MASK = "••••••••"

LLM_PROVIDERS = ("ario", "custom", "groq", "gemini", "ollama")


class SettingsError(ValueError):
    """Некорректное значение настройки, пришедшее с фронта."""


# ── Описание редактируемых полей ────────────────────────────────────────────
# kind: "text" | "secret" | "select"
# Порядок внутри группы = порядок отрисовки на форме.
SETTINGS_SPEC: list[dict] = [
    {
        "group": "llm",
        "group_label": "Модель LLM",
        "fields": [
            {"key": "LLM_PROVIDER", "label": "Провайдер", "kind": "select",
             "options": list(LLM_PROVIDERS),
             "hint": "Какой провайдер используется по умолчанию"},
            {"key": "ARIO_MODEL", "label": "Ario: модель", "kind": "text"},
            {"key": "ARIO_BASE_URL", "label": "Ario: base URL", "kind": "text",
             "validate": "url"},
            {"key": "ARIO_API_KEY", "label": "Ario: API-ключ", "kind": "secret"},
            {"key": "CUSTOM_LLM_BASE_URL", "label": "Свой endpoint: base URL",
             "kind": "text", "validate": "url",
             "hint": "OpenAI-совместимый адрес, например http://10.0.0.5:8000/v1"},
            {"key": "CUSTOM_LLM_MODEL", "label": "Свой endpoint: модель", "kind": "text",
             "hint": "Имя модели как его отдаёт GET /v1/models, например gpt-oss-20b"},
            {"key": "CUSTOM_LLM_API_KEY", "label": "Свой endpoint: API-ключ", "kind": "secret",
             "hint": "Если сервер не проверяет ключ — оставить пустым"},
            {"key": "GROQ_MODEL", "label": "Groq: модель", "kind": "text"},
            {"key": "GROQ_API_KEY", "label": "Groq: API-ключ", "kind": "secret"},
            {"key": "GEMINI_API_KEY", "label": "Gemini: API-ключ", "kind": "secret"},
            {"key": "OLLAMA_MODEL", "label": "Ollama: модель", "kind": "text"},
            {"key": "OLLAMA_BASE_URL", "label": "Ollama: base URL", "kind": "text",
             "validate": "url"},
        ],
    },
    {
        "group": "rx",
        "group_label": "Directum RX",
        "fields": [
            {"key": "RX_ODATA_URL", "label": "OData URL", "kind": "text",
             "validate": "url",
             "hint": "Адрес integration-сервиса RX, например http://localhost/integration/odata"},
            {"key": "RX_USER", "label": "Пользователь", "kind": "text"},
            {"key": "RX_PASSWORD", "label": "Пароль", "kind": "secret"},
        ],
    },
]

FIELDS_BY_KEY: dict[str, dict] = {
    field["key"]: field
    for group in SETTINGS_SPEC
    for field in group["fields"]
}

EDITABLE_KEYS: tuple[str, ...] = tuple(FIELDS_BY_KEY)


# ── Чтение ──────────────────────────────────────────────────────────────────

def _current_value(key: str) -> str:
    """Текущее значение из модуля config (он — единая точка правды в рантайме)."""
    import config
    return str(getattr(config, key, "") or "")


def read_settings() -> dict:
    """Текущие настройки для формы. Секреты отдаются маской, не значением."""
    groups = []
    for group in SETTINGS_SPEC:
        fields = []
        for field in group["fields"]:
            value = _current_value(field["key"])
            is_secret = field["kind"] == "secret"
            fields.append({
                "key": field["key"],
                "label": field["label"],
                "kind": field["kind"],
                "options": field.get("options", []),
                "hint": field.get("hint", ""),
                # Для секрета отдаём маску вместо значения, но сообщаем,
                # заполнен ли он вообще — иначе на форме не видно разницы
                # между «ключ есть» и «ключа нет».
                "value": (MASK if value else "") if is_secret else value,
                "is_secret": is_secret,
                "has_value": bool(value),
            })
        groups.append({
            "group": group["group"],
            "label": group["group_label"],
            "fields": fields,
        })
    return {"env_path": str(ENV_PATH), "groups": groups}


# ── Валидация ───────────────────────────────────────────────────────────────

def _validate(key: str, value: str) -> str:
    field = FIELDS_BY_KEY.get(key)
    if field is None:
        raise SettingsError(f"Неизвестная настройка: {key}")

    value = value.strip()

    # Перенос строки разорвал бы .env на две записи.
    if "\n" in value or "\r" in value:
        raise SettingsError(f"{field['label']}: значение не может содержать перенос строки")

    if field["kind"] == "select" and value not in field["options"]:
        allowed = ", ".join(field["options"])
        raise SettingsError(f"{field['label']}: допустимые значения — {allowed}")

    if field.get("validate") == "url" and value and not value.startswith(("http://", "https://")):
        raise SettingsError(f"{field['label']}: адрес должен начинаться с http:// или https://")

    if field["kind"] != "secret" and not value:
        raise SettingsError(f"{field['label']}: значение не может быть пустым")

    return value


def normalize_updates(raw: dict) -> dict:
    """Отсеять незаполненные секреты и проверить остальные значения.

    Пустое поле секрета (или присланная обратно маска) означает «оставить как
    было» — иначе открытая и сохранённая без изменений форма стирала бы ключи.
    """
    updates: dict[str, str] = {}
    for key, value in (raw or {}).items():
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            raise SettingsError(f"Неизвестная настройка: {key}")

        value = "" if value is None else str(value)
        if field["kind"] == "secret" and value.strip() in ("", MASK):
            continue

        updates[key] = _validate(key, value)
    return updates


# ── Запись в .env ───────────────────────────────────────────────────────────

def _read_env_text() -> str:
    """Прочитать .env как текст, не теряя байты.

    В файле лежит смесь кодировок (BOM + комментарии, побитые CP1251), поэтому
    декодируем с surrogateescape: любой байт переживёт круг чтение→запись.
    """
    if not ENV_PATH.exists():
        return ""
    return ENV_PATH.read_bytes().decode("utf-8", errors="surrogateescape")


def _write_env_text(text: str) -> None:
    ENV_PATH.write_bytes(text.encode("utf-8", errors="surrogateescape"))


def backup_env() -> Path | None:
    """Сохранить копию .env рядом. Возвращает путь копии (None, если файла нет)."""
    if not ENV_PATH.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ENV_PATH.with_name(f".env.bak-settings-{stamp}")
    shutil.copy2(ENV_PATH, backup)
    return backup


def _apply_to_env_text(text: str, updates: dict) -> str:
    """Заменить значения существующих ключей, недостающие — дописать в конец."""
    lines = text.split("\n")
    remaining = dict(updates)

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# --- Добавлено из бэк-офиса ---")
        lines.extend(f"{key}={value}" for key, value in remaining.items())
        lines.append("")

    return "\n".join(lines)


def write_env(updates: dict) -> Path | None:
    """Записать значения в .env, предварительно сделав резервную копию."""
    if not updates:
        return None
    backup = backup_env()
    _write_env_text(_apply_to_env_text(_read_env_text(), updates))
    return backup


# ── Горячее применение ──────────────────────────────────────────────────────

def _project_modules():
    """Загруженные модули проекта (всё, что лежит в src/)."""
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            if Path(path).parent == SRC_DIR:
                yield module
        except (OSError, ValueError):
            continue


def apply_runtime(updates: dict, agent=None) -> list[str]:
    """Применить значения в текущем процессе. Возвращает список применённых ключей.

    Порядок важен: сначала os.environ (его читают библиотеки), затем config,
    затем модули, импортировавшие имя напрямую.
    """
    if not updates:
        return []

    import config

    for key, value in updates.items():
        os.environ[key] = value
        setattr(config, key, value)

    for module in _project_modules():
        if module is config:
            continue
        for key, value in updates.items():
            if hasattr(module, key):
                setattr(module, key, value)

    if agent is not None:
        refresh_agent_llm(agent, updates)

    return sorted(updates)


def refresh_agent_llm(agent, updates: dict) -> None:
    """Сбросить закешированные LLM-клиенты агента после смены ключей.

    ClassifierAgent создаёт клиентов лениво и держит их на себе (self.groq и
    т.п.), поэтому новый API-ключ без сброса не подхватится.
    """
    if "LLM_PROVIDER" in updates:
        agent.llm = updates["LLM_PROVIDER"]

    cached_clients = {
        "GROQ_API_KEY": "groq",
        "GEMINI_API_KEY": "gemini",
        "OLLAMA_BASE_URL": "_ollama_client",
    }
    for key, attr in cached_clients.items():
        if key in updates and hasattr(agent, attr):
            client = getattr(agent, attr)
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            delattr(agent, attr)


def save_settings(raw: dict, agent=None) -> dict:
    """Полный цикл: проверить → записать в .env → применить в рантайме."""
    updates = normalize_updates(raw)
    if not updates:
        return {"saved": [], "applied": [], "backup": None}

    backup = write_env(updates)
    applied = apply_runtime(updates, agent=agent)
    return {
        "saved": sorted(updates),
        "applied": applied,
        "backup": str(backup) if backup else None,
    }

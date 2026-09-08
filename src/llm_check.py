"""Проверка связи с OpenAI-совместимым LLM-endpoint.

Используется кнопкой «Проверить связь» в настройках — по аналогии с проверкой RX.
Дёргаем `GET {base_url}/models`: это дешёвый запрос без генерации, он же
показывает, какие модели сервер реально отдаёт (частая ошибка — имя модели
не совпадает с тем, что на сервере).
"""
from __future__ import annotations

import httpx

TIMEOUT = 15.0


def check_connection(base_url: str, api_key: str = "", model: str = "") -> dict:
    """Вернуть {ok, detail, models}.

    Никогда не бросает исключений: любой сбой превращается в ok=False с текстом
    причины, чтобы фронт мог показать её как есть.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "detail": "Не задан base URL", "models": []}

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = httpx.get(f"{base}/models", headers=headers, timeout=TIMEOUT)
    except Exception as exc:                                     # noqa: BLE001
        return {"ok": False,
                "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
                "models": []}

    if r.status_code != 200:
        body = ""
        try:
            body = str(r.json())[:200]
        except Exception:                                        # noqa: BLE001
            body = (r.text or "")[:200]
        return {"ok": False, "detail": f"HTTP {r.status_code}: {body}", "models": []}

    try:
        data = r.json().get("data") or []
        models = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
    except Exception as exc:                                     # noqa: BLE001
        return {"ok": False,
                "detail": f"Ответ не похож на список моделей: {type(exc).__name__}",
                "models": []}

    wanted = (model or "").strip()
    if wanted and wanted not in models:
        return {"ok": True,
                "detail": (f"Связь есть, но модели «{wanted}» нет в списке сервера. "
                           f"Доступно: {', '.join(models[:8]) or '—'}"),
                "models": models}

    return {"ok": True,
            "detail": f"Связь есть, моделей доступно: {len(models)}",
            "models": models}


# ── Какой endpoint проверять ─────────────────────────────────────────────────
# Проверка обязана идти туда, куда пойдёт классификация. Раньше кнопка всегда
# читала CUSTOM_LLM_*, и при провайдере `ario` отвечала «Не задан base URL» —
# оператор читал это как отсутствие связи с моделью, хотя связь была.

# У groq адрес не настраивается: он зашит в SDK, здесь тот же самый.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_PROVIDERS: dict[str, dict] = {
    "ario": {"base": "ARIO_BASE_URL", "model": "ARIO_MODEL", "key": "ARIO_API_KEY"},
    "custom": {"base": "CUSTOM_LLM_BASE_URL", "model": "CUSTOM_LLM_MODEL",
               "key": "CUSTOM_LLM_API_KEY"},
    "ollama": {"base": "OLLAMA_BASE_URL", "model": "OLLAMA_MODEL", "key": None},
    "groq": {"base": None, "fixed_base": GROQ_BASE_URL, "model": "GROQ_MODEL",
             "key": "GROQ_API_KEY"},
}

# Gemini ходит собственным SDK Google, OpenAI-совместимого /models у нас в
# этом пути нет — честнее сказать это прямо, чем стучаться не туда.
_UNCHECKABLE = {
    "gemini": "Провайдер gemini работает через SDK Google, а не через "
              "OpenAI-совместимый /models — этой кнопкой связь не проверяется.",
}


def resolve_endpoint(provider: str | None = None,
                     values: dict | None = None) -> dict:
    """Куда и с каким ключом идти проверке связи.

    `provider` пустой — берётся из конфига. `values` — значения с формы
    настроек по именам из `.env`: они имеют приоритет над конфигом, поля
    других провайдеров игнорируются.
    """
    import config

    name = (provider or getattr(config, "LLM_PROVIDER", "") or "").strip().lower()
    if name in _UNCHECKABLE:
        return {"provider": name, "base_url": "", "api_key": "", "model": "",
                "supported": False, "detail": _UNCHECKABLE[name]}

    spec = _PROVIDERS.get(name)
    if spec is None:
        return {"provider": name, "base_url": "", "api_key": "", "model": "",
                "supported": False,
                "detail": f"Неизвестный провайдер «{name}»"}

    form = values or {}

    def pick(setting: str | None) -> str:
        if not setting:
            return ""
        value = form.get(setting)
        if value is None or not str(value).strip():
            value = getattr(config, setting, "")
        return str(value or "").strip()

    return {
        "provider": name,
        "base_url": pick(spec["base"]) or spec.get("fixed_base", ""),
        "api_key": pick(spec["key"]),
        "model": pick(spec["model"]),
        "supported": True,
        "detail": "",
        "base_setting": spec["base"],
    }


def check_active(provider: str | None = None, values: dict | None = None) -> dict:
    """Проверить endpoint активного провайдера.

    Возвращает то же, что `check_connection`, плюс `provider` и `base_url` —
    оператору важно видеть, какой именно адрес проверяли.
    """
    resolved = resolve_endpoint(provider, values)
    name = resolved["provider"]

    if not resolved["supported"]:
        return {"ok": False, "detail": resolved["detail"], "models": [],
                "provider": name, "base_url": ""}

    base = resolved["base_url"]
    if not base:
        setting = resolved.get("base_setting") or "base URL"
        return {"ok": False,
                "detail": f"Провайдер «{name}»: не задан {setting}",
                "models": [], "provider": name, "base_url": ""}

    result = check_connection(base_url=base,
                              api_key=resolved["api_key"],
                              model=resolved["model"])
    return {**result,
            "provider": name,
            "base_url": base,
            "detail": f"{name} → {base}: {result['detail']}"}

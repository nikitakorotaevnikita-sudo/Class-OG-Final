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

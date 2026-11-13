"""Через прокси из окружения ходим только во внешнюю сеть.

Прокси (`HTTP_PROXY`/`HTTPS_PROXY`) настраивают для выхода в интернет, а RX и
модель Заказчика стоят во внутренней сети. httpx по умолчанию читает эти
переменные, поэтому запрос к внутреннему адресу уходил в прокси: тот отвечал
`503`, а при незапущенном клиенте прокси — отказом в соединении (`WinError
10061` на Windows). В бэк-офисе это выглядело как «сервис лежит», хотя тот же
адрес напрямую отвечал.

Отличить внутренний адрес от внешнего можно по самому адресу, и это надёжнее
общего выключателя: Ario и Groq остаются доступны через прокси там, где без
него нет интернета.
"""

from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlsplit

# Домены внутренних сетей. Односоставное имя (`vllm`, `web-rx-test`) тоже
# внутреннее: в интернете таких не бывает.
_INTERNAL_SUFFIXES = (".local", ".localdomain", ".lan", ".internal",
                      ".intranet", ".corp", ".home", ".test")


def _is_internal_host(host: str) -> bool:
    host = host.strip().strip("[]").rstrip(".").lower()
    if not host:
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return (address.is_private or address.is_loopback
                or address.is_link_local)

    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        return True
    return "." not in host


def uses_env_proxy(url: Optional[str], override: Optional[bool] = None) -> bool:
    """Пускать ли запрос к `url` через прокси из переменных окружения.

    `override` — явная настройка (`RX_VIA_PROXY`, `LLM_VIA_PROXY`): задана —
    решает она. Иначе внутренние адреса запрашиваются напрямую, внешние — как
    настроено в окружении. Неразобранный адрес поведения не меняет.
    """
    if override is not None:
        return bool(override)

    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:                                           # noqa: BLE001
        return True

    return not _is_internal_host(host)

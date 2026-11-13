"""Доставка webhook: белый список хостов, HMAC-подпись, повторы."""
import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

from core import config
from integrations.proxy_policy import uses_env_proxy

log = logging.getLogger(__name__)

ATTEMPTS = 3
BACKOFF = (0.5, 1.0)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _norm_host(host: Optional[str]) -> str:
    """Имя хоста без регистра и без завершающей точки (rx.local. == rx.local)."""
    return (host or "").lower().rstrip(".")


def _allowed_entries() -> list[tuple[str, Optional[int]]]:
    """Записи WEBHOOK_ALLOWED_HOSTS как (хост, порт или None = любой порт).

    Формат записи: host, host:port, [ipv6] или [ipv6]:port. Запись с
    некорректным портом пропускается (ничего не разрешает).
    """
    entries = []
    for raw in config.WEBHOOK_ALLOWED_HOSTS:
        raw = raw.strip()
        if not raw:
            continue
        if "/" in raw or "@" in raw:
            log.warning("WEBHOOK_ALLOWED_HOSTS: запись %r пропущена: нужен только "
                        "host, host:port или [ipv6] (без схемы и userinfo)", raw)
            continue
        try:
            parsed = urlparse("//" + raw)
            host, port = _norm_host(parsed.hostname), parsed.port
        except ValueError:
            continue
        if host:
            entries.append((host, port))
    return entries


def is_allowed(url: str) -> bool:
    """http/https и хост точно в WEBHOOK_ALLOWED_HOSTS (без wildcard).

    Решает хост после userinfo (http://rx.local@evil/ — это хост evil).
    Запись с портом разрешает только этот порт (с учётом порта схемы по
    умолчанию), запись без порта — любой.
    """
    try:
        parsed = urlparse(url)
        host = _norm_host(parsed.hostname)
        port = parsed.port
        if port is None:
            port = _DEFAULT_PORTS.get(parsed.scheme)
    except ValueError:
        return False
    if parsed.scheme not in _DEFAULT_PORTS or not host:
        return False
    return any(host == allowed_host and allowed_port in (None, port)
               for allowed_host, allowed_port in _allowed_entries())


def _host(url: str) -> str:
    """Хост для журнала: путь и query webhook могут содержать токены."""
    try:
        return urlparse(url).hostname or "?"
    except ValueError:
        return "?"


def sign(body: bytes) -> Optional[str]:
    secret = config.WEBHOOK_SECRET
    if not secret:
        return None
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def deliver(url: str, payload: dict, transport: Optional[httpx.BaseTransport] = None) -> bool:
    """POST payload на url. Возвращает True при 2xx. Не бросает исключений."""
    try:
        if not is_allowed(url):
            log.warning("webhook отклонён: хост не в белом списке (%s)", _host(url))
            return False
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        signature = sign(body)
        if signature:
            headers["X-Signature"] = signature
        # Редиректы не следуем: 3xx увёл бы запрос на хост вне белого списка.
        with httpx.Client(timeout=10, trust_env=uses_env_proxy(url, None),
                          transport=transport, follow_redirects=False) as client:
            for attempt in range(ATTEMPTS):
                try:
                    resp = client.post(url, content=body, headers=headers)
                    if 200 <= resp.status_code < 300:
                        return True
                    log.warning("webhook %s: HTTP %s (попытка %d)", _host(url), resp.status_code,
                                attempt + 1)
                except httpx.HTTPError as exc:
                    log.warning("webhook %s: %s (попытка %d)", _host(url), type(exc).__name__,
                                attempt + 1)
                if attempt < ATTEMPTS - 1:
                    _sleep(BACKOFF[attempt])
        return False
    except Exception:
        log.exception("webhook: непредвиденная ошибка")
        return False

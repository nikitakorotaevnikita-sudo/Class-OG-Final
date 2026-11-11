"""Доставка webhook: белый список хостов, HMAC-подпись, повторы."""
import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

import config
from proxy_policy import uses_env_proxy

log = logging.getLogger(__name__)

ATTEMPTS = 3
BACKOFF = (0.5, 1.0)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def is_allowed(url: str) -> bool:
    """http/https и хост точно в WEBHOOK_ALLOWED_HOSTS (без wildcard)."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not host:
        return False
    allowed = {h.strip().lower() for h in config.WEBHOOK_ALLOWED_HOSTS if h.strip()}
    return host in allowed


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
        with httpx.Client(timeout=10, trust_env=uses_env_proxy(url, None),
                          transport=transport) as client:
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

"""Webhook: белый список хостов, подпись, доставка с повторами."""
import hashlib
import hmac

import httpx

import config
from queueing import webhook


def test_is_allowed_rejects_unknown_host(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local"])
    assert webhook.is_allowed("https://RX.local/hook")
    assert not webhook.is_allowed("https://evil.example/hook")
    assert not webhook.is_allowed("ftp://rx.local/hook")
    assert not webhook.is_allowed("https://sub.rx.local/hook")


def test_is_allowed_empty_list_disables(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", [])
    assert not webhook.is_allowed("https://rx.local/hook")


def test_sign_hmac_sha256(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "key")
    expected = hmac.new(b"key", b"body", hashlib.sha256).hexdigest()
    assert webhook.sign(b"body") == "sha256=" + expected
    # известный вектор (RFC-стиль)
    assert webhook.sign(b"The quick brown fox jumps over the lazy dog") == (
        "sha256=f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    )
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    assert webhook.sign(b"body") is None


def test_deliver_retries_then_false(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local"])
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    sleeps = []
    monkeypatch.setattr(webhook, "_sleep", sleeps.append)
    ok = webhook.deliver("https://rx.local/h", {"a": "я"},
                         transport=httpx.MockTransport(handler))
    assert ok is False
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]
    assert calls[0].headers["X-Signature"].startswith("sha256=")
    assert calls[0].content == '{"a": "я"}'.encode("utf-8")


def test_deliver_success_and_refusal(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local"])
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(webhook, "_sleep", lambda s: None)
    t = httpx.MockTransport(lambda r: httpx.Response(204))
    assert webhook.deliver("https://rx.local/h", {}, transport=t) is True
    assert webhook.deliver("https://other/h", {}, transport=t) is False


def test_logs_show_host_only_not_full_url(monkeypatch, caplog):
    """URL webhook может содержать токены — в журнал попадает только хост."""
    import logging

    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local"])
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(webhook, "_sleep", lambda s: None)
    caplog.set_level(logging.WARNING, logger=webhook.log.name)
    secret_url = "https://evil.example/hook?token=SECRET123"
    assert webhook.deliver(secret_url, {}) is False
    t = httpx.MockTransport(lambda r: httpx.Response(500))
    assert webhook.deliver("https://rx.local/h?token=SECRET456", {}, transport=t) is False
    text = caplog.text
    assert "evil.example" in text and "rx.local" in text
    assert "SECRET123" not in text and "SECRET456" not in text
    assert "/hook" not in text


def test_allowlist_entry_with_port_matches_host_and_port(monkeypatch):
    """Запись host:port разрешает только этот порт; запись без порта — любой."""
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS",
                        ["rx.local:8443", "172.16.96.98:80", "plain.local"])
    assert webhook.is_allowed("https://rx.local:8443/hook")
    assert not webhook.is_allowed("https://rx.local/hook")       # 443 по умолчанию
    assert not webhook.is_allowed("https://rx.local:9000/hook")
    assert webhook.is_allowed("http://172.16.96.98/hook")        # 80 по умолчанию для http
    assert webhook.is_allowed("http://172.16.96.98:80/hook")
    assert not webhook.is_allowed("http://172.16.96.98:8080/hook")
    assert webhook.is_allowed("https://plain.local:7000/hook")
    assert webhook.is_allowed("https://plain.local/hook")


def test_allowlist_ipv6_entry_with_port(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["[::1]:8080", "[fe80::1]"])
    assert webhook.is_allowed("http://[::1]:8080/hook")
    assert not webhook.is_allowed("http://[::1]:9090/hook")
    assert webhook.is_allowed("http://[fe80::1]:1234/hook")


def test_invalid_port_in_url_or_entry_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local:notaport", "ok.local"])
    assert not webhook.is_allowed("https://rx.local/hook")
    assert not webhook.is_allowed("https://ok.local:99999/hook")
    assert webhook.is_allowed("https://ok.local/hook")


def test_userinfo_does_not_spoof_allowed_host(monkeypatch):
    """Решает хост после '@', а не userinfo перед ним."""
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local"])
    assert not webhook.is_allowed("http://rx.local@evil.example/hook")
    assert not webhook.is_allowed("http://rx.local:pass@evil.example/hook")
    assert webhook.is_allowed("http://user@rx.local/hook")
    assert webhook.is_allowed("http://user:pw@rx.local/hook")


def test_trailing_dot_host_is_the_same_host(monkeypatch):
    """«rx.local.» — то же полное имя, что «rx.local»: ни обхода, ни ложного отказа."""
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local", "dotted.local."])
    assert webhook.is_allowed("https://rx.local./hook")
    assert webhook.is_allowed("https://dotted.local/hook")
    assert not webhook.is_allowed("https://rx.local.evil.example./hook")
    assert not webhook.is_allowed("https://./hook")


def test_redirects_are_not_followed(monkeypatch):
    """Ответ 3xx не уводит запрос на хост вне белого списка."""
    monkeypatch.setattr(config, "WEBHOOK_ALLOWED_HOSTS", ["rx.local"])
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(webhook, "_sleep", lambda s: None)
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    clients = []
    real_client = httpx.Client

    def spy_client(*args, **kwargs):
        clients.append(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(webhook.httpx, "Client", spy_client)
    assert webhook.deliver("https://rx.local/h", {}, transport=httpx.MockTransport(handler)) is False
    assert hosts and set(hosts) == {"rx.local"}
    assert clients and clients[0].get("follow_redirects") is False

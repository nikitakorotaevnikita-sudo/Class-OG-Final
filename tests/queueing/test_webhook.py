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

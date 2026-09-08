"""Запросы в RX не идут через системный прокси.

Причина: на машине оператора включён прокси для интернета
(`HTTP_PROXY=http://127.0.0.1:10809`), а RX стоит во внутренней сети. httpx
по умолчанию читает переменные окружения, поэтому запрос к RX уходил в прокси,
и тот отвечал `503` — а когда клиент прокси не запущен, `WinError 10061`.
Выглядело как «стенд лежит», хотя стенд отвечал: напрямую тот же адрес даёт
`401` с `WWW-Authenticate: Basic realm=...`, то есть сервис жив и ждёт креды.

Внутренний адрес отличается от внешнего по самому адресу (см.
`tests/test_proxy_policy.py`); если адрес обманчив, решение продавливает
`RX_VIA_PROXY`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import pytest

import config
import rx_client


class _Resp:
    status_code = 200


class _FakeClient:
    """Клиент, который только запоминает, с чем его создали."""

    created: list[dict] = []

    def __init__(self, **kwargs):
        _FakeClient.created.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, *a, **k):
        return _Resp()


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    _FakeClient.created = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    yield


@pytest.fixture(autouse=True)
def decide_by_address(monkeypatch):
    """По умолчанию настройка ничего не продавливает — решает адрес."""
    monkeypatch.setattr(rx_client, "RX_VIA_PROXY", None)
    yield


def test_check_connection_ignores_env_proxy():
    rx_client.check_connection(url="http://172.16.104.68/integration/odata")
    assert _FakeClient.created[0]["trust_env"] is False


def test_document_client_ignores_env_proxy(monkeypatch):
    """Клиент за документами адреса не получает — берёт его из конфига."""
    monkeypatch.setattr(rx_client, "RX_ODATA_URL", "http://172.16.104.68/integration/odata")
    rx_client.build_client()
    assert _FakeClient.created[0]["trust_env"] is False


def test_proxy_can_be_turned_back_on(monkeypatch):
    """У кого RX за прокси — включает обратно настройкой, а не правкой кода."""
    monkeypatch.setattr(rx_client, "RX_VIA_PROXY", True)
    rx_client.check_connection(url="http://172.16.104.68/integration/odata")
    rx_client.build_client()
    assert [c["trust_env"] for c in _FakeClient.created] == [True, True]


def test_proxy_can_be_turned_off_for_an_external_rx(monkeypatch):
    monkeypatch.setattr(rx_client, "RX_VIA_PROXY", False)
    rx_client.check_connection(url="https://rx.example.com/odata")
    assert _FakeClient.created[0]["trust_env"] is False


def test_setting_defaults_to_deciding_by_address():
    assert config.RX_VIA_PROXY is None

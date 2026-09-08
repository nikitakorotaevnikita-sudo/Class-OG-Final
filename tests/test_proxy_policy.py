"""Прокси из окружения — только для внешних адресов.

Прокси (`HTTP_PROXY`/`HTTPS_PROXY`) настраивают для выхода в интернет. Через
него не видно ни RX, ни модель Заказчика, поднятую во внутренней сети: запрос
уходит в прокси, и тот отвечает `503`, а при незапущенном клиенте прокси —
отказом в соединении (`WinError 10061`). Со стороны это выглядит как «сервис
лежит», хотя напрямую тот же адрес отвечает.

Поэтому адрес во внутренней сети запрашивается напрямую, а внешний (Ario,
Groq) — как настроено в окружении. Решение можно продавить настройкой.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from proxy_policy import uses_env_proxy


@pytest.mark.parametrize("url", [
    "http://10.112.103.34/v1",              # модель Заказчика
    "http://172.16.104.68/integration/odata",  # RX
    "http://192.168.1.10:8000/v1",
    "http://127.0.0.1:11434/v1",
    "http://localhost:11434/v1",
    "http://[::1]:8000/v1",
    "http://vllm:8000/v1",                  # имя без домена = внутреннее
    "http://web-rx-test/integration/odata",
    "http://rx.local/odata",
    "http://model.internal/v1",
    "https://gpt.corp.lan/v1",
])
def test_internal_addresses_go_direct(url):
    assert uses_env_proxy(url) is False


@pytest.mark.parametrize("url", [
    "https://llm.ario.directum360.ru/v1",
    "https://api.groq.com/openai/v1",
    "https://generativelanguage.googleapis.com/v1beta",
    "http://8.8.8.8/v1",   # 203.0.113.x нельзя: ipaddress считает
                           # документационные диапазоны приватными
])
def test_external_addresses_keep_the_env_proxy(url):
    assert uses_env_proxy(url) is True


@pytest.mark.parametrize("override,expected", [(True, True), (False, False)])
def test_override_wins_over_the_guess(override, expected):
    """У кого внутренний сервис за прокси (или наоборот) — решает настройкой."""
    assert uses_env_proxy("http://10.0.0.1/v1", override) is expected
    assert uses_env_proxy("https://api.groq.com/openai/v1", override) is expected


@pytest.mark.parametrize("url", ["", None, "не-адрес", "http://"])
def test_unparsable_address_keeps_the_env_proxy(url):
    """Не разобрали адрес — не меняем поведение окружения."""
    assert uses_env_proxy(url) is True

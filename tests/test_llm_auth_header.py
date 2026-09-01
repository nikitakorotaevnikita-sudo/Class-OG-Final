"""Заголовок авторизации при пустом ключе.

Ошибка со стенда: локальный endpoint ключа не требует, `CUSTOM_LLM_API_KEY`
пустой, и запрос падал ещё до отправки — «Illegal header value b'Bearer '».
Значение «Bearer » с висящим пробелом httpx считает недопустимым.
"""

import sys

sys.path.insert(0, "src")

import httpx
import pytest

import classifier_agent


class _Capture:
    """Подменяет httpx.Client, чтобы увидеть заголовки без сетевого вызова."""

    def __init__(self):
        self.headers = None

    def __call__(self, *, base_url, headers, timeout):
        self.headers = headers
        # httpx проверяет заголовки при создании клиента — пусть проверит.
        return httpx.Client(base_url=base_url, headers=headers, timeout=timeout)


def call_with_key(monkeypatch, key):
    capture = _Capture()
    monkeypatch.setattr(httpx, "Client", capture)
    monkeypatch.setattr(classifier_agent.ClassifierAgent, "_openai_endpoint",
                        staticmethod(lambda provider=None: ("http://endpoint.local/v1", key)))

    agent = classifier_agent.ClassifierAgent.__new__(classifier_agent.ClassifierAgent)
    try:
        agent._ario_call(messages=[{"role": "user", "content": "тест"}],
                         model="local-model", provider="custom")
    except Exception:
        # Сетевого сервера нет — важны только заголовки, собранные до запроса.
        pass
    return capture.headers


@pytest.mark.parametrize("key", ["", "   ", None])
def test_no_authorization_header_when_key_is_empty(monkeypatch, key):
    headers = call_with_key(monkeypatch, key)
    assert headers == {}, f"при пустом ключе заголовок не нужен, получили {headers}"


def test_authorization_header_present_when_key_is_set(monkeypatch):
    headers = call_with_key(monkeypatch, "sk-secret")
    assert headers == {"Authorization": "Bearer sk-secret"}


def test_empty_key_header_is_rejected_when_sent():
    """Почему пустой ключ нельзя просто подставить в шаблон.

    Создание клиента такой заголовок пропускает — отвергает его h11 уже при
    отправке запроса, поэтому ошибка и всплывала во время классификации, а не
    при старте сервиса.
    """
    import h11

    with pytest.raises(h11.LocalProtocolError) as exc:
        h11.Request(method="POST", target="/v1/chat/completions",
                    headers=[("host", "endpoint.local"), ("authorization", "Bearer ")])
    assert "Illegal header value" in str(exc.value)

    # С непустым ключом тот же заголовок проходит.
    h11.Request(method="POST", target="/v1/chat/completions",
                headers=[("host", "endpoint.local"), ("authorization", "Bearer sk-1")])

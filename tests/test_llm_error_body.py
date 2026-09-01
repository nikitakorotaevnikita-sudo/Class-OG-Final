"""Тело ответа LLM должно попадать в лог при HTTP-ошибке.

Случай со стенда: vLLM на неизвестное имя модели отвечает `404` с текстом
«The model does not exist», а в логе был голый `404 Not Found` — по нему
неверное имя модели не отличить от неверного пути, и диагностика встала.
"""

import sys

sys.path.insert(0, "src")

import httpx
import pytest

from classifier_agent import ClassifierAgent


def response(status: int, body: str) -> httpx.Response:
    return httpx.Response(status_code=status, text=body,
                          request=httpx.Request("POST", "http://endpoint.local/v1/chat/completions"))


def test_error_body_is_printed_and_error_raised(capsys):
    body = '{"object":"error","message":"The model `gpt-oss-20b` does not exist.","code":404}'
    with pytest.raises(httpx.HTTPStatusError):
        ClassifierAgent._raise_with_body(response(404, body))

    printed = capsys.readouterr().out
    assert "404" in printed
    assert "does not exist" in printed, f"тело ответа не попало в лог: {printed!r}"


def test_long_body_is_truncated(capsys):
    with pytest.raises(httpx.HTTPStatusError):
        ClassifierAgent._raise_with_body(response(500, "x" * 5000))
    printed = capsys.readouterr().out
    # Лог не должен раздуваться на пять килобайт мусора.
    assert len(printed) < 800
    assert "xxx" in printed


def test_successful_response_prints_nothing(capsys):
    ClassifierAgent._raise_with_body(response(200, '{"choices":[]}'))
    assert capsys.readouterr().out == ""


def test_path_is_shown_in_message(capsys):
    with pytest.raises(httpx.HTTPStatusError):
        ClassifierAgent._raise_with_body(response(404, "nope"), where="/v1/chat/completions")
    assert "/v1/chat/completions" in capsys.readouterr().out

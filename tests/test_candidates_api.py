"""Поиск кандидатов и проверка кода для агента ArioCowork.

Агент вызывает их через тулы mcpOGV `classifier_search` / `classifier_get`;
выбор финального кода остаётся за агентом. Контракт согласован с сессией MCP —
поля и коды ответов менять только вместе с ней.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import integration_api

VET = "0004.0015.0158.0974"


def _candidate(code, similarity, level=4):
    return {"code": code, "name": f"рубрика {code}", "level": level,
            "full_path": f"раздел / {code}", "parent_code": "p",
            "similarity": similarity, "source": "dense"}


def _agent(candidates=None):
    seen = []

    def retrieve(text):
        seen.append(text)
        return list(candidates if candidates is not None else [
            _candidate(VET, 0.6123), _candidate("0004.0015.0158.0975", 0.5),
            _candidate("0002.0007.0072.0281", 0.4)])

    index = {
        "0003.0009.0097.0000": {"code": "0003.0009.0097.0000", "name": "Благоустройство",
                                "level": 3, "full_path": "a / b / Благоустройство",
                                "parent_code": "0003.0009.0000.0000"},
        VET: {"code": VET, "name": "Выплаты", "level": 4, "full_path": "a / b / c / Выплаты",
              "parent_code": "0004.0015.0158.0000"},
        "0001.0001.0006.0013.0001": {"code": "0001.0001.0006.0013.0001", "name": "Уточнение",
                                     "level": 5, "full_path": "a / b / c / d / Уточнение",
                                     "parent_code": "0001.0001.0006.0013"},
    }
    return SimpleNamespace(_retrieve_for_segment=retrieve, code_index=index, seen=seen)


def _client(agent):
    app = FastAPI()
    app.include_router(integration_api.router)
    if agent is not None:
        app.state.agent = agent
    return TestClient(app)


def test_search_returns_candidates_with_score():
    r = _client(_agent()).post("/integration/candidates", json={"query": "выплаты СВО"})
    assert r.status_code == 200
    first = r.json()["items"][0]
    assert first == {"code": VET, "name": f"рубрика {VET}", "level": 4,
                     "full_path": f"раздел / {VET}", "score": 0.612}
    assert r.json()["returned"] == 3


def test_search_default_is_fifty():
    many = [_candidate(f"0001.0001.0001.{i:04d}", 0.9 - i / 100) for i in range(60)]
    r = _client(_agent(many)).post("/integration/candidates", json={"query": "вопрос"})
    assert r.json()["returned"] == 50
    assert len(r.json()["items"]) == 50


def test_search_respects_top_k():
    r = _client(_agent()).post("/integration/candidates", json={"query": "вопрос", "top_k": 2})
    assert r.json()["returned"] == 2


def test_search_clamps_top_k():
    client = _client(_agent())
    assert client.post("/integration/candidates", json={"query": "в", "top_k": 0}).json()["returned"] == 1
    assert client.post("/integration/candidates", json={"query": "в", "top_k": -5}).json()["returned"] == 1
    r = client.post("/integration/candidates", json={"query": "в", "top_k": 500})
    assert r.status_code == 200
    assert r.json()["returned"] <= 50


def test_search_rejects_empty_query():
    r = _client(_agent()).post("/integration/candidates", json={"query": "   "})
    assert r.status_code == 400
    assert "пуст" in r.json()["detail"]


def test_search_accepts_a_long_query():
    agent = _agent()
    r = _client(agent).post("/integration/candidates", json={"query": "слово " * 2000})
    assert r.status_code == 200
    assert len(agent.seen[0]) > 9000


def test_get_finds_leaf_and_normalizes():
    client = _client(_agent())
    leaf = client.get(f"/integration/classifier/%20{VET}%20").json()
    assert leaf["exists"] is True and leaf["code"] == VET and leaf["level"] == 4
    assert leaf["full_path"] == "a / b / c / Выплаты"
    short = client.get("/integration/classifier/0003.0009.0097").json()
    assert short["code"] == "0003.0009.0097.0000" and short["level"] == 3
    l5 = client.get("/integration/classifier/0001.0001.0006.0013.0001").json()
    assert l5["exists"] is True and l5["level"] == 5


def test_get_unknown_code():
    r = _client(_agent()).get("/integration/classifier/9999.9999.9999.9999")
    assert r.status_code == 200
    assert r.json() == {"code": "9999.9999.9999.9999", "exists": False}


def test_503_while_agent_loads():
    client = _client(None)
    assert client.post("/integration/candidates", json={"query": "в"}).status_code == 503
    assert client.get(f"/integration/classifier/{VET}").status_code == 503


def test_query_text_not_logged(capsys):
    _client(_agent()).post("/integration/candidates", json={"query": "секретный текст"})
    assert "секретный" not in capsys.readouterr().out


def test_router_is_registered():
    paths = {route.path for route in integration_api.router.routes}
    assert "/integration/candidates" in paths
    assert "/integration/classifier/{code}" in paths


def test_warns_when_pipeline_pool_is_smaller_than_fifty(monkeypatch, capsys):
    """Поиск отдаёт не больше TOP_K_CANDIDATES: при 10 агент молча получил бы 10, а не 50."""
    import candidates_api
    monkeypatch.setattr(candidates_api, "TOP_K_CANDIDATES", 10)
    r = _client(_agent()).post("/integration/candidates", json={"query": "вопрос"})
    assert r.status_code == 200
    assert "TOP_K_CANDIDATES=10" in capsys.readouterr().out


def test_503_detail_reaches_the_agent():
    r = _client(None).post("/integration/candidates", json={"query": "в"})
    assert r.json()["detail"] == "Агент не инициализирован"

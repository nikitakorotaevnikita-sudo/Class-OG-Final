"""Поиск кандидатов и проверка кода классификатора для агента ArioCowork.

Агент вызывает эти точки через тулы mcpOGV `classifier_search` и
`classifier_get` и сам выбирает финальный код. Здесь — только первая стадия
пайплайна: смысловой и лексический поиск по уже загруженной базе.

Контракт согласован с сессией MCP (спецификация агента, §8.1–8.2): поля и коды
ответов менять только вместе с ней.
"""

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# Без своего префикса: роутер подключается к /integration в integration_api.
router = APIRouter()

MAX_TOP_K = 50


class CandidatesRequest(BaseModel):
    query: str
    # Без ограничений в схеме: 0 или 500 приводятся к 1…50, а не дают 422.
    top_k: int = MAX_TOP_K


def normalize_code(code: str) -> str:
    """Код в том виде, в котором он лежит в базе: L1–L3 дополнены `.0000`."""
    parts = [p for p in code.strip().split(".") if p]
    while len(parts) < 4:
        parts.append("0000")
    return ".".join(parts)


def _agent(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Агент не инициализирован")
    return agent


@router.post("/candidates")
def search_candidates(body: CandidatesRequest, request: Request) -> dict:
    agent = _agent(request)
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Запрос пуст: передайте фразу для поиска")
    top_k = max(1, min(MAX_TOP_K, body.top_k))
    started = time.time()
    candidates = agent._retrieve_for_segment(query)[:top_k]
    items = [
        {
            "code": c["code"],
            "name": c["name"],
            "level": c["level"],
            "full_path": c["full_path"],
            "score": round(float(c["similarity"]), 3),
        }
        for c in candidates
    ]
    # Текст запроса не пишем: это фраза из обращения гражданина.
    print(f"  [candidates] {len(items)} за {time.time() - started:.2f}с")
    return {"items": items, "returned": len(items)}


@router.get("/classifier/{code}")
def get_classifier_code(code: str, request: Request) -> dict:
    agent = _agent(request)
    normalized = normalize_code(code)
    entry = agent.code_index.get(normalized)
    if entry is None:
        return {"code": normalized, "exists": False}
    return {
        "code": entry["code"],
        "name": entry["name"],
        "level": entry["level"],
        "full_path": entry["full_path"],
        "parent_code": entry.get("parent_code", ""),
        "exists": True,
    }

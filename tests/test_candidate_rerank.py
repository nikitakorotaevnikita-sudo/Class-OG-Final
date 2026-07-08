"""Behavior contract for the upcoming lexical candidate reranker."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_MISSING = object()


def _install_import_stubs() -> dict[str, object]:
    """Keep classifier_agent import focused on pure rerank behavior."""
    originals: dict[str, object] = {}

    def remember(name: str) -> None:
        if name not in originals:
            originals[name] = sys.modules.get(name, _MISSING)

    def set_module(name: str, module: types.ModuleType) -> None:
        remember(name)
        sys.modules[name] = module

    torch = types.ModuleType("torch")
    torch.set_num_threads = lambda *_args, **_kwargs: None
    set_module("torch", torch)

    groq = types.ModuleType("groq")
    groq.Groq = type("Groq", (), {"__init__": lambda self, *args, **kwargs: None})
    groq.RateLimitError = type("RateLimitError", (Exception,), {})
    groq.APIError = type("APIError", (Exception,), {})
    set_module("groq", groq)

    sentence_transformers = types.ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = type(
        "SentenceTransformer",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )
    set_module("sentence_transformers", sentence_transformers)

    google = types.ModuleType("google")
    google.__path__ = []
    google_genai = types.ModuleType("google.genai")
    google_genai_client = types.ModuleType("google.genai.client")
    google_genai_types = types.ModuleType("google.genai.types")
    google_genai_client.Client = type("Client", (), {"__init__": lambda self, *args, **kwargs: None})
    google_genai_types.GenerateContentConfig = type(
        "GenerateContentConfig",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )
    google_genai.client = google_genai_client
    google_genai.types = google_genai_types
    google.genai = google_genai
    set_module("google", google)
    set_module("google.genai", google_genai)
    set_module("google.genai.client", google_genai_client)
    set_module("google.genai.types", google_genai_types)

    return originals


def _restore_modules(originals: dict[str, object]) -> None:
    for name, module in originals.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


_ORIGINAL_MODULES = _install_import_stubs()
try:
    ClassifierAgent = importlib.import_module("classifier_agent").ClassifierAgent
finally:
    _restore_modules(_ORIGINAL_MODULES)


def _agent() -> Any:
    return ClassifierAgent.__new__(ClassifierAgent)


def _candidate(code: str, name: str, similarity: float, full_path: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "level": 4,
        "full_path": full_path or name,
        "similarity": similarity,
    }


def _codes(candidates: list[dict[str, Any]]) -> list[str]:
    return [candidate["code"] for candidate in candidates]


def _rerank(query_text: str, candidates: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    rerank = getattr(_agent(), "_rerank_candidates", None)
    if rerank is None:
        pytest.xfail("ClassifierAgent._rerank_candidates is not implemented yet")
    return rerank(query_text, candidates, top_k=top_k)


def test_rerank_promotes_lexical_overlap_when_dense_similarity_is_close():
    candidates = [
        _candidate("dense-only", "street lighting maintenance", 0.902),
        _candidate(
            "lexical-match",
            "apartment building roof leak repair",
            0.900,
            "housing / apartment building / roof leak repair",
        ),
        _candidate("weaker-dense", "parking lot snow removal", 0.897),
    ]

    result = _rerank(
        "please repair the leaking roof in our apartment building",
        candidates,
        top_k=3,
    )

    assert _codes(result)[0] == "lexical-match"
    assert len(result) == 3


def test_rerank_keeps_input_order_stable_when_query_has_no_tokens():
    candidates = [
        _candidate("first", "apartment building roof leak repair", 0.902),
        _candidate("second", "street lighting maintenance", 0.901),
        _candidate("third", "parking lot snow removal", 0.899),
    ]

    result = _rerank("!!! ... ???", candidates, top_k=3)

    assert _codes(result) == ["first", "second", "third"]


def test_rerank_returns_exact_top_k_size_when_enough_candidates_exist():
    candidates = [
        _candidate("dense-only", "street lighting maintenance", 0.904),
        _candidate("lexical-match", "roof leak repair", 0.903),
        _candidate("related", "apartment building maintenance", 0.902),
        _candidate("extra", "public transport timetable", 0.901),
    ]

    result = _rerank("roof leak repair apartment building", candidates, top_k=2)

    assert len(result) == 2
    assert set(_codes(result)).issubset({candidate["code"] for candidate in candidates})


def test_lexical_search_adds_alias_candidates_from_full_metadata():
    agent = _agent()
    agent.metadata = [
        {
            "code": "health",
            "name": "child health examination",
            "level": 4,
            "full_path": "health / examination",
        },
        {
            "code": "kindergarten",
            "name": "дошкольное образование",
            "level": 5,
            "full_path": "образование / поступление в образовательные организации / дошкольное образование",
        },
    ]

    result = agent._search_lexical_candidates("очередь в детский сад", top_k=1)

    assert _codes(result) == ["kindergarten"]
    assert result[0]["source"] == "lexical"

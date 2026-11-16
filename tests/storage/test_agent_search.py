"""Плотный поиск агента через хранилище (memory и PostgreSQL) и сверка с numpy."""

import importlib.util
from pathlib import Path

import numpy as np

from classifier import classifier_agent as classifier_mod
from classifier.classifier_agent import ClassifierAgent
from storage.models import ClassifierEntry, ModelVersion

N = 120


def _parity():
    path = Path(__file__).resolve().parents[2] / "scripts" / "db" / "verify_search_parity.py"
    spec = importlib.util.spec_from_file_location("verify_search_parity", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeST:
    def __init__(self, *a, **k):
        pass

    def encode(self, text, normalize_embeddings=True, **k):
        v = np.random.default_rng(abs(hash(text)) % 2**32).standard_normal(768).astype(np.float32)
        return v / np.linalg.norm(v)


def _fill(storage):
    rng = np.random.default_rng(7)
    codes = [f"0001.0001.0001.{i:04d}" for i in range(N)]
    storage.classifier.upsert_entries(
        [ClassifierEntry(c, f"n{c}", 4, None, f"p/{c}", 0) for c in codes])
    storage.models.register(ModelVersion(0, "base", "intfloat/multilingual-e5-base", True))
    emb = rng.standard_normal((N, 768)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    storage.classifier.add_vectors(0, [(c, "base", c, emb[i]) for i, c in enumerate(codes)])
    return codes, emb


def test_store_search_matches_numpy_top50(storage):
    codes, emb = _fill(storage)
    mod = _parity()
    rng = np.random.default_rng(3)
    for _ in range(10):
        q = rng.standard_normal(768).astype(np.float32)
        q /= np.linalg.norm(q)
        np_top, np_all = mod.numpy_topk(emb, codes, q, 50)
        assert mod.compare_topk(np_top, storage.classifier.search(q, 0, 50, False), np_all) is None


def test_agent_search_candidates_on_store(storage, monkeypatch):
    codes, emb = _fill(storage)
    monkeypatch.setattr(classifier_mod, "SentenceTransformer", _FakeST)
    monkeypatch.setattr(classifier_mod, "LLM_PROVIDER", "ario")
    monkeypatch.setattr(classifier_mod, "ENABLE_CROSS_ENCODER_RERANKER", False)
    monkeypatch.setattr(classifier_mod, "ENABLE_EMBEDDING_ADAPTER", False)
    agent = ClassifierAgent(storage=storage)

    cands = agent._search_candidates("жалоба на шум", top_k=10)

    np_top, _ = _parity().numpy_topk(emb, codes, agent._embed_query("жалоба на шум"), 10)
    assert [c["code"] for c in cands] == [c for c, _ in np_top]
    assert [c["similarity"] for c in cands] == [round(s, 3) for _, s in np_top]


def test_parity_script_loads_finetuned_model_from_models_dir(tmp_path, monkeypatch):
    """Реестр хранит имя папки (e5-finetuned-v3): скрипт сверки ищет её в MODELS_DIR
    (config.resolve_model_path), а не грузит имя как модель Hugging Face."""
    import sys
    import types

    from core import config

    (tmp_path / "e5-finetuned-v3").mkdir()
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    loaded = []

    class _ST(_FakeST):
        def __init__(self, path, *a, **k):
            loaded.append(path)

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        types.SimpleNamespace(SentenceTransformer=_ST))
    mod = _parity()
    model, prefix = mod.load_model(ModelVersion(3, "base", "e5-finetuned-v3", True))
    assert loaded == [str(tmp_path / "e5-finetuned-v3")]
    assert prefix == "query: " and isinstance(model, _ST)

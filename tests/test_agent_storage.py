"""ClassifierAgent, AppealsLogger, annotations_storage и build_vectordb поверх Storage.

Настоящая SentenceTransformer не загружается: подменяется детерминированным фейком.
"""

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as classifier_mod
from classifier_agent import ClassifierAgent
from storage.memory import MemoryStorage
from storage.models import ClassifierEntry, ModelVersion

DIM = 768


def _unit(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.md5(text.encode("utf-8")).digest()[:8], "little")
    v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeST:
    loaded: list[str] = []

    def __init__(self, path, *args, **kwargs):
        self.path = path
        FakeST.loaded.append(path)

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        if isinstance(texts, str):
            return _unit(texts)
        return np.vstack([_unit(t) for t in texts])


ENTRIES = [
    ClassifierEntry("0001.0000.0000.0000", "Раздел", 1, None, "Раздел", 1),
    ClassifierEntry("0001.0001.0000.0000", "Тематика", 2, "0001.0000.0000.0000",
                    "Раздел / Тематика", 1),
    ClassifierEntry("0001.0001.0001.0001", "Вопрос", 4, "0001.0001.0000.0000",
                    "Раздел / Тематика / Вопрос", 0),
]


def _storage(with_model: bool = True) -> MemoryStorage:
    s = MemoryStorage()
    s.classifier.upsert_entries(ENTRIES)
    if with_model:
        s.models.register(ModelVersion(0, "base-e5", "intfloat/multilingual-e5-base", True))
        s.classifier.add_vectors(0, [
            (e.code, "base", f"{e.name}. {e.full_path}", _unit("v0:" + e.code)) for e in ENTRIES
        ])
    return s


@pytest.fixture
def fake_st(monkeypatch):
    FakeST.loaded = []
    monkeypatch.setattr(classifier_mod, "SentenceTransformer", FakeST)
    monkeypatch.setattr(classifier_mod, "LLM_PROVIDER", "ario")
    monkeypatch.setattr(classifier_mod, "ENABLE_CROSS_ENCODER_RERANKER", False)
    monkeypatch.setattr(classifier_mod, "ENABLE_EMBEDDING_ADAPTER", False)
    return FakeST


def test_agent_requires_active_model(fake_st):
    with pytest.raises(RuntimeError, match="Нет активной модели"):
        ClassifierAgent(storage=_storage(with_model=False))


def test_agent_loads_metadata_from_store(fake_st):
    agent = ClassifierAgent(storage=_storage())
    assert agent.model_version == 0
    assert fake_st.loaded == ["intfloat/multilingual-e5-base"]
    assert not hasattr(agent, "embeddings")
    root = agent.code_index["0001.0000.0000.0000"]
    assert set(root) == {"code", "name", "level", "parent_code", "full_path",
                         "children_count", "search_text"}
    assert root["parent_code"] == ""
    assert agent.code_index["0001.0001.0001.0001"]["search_text"] == (
        "Вопрос. Раздел / Тематика / Вопрос")


def test_search_candidates_uses_store(fake_st):
    storage = _storage()
    agent = ClassifierAgent(storage=storage)
    expected = storage.classifier.search(agent._embed_query("q"), 0, 10, False)

    cands = agent._search_candidates("q")

    assert [c["code"] for c in cands] == [code for code, _ in expected]
    assert all(isinstance(c["similarity"], float) for c in cands)
    assert cands[0]["similarity"] == round(expected[0][1], 3)
    assert set(cands[0]) >= {"code", "name", "level", "full_path", "parent_code", "source"}


def test_search_candidates_skips_unknown_codes(fake_st):
    storage = _storage()
    agent = ClassifierAgent(storage=storage)
    del agent.code_index["0001.0001.0001.0001"]
    codes = [c["code"] for c in agent._search_candidates("q")]
    assert "0001.0001.0001.0001" not in codes and len(codes) == 2


def _canned_llm(*_a, **_k):
    return {
        "vid_obrascheniya": "Заявление", "tip_obrascheniya": "Индивидуальное",
        "is_ustnoe": False, "summary": "суть",
        "questions": [{
            "ordinal": 1, "question_text": "вопрос", "selected_code": "0001.0001.0001.0001",
            "confidence": 0.9, "reasoning": "потому что", "alternative_codes": [],
            "predmet_vedeniya": "совместное",
        }],
    }


def _classify_ready(monkeypatch, agent):
    for flag in ("ENABLE_QUERY_EXPANSION", "ENABLE_MULTI_QUERY_EXPAND", "ENABLE_SECTION_ROUTING",
                 "ENABLE_FULL_CLASSIFIER_FALLBACK", "ENABLE_REPEAT_DETECTION",
                 "ENABLE_WITHDRAWAL_DETECTION"):
        monkeypatch.setattr(classifier_mod, flag, False)
    monkeypatch.setattr(agent, "_resolve_llm", lambda *a, **k: ("ario", "test-model"))
    monkeypatch.setattr(agent, "_retrieve_for_segment",
                        lambda text, **k: agent._search_candidates(text))
    monkeypatch.setattr(agent, "_classify_with_llm", _canned_llm)


def test_classify_logs_to_given_appeal_id(fake_st, monkeypatch):
    storage = _storage()
    agent = ClassifierAgent(storage=storage)
    _classify_ready(monkeypatch, agent)
    appeal_id = storage.appeals.create("Прошу помочь с вопросом")

    result = agent.classify("Прошу помочь с вопросом", appeal_id=appeal_id)

    assert result.log_id == appeal_id
    assert result.elapsed_seconds > 0
    entries = storage.appeals.as_log_entries()
    assert len(entries) == 1
    q = entries[0]["agent_questions"][0]
    assert q["selected_code"] == "0001.0001.0001.0001"
    assert entries[0]["candidates"] and entries[0]["overall_confidence"] == 0.9
    card = storage.appeals._cards[appeal_id]["result"]
    assert card.summary == "суть" and card.llm_model == "test-model"
    assert card.questions[0].predmet_vedeniya == "совместное"
    # запросы к LLM/поиску пишутся в журнал с id обращения
    assert any(r["appeal_id"] == appeal_id for r in storage.appeals._llm_log)


def test_classify_without_appeal_id_creates_card(fake_st, monkeypatch):
    storage = _storage()
    agent = ClassifierAgent(storage=storage)
    _classify_ready(monkeypatch, agent)

    result = agent.classify("Новое обращение")

    entries = storage.appeals.as_log_entries()
    assert len(entries) == 1 and entries[0]["id"] == result.log_id


def test_classify_reraises_logging_error_with_log_conn(fake_st, monkeypatch):
    storage = _storage()
    agent = ClassifierAgent(storage=storage)
    _classify_ready(monkeypatch, agent)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage.appeals, "save_result", boom)
    appeal_id = storage.appeals.create("текст")
    # без транзакции воркера — ошибка лога не роняет классификацию
    assert agent.classify("текст", appeal_id=appeal_id).log_id is None
    with pytest.raises(RuntimeError, match="db down"):
        agent.classify("текст", appeal_id=appeal_id, log_conn=object())


def test_reload_model_if_changed(fake_st):
    storage = _storage()
    agent = ClassifierAgent(storage=storage)
    assert agent.reload_model_if_changed() is False

    storage.models.register(ModelVersion(1, "base-e5", "models/e5-finetuned-v1", False))
    storage.classifier.add_vectors(1, [
        (e.code, "base", f"v1 {e.name}", _unit("v1:" + e.code)) for e in ENTRIES
    ])
    storage.models.activate(1)

    assert agent.reload_model_if_changed() is True
    assert agent.model_version == 1
    assert fake_st.loaded[-1] == "models/e5-finetuned-v1"
    assert agent.code_index["0001.0001.0001.0001"]["search_text"] == "v1 Вопрос"


def test_embed_passage_applies_prefix_rule(fake_st):
    agent = ClassifierAgent(storage=_storage())
    np.testing.assert_allclose(agent.embed_passage("текст"), _unit("passage: текст"))


# ── AppealsLogger ────────────────────────────────────────────────────────────

def test_logger_legacy_log_and_verification():
    from appeals_logger import AppealsLogger

    logger = AppealsLogger(_storage())
    lid = logger.log(appeal_text="текст", candidates=[{"code": "c", "name": "n", "similarity": 0.5}],
                     agent_questions=[{"selected_code": "0001.0001.0001.0001", "confidence": 0.8}],
                     overall_confidence=0.8)
    assert logger.read_all()[0]["agent_questions"][0]["selected_code"] == "0001.0001.0001.0001"
    assert logger.read_verified() == []
    assert logger.confirm(lid) is True
    assert logger.stats()["confirmed"] == 1
    assert len(logger.read_verified()) == 1
    assert logger.reject("not-an-id") is False


def test_correct_with_comment_creates_annotation():
    from appeals_logger import AppealsLogger

    storage = _storage()
    logger = AppealsLogger(storage)
    lid = logger.log(appeal_text="текст", candidates=[], agent_questions=[],
                     overall_confidence=0.5)

    assert logger.correct(lid, ["0001.0001.0001.0001"], comment="про вопрос") is True

    entry = logger.read_all()[0]
    assert entry["verification"]["status"] == "corrected"
    assert entry["verification"]["operator_codes"] == ["0001.0001.0001.0001"]
    assert storage.classifier.annotations_for("0001.0001.0001.0001") == ["про вопрос"]
    assert storage.classifier.pending_annotations(0) != []


# ── annotations_storage ─────────────────────────────────────────────────────

@pytest.fixture
def global_storage(monkeypatch):
    import storage.factory as factory

    s = _storage()
    monkeypatch.setattr(factory, "_storage", s)
    return s


def test_annotations_api(global_storage):
    import annotations_storage as ann

    code = "0001.0001.0001.0001"
    assert ann.build_search_text(code, "Вопрос", "Раздел / Вопрос") == "Вопрос. Раздел / Вопрос"
    assert ann.list_annotated_codes() == []

    vid = ann.add_annotation(code, "просто комментарий", case_id="x")
    assert isinstance(vid, int)
    ann.save_annotation(code, "correct_name", "Новое имя")
    ann.save_annotation(code, "note", "старая")
    ann.save_annotation(code, "note", "заметка")
    ann.save_annotation(code, "category", "кат")

    assert ann.list_annotated_codes() == [code]
    assert ann.get_annotations(code) == [
        "просто комментарий", "correct_name: Новое имя", "note: заметка", "category: кат"]
    assert ann.build_search_text(code, "Вопрос", "Раздел / Вопрос") == (
        "Новое имя. Раздел / Вопрос | #category: кат | Note: заметка")

    ann.delete_annotation(code, "note")
    assert "note: заметка" not in ann.get_annotations(code)
    ann.delete_annotation(code)
    assert ann.get_annotations(code) == []
    assert not hasattr(ann, "backup_annotations")


# ── build_vectordb ──────────────────────────────────────────────────────────

def test_build_database_writes_base_and_annotation_rows(global_storage, monkeypatch):
    import annotations_storage as ann
    import build_vectordb

    monkeypatch.setattr(build_vectordb, "SentenceTransformer", FakeST)
    code = "0001.0001.0001.0001"
    ann.add_annotation(code, "комментарий")
    ann.save_annotation(code, "correct_name", "Иное")
    global_storage.models.register(ModelVersion(1, "base-e5", "models/e5-finetuned-v1", False))

    n = build_vectordb.build_database(global_storage, "models/e5-finetuned-v1", 1)

    rows = global_storage.classifier.vectors_for_version(1)
    assert n == len(rows) == 3 + 2
    base = {c: t for c, st, t in rows if st == "base"}
    assert base[code] == "Иное. Раздел / Тематика / Вопрос"
    assert base["0001.0000.0000.0000"] == "Раздел. Раздел"
    assert sorted(t for c, st, t in rows if st == "annotation") == sorted(
        ["комментарий", "correct_name: Иное"])
    assert global_storage.classifier.pending_annotations(1) == []
    q = _unit("passage: Раздел. Раздел")
    top = global_storage.classifier.search(q, 1, 1, False)
    assert top[0][0] == "0001.0000.0000.0000" and top[0][1] == pytest.approx(1.0, abs=1e-5)


# ── verify_search_parity ────────────────────────────────────────────────────

def _parity_mod():
    import importlib.util

    path = Path(__file__).parent.parent / "scripts" / "db" / "verify_search_parity.py"
    spec = importlib.util.spec_from_file_location("verify_search_parity", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parity_numpy_vs_store():
    mod = _parity_mod()
    rng = np.random.default_rng(1)
    codes = [f"0001.0001.0001.{i:04d}" for i in range(80)]
    emb = rng.standard_normal((80, DIM)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    s = MemoryStorage()
    s.classifier.add_vectors(0, [(c, "base", c, emb[i]) for i, c in enumerate(codes)])

    for qi in range(5):
        q = rng.standard_normal(DIM).astype(np.float32)
        q /= np.linalg.norm(q)
        np_top, np_all = mod.numpy_topk(emb, codes, q, 50)
        db_top = s.classifier.search(q, 0, 50, False)
        assert mod.compare_topk(np_top, db_top, np_all) is None

    # расхождение: другой порядок / сдвиг similarity
    q = rng.standard_normal(DIM).astype(np.float32)
    np_top, np_all = mod.numpy_topk(emb, codes, q, 50)
    swapped = [np_top[1], np_top[0]] + np_top[2:]
    assert "позиция 0" in mod.compare_topk(np_top, swapped, np_all)
    shifted = [(np_top[0][0], np_top[0][1] + 1e-3)] + np_top[1:]
    assert mod.compare_topk(np_top, shifted, np_all) is not None
    assert mod.compare_topk(np_top, np_top[:49], np_all) is not None
    # ничья в пределах допуска — не расхождение
    tie = [(np_top[0][0], 0.5), (np_top[1][0], 0.5 + 1e-7)]
    tie_all = {np_top[0][0]: 0.5, np_top[1][0]: 0.5 + 1e-7}
    assert mod.compare_topk(tie, list(reversed(tie)), tie_all) is None


def test_agent_metadata_keeps_store_order(fake_st):
    """Порядок self.metadata = порядок classifier_flat.json (не сортировка по коду)."""
    s = MemoryStorage()
    given = [ENTRIES[2], ENTRIES[0], ENTRIES[1]]
    s.classifier.upsert_entries(given)
    s.models.register(ModelVersion(0, "base-e5", "intfloat/multilingual-e5-base", True))
    agent = ClassifierAgent(storage=s)
    assert [m["code"] for m in agent.metadata] == [e.code for e in given]


def test_agent_metadata_order_equals_classifier_flat(fake_st):
    import json

    flat_path = Path(__file__).parent.parent / "data" / "classifier_flat.json"
    flat = json.loads(flat_path.read_text(encoding="utf-8"))
    s = MemoryStorage()
    s.classifier.upsert_entries([
        ClassifierEntry(e["code"], e["name"], int(e["level"]), e.get("parent_code") or None,
                        e.get("full_path", ""), int(e.get("children_count", 0)))
        for e in flat
    ])
    s.models.register(ModelVersion(0, "base-e5", "intfloat/multilingual-e5-base", True))
    agent = ClassifierAgent(storage=s)
    assert [m["code"] for m in agent.metadata] == [e["code"] for e in flat]


# ── WP3: аннотации оператора, N+1, сверка множеств ─────────────────────────

def test_operator_comment_with_legacy_prefix_is_not_a_legacy_field(global_storage):
    """Комментарий оператора "correct_name: ..." не переименовывает запись."""
    import annotations_storage as ann

    code = "0001.0001.0001.0001"
    ann.add_annotation(code, "correct_name: Чужое имя")
    ann.add_annotation(code, "note: не заметка")
    assert ann.build_search_text(code, "Вопрос", "Раздел / Вопрос") == "Вопрос. Раздел / Вопрос"
    texts = ann.get_annotations(code)
    assert len(texts) == 2 and all("Чужое имя" in t or "не заметка" in t for t in texts)
    # поле старого формата через save_annotation по-прежнему действует
    ann.save_annotation(code, "correct_name", "Новое имя")
    assert ann.build_search_text(code, "Вопрос", "Раздел / Вопрос") == "Новое имя. Раздел / Вопрос"


def test_appeals_logger_correct_comment_does_not_rename_code(global_storage):
    import annotations_storage as ann
    from appeals_logger import AppealsLogger

    code = "0001.0001.0001.0001"
    logger = AppealsLogger(storage=global_storage)
    aid = global_storage.appeals.create("обращение")
    assert logger.correct(aid, [code], comment="correct_name: Подмена")
    assert ann.build_search_text(code, "Вопрос", "Раздел / Вопрос") == "Вопрос. Раздел / Вопрос"


class _NoPerCodeAnnotations:
    """Обёртка хранилища: annotations_for запрещён (N+1), остальное — как есть."""

    def __init__(self, storage):
        self._s = storage
        self.classifier = self

    def annotations_for(self, code):
        raise AssertionError(f"annotations_for({code}) — запрос на каждый код")

    def __getattr__(self, name):
        if hasattr(self._s.classifier, name):
            return getattr(self._s.classifier, name)
        return getattr(self._s, name)


def test_build_vectordb_reads_annotations_in_bulk(global_storage, monkeypatch):
    import annotations_storage as ann
    import build_vectordb

    monkeypatch.setattr(build_vectordb, "SentenceTransformer", FakeST)
    code = "0001.0001.0001.0001"
    ann.add_annotation(code, "комментарий")
    ann.save_annotation(code, "correct_name", "Иное")
    rows = build_vectordb.compute_vectors(_NoPerCodeAnnotations(global_storage), "m")
    base = {c: t for c, st, t, _ in rows if st == "base"}
    assert base[code] == "Иное. Раздел / Тематика / Вопрос"
    assert sum(1 for r in rows if r[1] == "annotation") == 2


def test_finetune_texts_read_annotations_in_bulk(global_storage):
    import annotations_storage as ann
    import finetune_model

    code = "0001.0001.0001.0001"
    ann.save_annotation(code, "correct_name", "Иное")
    texts = finetune_model._ClassifierTexts(_NoPerCodeAnnotations(global_storage))
    assert texts.passage(code) == "passage: Иное. Раздел / Тематика / Вопрос"


def test_compare_topk_detects_different_code_sets():
    mod = _parity_mod()
    np_all = {"A": 0.5, "B": 0.5, "C": 0.4, "D": 0.4}
    np_top = [("A", 0.5), ("B", 0.5), ("C", 0.4)]
    # каждая позиция — ничья, но A потерян, B повторён: множества различаются
    assert "повторяются" in mod.compare_topk(np_top, [("B", 0.5), ("B", 0.5), ("C", 0.4)],
                                             np_all)
    # X — ничья по позиции, но A из середины top пропал
    assert "множеств" in mod.compare_topk(np_top, [("B", 0.5), ("X", 0.5), ("C", 0.4)],
                                          {**np_all, "X": 0.5})
    # ничья на границе top-K (C и D по 0.4) — допустимое различие множеств
    assert mod.compare_topk(np_top, [("B", 0.5), ("A", 0.5), ("D", 0.4)], np_all) is None

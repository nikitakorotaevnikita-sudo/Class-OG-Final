"""Формат запроса к модели эмбеддингов зависит от семейства модели.

e5 обучена на префиксах «query: » / «passage: ». Qwen3-Embedding — на
инструкции задачи у запроса и голом тексте у документа: без инструкции её
поиск, по карточке модели, проседает на 1–5%. Остальные модели (BGE-M3 и
подобные) префиксов не ждут.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from embedding_format import format_passage, format_query


def test_e5_query_gets_its_prefix():
    assert format_query("не вывозят мусор", "intfloat/multilingual-e5-base") == "query: не вывозят мусор"


def test_e5_passage_gets_its_prefix():
    assert format_passage("Обращение с ТКО", "intfloat/multilingual-e5-base") == "passage: Обращение с ТКО"


@pytest.mark.parametrize("model", ["Qwen/Qwen3-Embedding-0.6B", "qwen/qwen3-embedding-4b"])
def test_qwen3_query_carries_a_task_instruction(model):
    formatted = format_query("не вывозят мусор", model)
    assert formatted.startswith("Instruct: ")
    assert formatted.endswith("Query:не вывозят мусор"), "формат из карточки модели"


def test_qwen3_passage_is_plain_text():
    """Документам Qwen3 инструкция не нужна — только запросу."""
    assert format_passage("Обращение с ТКО", "Qwen/Qwen3-Embedding-0.6B") == "Обращение с ТКО"


@pytest.mark.parametrize("model", ["BAAI/bge-m3", "deepvk/USER-bge-m3"])
def test_other_models_get_no_prefix(model):
    assert format_query("текст", model) == "текст"
    assert format_passage("текст", model) == "текст"


def test_local_finetuned_e5_path_is_still_e5():
    """Дообученная модель лежит по пути вида models/e5-finetuned-v1."""
    assert format_query("текст", "models/e5-finetuned-v1").startswith("query: ")


# ── Тип весов при загрузке ──────────────────────────────────────────────────
# Qwen3-Embedding хранится в bfloat16, и свежий transformers так её и грузит.
# У CPU без аппаратной поддержки bf16 (Ryzen 7430U, Zen 3) вычисления
# эмулируются: на этой машине сборка базы шла почти два часа вместо минут,
# 0.2 записи/с против 1.2 в float32.

def test_embedder_on_cpu_is_loaded_in_float32(monkeypatch):
    import embedding_format
    import torch

    captured = {}

    class _FakeST:
        def __init__(self, name, **kwargs):
            captured.update(kwargs, name=name)

    monkeypatch.setattr(embedding_format, "SentenceTransformer", _FakeST, raising=False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    embedding_format.load_embedder("Qwen/Qwen3-Embedding-0.6B")

    assert captured["name"] == "Qwen/Qwen3-Embedding-0.6B"
    assert captured["model_kwargs"]["dtype"] == torch.float32


def test_embedder_on_gpu_keeps_the_native_dtype(monkeypatch):
    """На видеокарте bf16 быстрый и экономит память — там не вмешиваемся."""
    import embedding_format
    import torch

    captured = {}

    class _FakeST:
        def __init__(self, name, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(embedding_format, "SentenceTransformer", _FakeST, raising=False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    embedding_format.load_embedder("Qwen/Qwen3-Embedding-0.6B")
    assert "model_kwargs" not in captured


# ── Предел длины входа ──────────────────────────────────────────────────────
# У e5 предел 512 токенов, у Qwen3-Embedding — 32 тысячи. Обращение на 5000
# символов кодировалось целиком: поиск на реальных обращениях ii25 занимал
# 5.9 с на запрос против 1.05 у e5. Суть обращения почти всегда в начале.

class _ModelWithLimit:
    def __init__(self, name, **kwargs):
        self.max_seq_length = 32768


def test_long_context_model_is_capped(monkeypatch):
    import embedding_format
    import torch
    monkeypatch.setattr(embedding_format, "SentenceTransformer", _ModelWithLimit)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    model = embedding_format.load_embedder("Qwen/Qwen3-Embedding-0.6B")
    assert model.max_seq_length == embedding_format.MAX_SEQ_LENGTH == 512


def test_short_context_model_is_left_alone(monkeypatch):
    import embedding_format
    import torch

    class _Short:
        def __init__(self, name, **kwargs):
            self.max_seq_length = 256

    monkeypatch.setattr(embedding_format, "SentenceTransformer", _Short)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert embedding_format.load_embedder("some/model").max_seq_length == 256

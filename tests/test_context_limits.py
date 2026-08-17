"""Лимиты, зависящие от размера контекста модели.

На Qwen3.6-35B-A3B контекст был 131 072 токена, поэтому:
  * дамп классификатора для фоллбэка шёл без full_path (с путём — 140 898 токенов,
    не влезало);
  * текст обращения обрезался до 4000 символов.

На Qwen3.8-27B контекст 262 144 — оба ограничения можно ослабить, но они должны
управляться конфигом, а не быть зашиты в код.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as ca
from classifier_agent import ClassifierAgent, _clip_for_llm


def _agent_with_meta():
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.metadata = [
        {"code": "0005.0005.0056.1160", "name": "Обращение с ТКО", "level": 4,
         "full_path": "ЖКХ / Благоустройство / Обращение с ТКО"},
        {"code": "0003.0009.0099.0742.0110", "name": "ремонт дорог", "level": 5,
         "full_path": "Экономика / Транспорт / Дороги / Ремонт"},
        {"code": "0005.0000.0000.0000", "name": "Раздел ЖКХ", "level": 1,
         "full_path": "ЖКХ"},
    ]
    return agent


# ── Обрезка текста обращения ────────────────────────────────────────────────

def test_clip_uses_configured_limit(monkeypatch):
    monkeypatch.setattr(ca, "LLM_APPEAL_CHAR_LIMIT", 100)
    assert len(_clip_for_llm("а" * 500)) == 100


def test_clip_keeps_long_appeal_when_limit_is_high(monkeypatch):
    """Обращение на 6249 символов (максимум в выборке Заказчика) не должно резаться."""
    monkeypatch.setattr(ca, "LLM_APPEAL_CHAR_LIMIT", 20000)
    text = "а" * 6249
    assert _clip_for_llm(text) == text


def test_clip_handles_empty():
    assert _clip_for_llm("") == ""


# ── Дамп классификатора для фоллбэка ────────────────────────────────────────

def test_leaf_listing_without_path_by_default(monkeypatch):
    monkeypatch.setattr(ca, "FULL_FALLBACK_INCLUDE_PATH", False)
    listing = _agent_with_meta()._full_leaf_listing()
    assert "0005.0005.0056.1160 — Обращение с ТКО" in listing
    assert "Благоустройство" not in listing, "путь не должен попадать при выключенном флаге"


def test_leaf_listing_with_path_when_enabled(monkeypatch):
    monkeypatch.setattr(ca, "FULL_FALLBACK_INCLUDE_PATH", True)
    listing = _agent_with_meta()._full_leaf_listing()
    assert "ЖКХ / Благоустройство / Обращение с ТКО" in listing
    assert "Экономика / Транспорт / Дороги / Ремонт" in listing


def test_leaf_listing_covers_only_leaves(monkeypatch):
    """В дамп идут только листья L4/L5, разделы верхних уровней — нет."""
    monkeypatch.setattr(ca, "FULL_FALLBACK_INCLUDE_PATH", False)
    listing = _agent_with_meta()._full_leaf_listing()
    assert "0005.0000.0000.0000" not in listing
    assert len(listing.splitlines()) == 2

"""LLM-сегментация обращения и разграничения веток соцсферы.

По обратной связи ПУР (Удмуртия) на 68 обращениях: 14 замечаний из 37 —
«добавить вопрос», то есть вопросы ТЕРЯЮТСЯ. Детерминированный сегментатор
видит только явные маркеры («1.», «во-первых», абзац с глаголом-просьбой),
поэтому вопрос, вписанный в текст без маркера, не выделяется вовсе —
и никакой ретривер его уже не найдёт.

Соцсфера (раздел 0002) — 39 % реального потока и худшая точность (30 % по
абсолютному критерию), поэтому в промпт добавлены разграничения её веток.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import classifier_agent as ca
from classifier_agent import ClassifierAgent, split_appeal_questions


def _agent(monkeypatch, llm_raw):
    agent = ClassifierAgent.__new__(ClassifierAgent)
    agent.llm = "ario"
    monkeypatch.setattr(agent, "_resolve_llm", lambda *a, **k: ("ario", "test-model"))
    monkeypatch.setattr(agent, "_ario_call", lambda **kw: llm_raw)
    return agent


# ── LLM-сегментация ─────────────────────────────────────────────────────────

def test_llm_split_extracts_questions_without_markers(monkeypatch):
    """Вопросы без маркеров нумерации — то, что теряет детерминированный сегментатор."""
    raw = '{"questions": ["Не вывозят мусор во дворе", "Сломано уличное освещение"]}'
    agent = _agent(monkeypatch, raw)
    segs = agent._llm_split_questions("во дворе не вывозят мусор и не горят фонари", "ario", "m")
    assert [s.text for s in segs] == ["Не вывозят мусор во дворе", "Сломано уличное освещение"]
    assert [s.ordinal for s in segs] == [1, 2]
    assert all(s.evidence == "llm" for s in segs)


def test_llm_split_returns_empty_on_broken_json(monkeypatch):
    """Сломанный ответ LLM => пустой список, вызывающий код уйдёт на детерминированный путь."""
    agent = _agent(monkeypatch, "это не json")
    assert agent._llm_split_questions("текст", "ario", "m") == []


def test_llm_split_ignores_empty_items(monkeypatch):
    raw = '{"questions": ["Реальный вопрос", "", "   "]}'
    agent = _agent(monkeypatch, raw)
    segs = agent._llm_split_questions("текст", "ario", "m")
    assert len(segs) == 1


def test_llm_split_respects_max_segments(monkeypatch):
    raw = '{"questions": ' + str([f"вопрос {i}" for i in range(20)]).replace("'", '"') + '}'
    agent = _agent(monkeypatch, raw)
    segs = agent._llm_split_questions("текст", "ario", "m", max_segments=7)
    assert len(segs) == 7


def test_deterministic_segmenter_still_available():
    """Детерминированный путь остаётся — он резервный при сбое LLM."""
    segs = split_appeal_questions("1. Первый вопрос про мусор\n2. Второй вопрос про свет")
    assert len(segs) == 2


# ── Разграничения соцсферы в промпте ────────────────────────────────────────

def test_prompt_has_social_sphere_disambiguations():
    tpl = ca.CLASSIFICATION_PROMPT_TEMPLATE_L3GROUP
    # Крупнейшие по объёму пары, которые модель путает
    assert "0390 vs 0420" in tpl, "лечение vs лекарственное обеспечение"
    assert "0327 vs 0325" in tpl, "контроль качества образования vs образовательные стандарты"
    assert "0253 vs 0312" in tpl, "выплаты безработным vs льготы категориям"


def test_prompt_keeps_existing_disambiguations():
    """Старые разграничения не должны пропасть."""
    tpl = ca.CLASSIFICATION_PROMPT_TEMPLATE_L3GROUP
    for marker in ("0096 vs 0097", "0007 vs 0014", "0004 vs 0013"):
        assert marker in tpl

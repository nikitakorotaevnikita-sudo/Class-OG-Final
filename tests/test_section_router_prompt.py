import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from section_router import ROUTING_SYSTEM_PROMPT, SECTIONS_CATALOG, build_routing_prompt


def test_routing_prompt_distinguishes_school_activity_from_construction():
    assert "деятельность детского сада/школы" in ROUTING_SYSTEM_PROMPT
    assert "строительство, реконструкция, проектирование" in ROUTING_SYSTEM_PROMPT
    assert "зданий школ/детсадов" in SECTIONS_CATALOG


def test_routing_prompt_ignores_addressee_status_for_topic():
    assert "Упоминание главы государства" in ROUTING_SYSTEM_PROMPT
    assert "лично Президенту" in ROUTING_SYSTEM_PROMPT
    assert "НЕ определяет тематику" in ROUTING_SYSTEM_PROMPT


def test_routing_catalog_expands_section_one_edge_cases():
    assert "нарушения избирательного процесса" in SECTIONS_CATALOG
    assert "наследование по существу" in SECTIONS_CATALOG
    assert "оформление через нотариуса = 0004.0019" in SECTIONS_CATALOG
    assert "получение гражданства РФ" in SECTIONS_CATALOG


def test_build_routing_prompt_includes_updated_guardrails():
    system, user = build_routing_prompt("Пишу Президенту про строительство школы", max_topics=3)

    assert system == ROUTING_SYSTEM_PROMPT
    assert "реконструкция и проектирование" in user
    assert "Пишу Президенту про строительство школы" in user

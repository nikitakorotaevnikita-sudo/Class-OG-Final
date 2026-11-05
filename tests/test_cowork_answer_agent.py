"""Агент подготовки ответов на обращения: инструкция и навыки для ArioCowork.

Держит то, что ломалось в прогоне из LibreChat 05.11.2026: переклассификация при
кодах в карточке, плавающий формат (то md, то docx), срок «+30 рабочих дней»,
несуществующий навык, нормы из памяти. Проект — спецификация
docs/superpowers/specs/2026-11-05-cowork-answer-agent-design.md.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
COWORK = ROOT / "cowork"
INSTRUCTION = COWORK / "answer-agent" / "agent-instruction.md"
NEW_SKILLS = ["answer-practice", "answer-legal-basis"]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _skill(name: str) -> str:
    return _text(COWORK / "skills" / name / "SKILL.md")


def test_instruction_names_every_tool_and_skill():
    text = _text(INSTRUCTION)
    for name in ["get_citizen_request_status", "get_document_text", "list_citizen_request_answers",
                 "create_outgoing_letter", "create_document_body", "classifier_search",
                 "appeal-questions", "classifier-code", "applicant-contacts", "og-repeat",
                 "answer-practice", "answer-legal-basis", "og-letters", "og-answer-review",
                 "mcp-pravo", "mcpOGV"]:
        assert name in text, name


def test_instruction_drops_what_broke_the_run():
    text = _text(INSTRUCTION)
    for gone in ["add_working_days", "garant", "answer-template-2024"]:
        assert gone not in text, gone


def test_codes_come_from_the_card():
    text = _text(INSTRUCTION)
    assert "без переклассификации" in text
    assert '"codes_source"' in text and '"card' in text


def test_deadline_comes_from_the_card():
    text = _text(INSTRUCTION)
    assert "deadline" in text and "days_left" in text
    assert "рабочих дней" not in text


def test_one_output_format():
    text = _text(INSTRUCTION)
    assert "Не создавай файлы" in text
    assert "answer_text" in text and "без markdown" in text


def test_instruction_has_the_pipeline_and_the_contract():
    text = _text(INSTRUCTION)
    assert "БЕТОННОЕ ПРАВИЛО" in text
    assert "=== ОТЛАДКА ===" in text
    for key in ["document_id", "deadline", "questions", "practice", "legal_basis",
                "answer_text", "summary", "reasoning", "check", "pipeline"]:
        assert f'"{key}"' in text, key


def test_no_personal_data_in_debug_rule():
    assert "без ФИО" in _text(INSTRUCTION)


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skills_have_valid_frontmatter(name):
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", _skill(name), re.DOTALL)
    assert match, "нет frontmatter"
    fields = dict(line.split(":", 1) for line in match.group(1).splitlines())
    assert fields["name"].strip() == name
    assert 0 < len(fields["description"].strip()) <= 1024
    assert len(match.group(2).strip()) >= 500


def test_practice_skill_widens_the_search_and_drops_boilerplate():
    text = _skill("answer-practice")
    for marker in ["list_citizen_request_answers", "limit=5", "skip", "XXXX.XXXX.XXXX",
                   "12 месяцев", "og-answer-review", "searched_by", "от сегодняшней даты"]:
        assert marker in text, marker


def test_legal_basis_skill_cites_only_what_was_found():
    text = _skill("answer-legal-basis")
    for marker in ["mcp-pravo", "search_documents", "только нормы, найденные",
                   "needs_verification", "legal_basis", "из памяти"]:
        assert marker in text, marker

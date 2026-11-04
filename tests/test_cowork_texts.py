"""Тексты агента-классификатора для ArioCowork: системная инструкция и навыки.

Оператор вставляет их в интерфейс платформы как есть, поэтому тесты держат то,
без чего агент сломается: имена тулов и навыков, контракт ответа для RX,
правила разграничения кодов и корректный frontmatter навыков.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
COWORK = ROOT / "cowork"
SKILLS = ["appeal-questions", "applicant-contacts", "classifier-code", "classifier-full"]


def _frontmatter(text: str) -> tuple[dict, str]:
    # На Windows git отдаёт файлы с CRLF (core.autocrlf) — разбор от этого не зависит.
    text = text.replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match, "нет frontmatter между строками ---"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, match.group(2)


def _instruction() -> str:
    return (COWORK / "agent-instruction.md").read_text(encoding="utf-8")


def test_instruction_names_every_tool_and_skill():
    text = _instruction()
    for name in ["get_document", "get_document_text", "classifier_search", "classifier_get",
                 "appeal-questions", "applicant-contacts", "classifier-code", "og-repeat",
                 "mcpOGV"]:
        assert name in text, name


def test_instruction_has_the_rx_contract():
    text = _instruction()
    for key in ["document_id", "applicant_fio", "applicant_email", "summary", "reasoning",
                "questions", "check"]:
        assert f'"{key}"' in text, key
    assert "250" in text and "1000" in text


@pytest.mark.parametrize("name", SKILLS)
def test_skills_have_valid_frontmatter(name):
    fields, body = _frontmatter((COWORK / "skills" / name / "SKILL.md").read_text(encoding="utf-8"))
    assert fields.get("name") == name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fields["name"])
    assert 0 < len(fields.get("description", "")) <= 1024
    assert len(body.strip()) >= 500


def test_classifier_code_keeps_the_disambiguations():
    text = (COWORK / "skills" / "classifier-code" / "SKILL.md").read_text(encoding="utf-8")
    for marker in ["top_k=50", "0096", "0097", "0007", "0014", "0004", "0013",
                   "0005.0005.0056", "0003.0009.0099", "0001.0002.0027.0126"]:
        assert marker in text, marker


def test_example_emails_are_placeholders():
    for path in COWORK.rglob("*.md"):
        for address in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", path.read_text(encoding="utf-8")):
            assert address.endswith("@example.ru"), f"{path.name}: {address}"

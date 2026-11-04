"""Сборка навыков агента в zip для загрузки в ArioCowork.

Платформа принимает навыки с файлами только zip-архивом, `SKILL.md` — в корне.
Навык `classifier-full` (вариант B проверки) несёт файл со всеми рубриками,
которые агент может выбрать: уровни 4 и 5.
"""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_cowork_skills

SKILLS = {"appeal-questions", "applicant-contacts", "classifier-code", "classifier-full"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    return {p.stem: p for p in build_cowork_skills.build(out)}


def _leaves():
    flat = json.loads((ROOT / "data" / "classifier_flat.json").read_text(encoding="utf-8"))
    return [e for e in flat if e["level"] in (4, 5)]


def test_builds_one_zip_per_skill(built):
    assert set(built) == SKILLS
    assert all(p.suffix == ".zip" for p in built.values())


def test_skill_md_at_zip_root(built):
    for path in built.values():
        with zipfile.ZipFile(path) as z:
            assert "SKILL.md" in z.namelist(), path.name


def test_full_listing_has_every_leaf(built):
    with zipfile.ZipFile(built["classifier-full"]) as z:
        lines = io.TextIOWrapper(z.open("classifier_full.txt"), encoding="utf-8").read().splitlines()
    leaves = _leaves()
    assert len(lines) == len(leaves) == 1876
    codes = [line.split(" — ", 1)[0] for line in lines]
    assert len(set(codes)) == len(codes)
    assert codes[0] == leaves[0]["code"]
    assert set(codes) == {e["code"] for e in leaves}
    assert all(" — " in line for line in lines)


def test_full_skill_states_the_count():
    text = (ROOT / "cowork" / "skills" / "classifier-full" / "SKILL.md").read_text(encoding="utf-8")
    assert "1876" in text
    assert "classifier_full.txt" in text

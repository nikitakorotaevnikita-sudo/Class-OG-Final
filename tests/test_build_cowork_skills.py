"""Сборка навыков агента в архивы для загрузки в ArioCowork — как в mcpOGV.

Правила упаковки повторяют `docs/usecases/skills/package_skills.py` репозитория
MCP-Directum-RX: на навык два одинаковых архива `ИМЯ.zip` и `ИМЯ.skill`,
`SKILL.md` в корне, фиксированная дата записей (одинаковые исходники — одинаковый
архив), проверка YAML-заголовка, служебные файлы не попадают. Навык
`classifier-full` (вариант B) несёт сгенерированный перечень рубрик уровней 4 и 5.
"""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_cowork_skills as pkg

SKILLS = {"appeal-questions", "applicant-contacts", "classifier-code", "classifier-full",
          "answer-practice", "answer-legal-basis"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    return {p.name: p for p in pkg.build(out)}


def _leaves():
    flat = json.loads((ROOT / "data" / "classifier_flat.json").read_text(encoding="utf-8"))
    return [e for e in flat if e["level"] in (4, 5)]


def _skill(tmp_path, name="demo-skill", header=None, extra=None):
    folder = tmp_path / name
    folder.mkdir()
    header = header if header is not None else f"---\nname: {name}\ndescription: Демо\n---\n"
    # write_bytes: write_text на Windows сам превратил бы \n в \r\n.
    (folder / "SKILL.md").write_bytes((header + "\nТело навыка.\n").encode("utf-8"))
    for rel, data in (extra or {}).items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


def test_builds_zip_and_skill_per_skill(built):
    assert set(built) == {f"{s}{ext}" for s in SKILLS for ext in (".zip", ".skill")}


def test_zip_and_skill_are_identical(built):
    for name in SKILLS:
        assert built[f"{name}.zip"].read_bytes() == built[f"{name}.skill"].read_bytes()


def test_skill_md_at_root_with_fixed_date(built):
    for path in built.values():
        with zipfile.ZipFile(path) as z:
            assert "SKILL.md" in z.namelist(), path.name
            assert {i.date_time for i in z.infolist()} == {(2026, 1, 1, 0, 0, 0)}


def test_rebuild_gives_the_same_bytes(built, tmp_path):
    again = {p.name: p for p in pkg.build(tmp_path)}
    for name, path in built.items():
        assert again[name].read_bytes() == path.read_bytes(), name


def test_full_listing_has_every_leaf(built):
    with zipfile.ZipFile(built["classifier-full.zip"]) as z:
        lines = io.TextIOWrapper(z.open("classifier_full.txt"), encoding="utf-8").read().splitlines()
    leaves = _leaves()
    assert len(lines) == len(leaves) == 1876
    codes = [line.split(" — ", 1)[0] for line in lines]
    assert len(set(codes)) == len(codes)
    assert codes[0] == leaves[0]["code"]
    assert set(codes) == {e["code"] for e in leaves}


def test_full_skill_states_the_count():
    text = (ROOT / "cowork" / "skills" / "classifier-full" / "SKILL.md").read_text(encoding="utf-8")
    assert "1876" in text and "classifier_full.txt" in text


def test_service_files_and_binaries(tmp_path):
    """Кэш питона не попадает в архив, бинарное вложение не ломает сборку."""
    skill = _skill(tmp_path, extra={"__pycache__/x.pyc": b"\x00", "assets/logo.png": b"\x89PNG\x00\xff"})
    out = pkg.package(skill, tmp_path / "out")
    with zipfile.ZipFile(out[0]) as z:
        names = z.namelist()
        assert "assets/logo.png" in names and z.read("assets/logo.png") == b"\x89PNG\x00\xff"
        assert not any("__pycache__" in n for n in names)


def test_text_goes_into_the_archive_with_lf(tmp_path):
    skill = _skill(tmp_path, header="---\r\nname: demo-skill\r\ndescription: Демо\r\n---\r\n")
    out = pkg.package(skill, tmp_path / "out")
    with zipfile.ZipFile(out[0]) as z:
        assert b"\r\n" not in z.read("SKILL.md")


@pytest.mark.parametrize("header, message", [
    ("нет заголовка\n", "YAML-заголовк"),
    ("---\nname: demo-skill\n", "не закрыт"),
    ("---\nname: Demo_Skill\ndescription: x\n---\n", "строчные"),
    ("---\nname: other-name\ndescription: x\n---\n", "совпадать"),
    ("---\nname: demo-skill\n---\n", "description"),
    ("---\nname: demo-skill\ndescription: " + "д" * 1025 + "\n---\n", "1024"),
], ids=["no-header", "not-closed", "bad-name", "name-mismatch", "no-description", "long-description"])
def test_bad_header_is_rejected(tmp_path, header, message):
    skill = _skill(tmp_path, header=header)
    with pytest.raises(pkg.SkillError, match=message):
        pkg.package(skill, tmp_path / "out")
    assert not (tmp_path / "out" / "demo-skill.zip").exists()


def test_published_archives_match_sources(tmp_path):
    """В docs/skills/ лежат архивы, собранные из текущих исходников."""
    fresh = {p.name: p for p in pkg.build(tmp_path)}
    published = ROOT / "docs" / "skills"
    for name, path in fresh.items():
        assert (published / name).read_bytes() == path.read_bytes(), f"пересоберите {name}"

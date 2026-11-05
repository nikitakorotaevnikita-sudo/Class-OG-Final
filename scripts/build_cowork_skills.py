"""Упаковка навыков агентов ArioCowork: .zip и .skill с SKILL.md в корне архива.

    venv\\Scripts\\python scripts\\build_cowork_skills.py [ИМЯ_НАВЫКА ...] [--out КАТАЛОГ]

Правила — как в mcpOGV (`docs/usecases/skills/package_skills.py` репозитория
MCP-Directum-RX), чтобы навыки обоих репозиториев грузились в платформу одинаково:
- на навык два одинаковых архива — `ИМЯ.zip` и `ИМЯ.skill`;
- SKILL.md начинается с YAML-заголовка с полями name и description; name — строчные
  латинские буквы, цифры и дефисы, до 64 символов, совпадает с именем каталога;
  description — до 1024 символов;
- фиксированная дата записей: одинаковые исходники дают одинаковый архив;
- служебное (`__pycache__`, `.pyc` …) не попадает, архив не больше 50 МБ.

Исходники — `cowork/skills/<имя>/`, готовые архивы — `docs/skills/` (в Git).
Навыку `classifier-full` в архив добавляется сгенерированный перечень рубрик
уровней 4 и 5 из `data/classifier_flat.json`.

Код выхода: 0 — всё собрано, 2 — навык не прошёл проверку, 1 — непредвиденная ошибка.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "cowork" / "skills"
DEFAULT_OUT = ROOT / "docs" / "skills"
CLASSIFIER = ROOT / "data" / "classifier_flat.json"
FULL_SKILL = "classifier-full"
FULL_LISTING = "classifier_full.txt"

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
EXTENSIONS = (".zip", ".skill")
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", "dist", ".venv"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}
# Текст кладётся в архив с LF: на Windows git отдаёт файлы с CRLF (core.autocrlf),
# и без нормализации архив зависел бы от машины, на которой собран.
TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".csv"}
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


class SkillError(ValueError):
    """Навык не соответствует требованиям платформы."""


def read_frontmatter(text: str, label: str = "SKILL.md") -> dict:
    lines = text.replace("\r\n", "\n").lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"{label}: файл должен начинаться с YAML-заголовка — строки «---»")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise SkillError(f"{label}: YAML-заголовок не закрыт второй строкой «---»") from None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise SkillError(f"{label}: YAML-заголовок не разбирается: {exc}") from None
    if not isinstance(data, dict):
        raise SkillError(f"{label}: YAML-заголовок должен быть набором полей «ключ: значение»")
    return data


def validate_skill(skill_dir: Path) -> dict:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError(f"{skill_dir.name}: нет файла SKILL.md")
    meta = read_frontmatter(skill_md.read_text(encoding="utf-8"), f"{skill_dir.name}/SKILL.md")
    name, description = meta.get("name"), meta.get("description")
    errors = []
    if not isinstance(name, str) or not name.strip():
        errors.append("поле name обязательно и должно быть строкой")
    elif len(name) > MAX_NAME_CHARS or not NAME_PATTERN.match(name):
        errors.append(f"name «{name}»: только строчные латинские буквы, цифры и дефисы, "
                      f"до {MAX_NAME_CHARS} символов")
    elif name != skill_dir.name:
        errors.append(f"name «{name}» должно совпадать с именем каталога «{skill_dir.name}»")
    if not isinstance(description, str) or not description.strip():
        errors.append("поле description обязательно и должно быть строкой")
    elif len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(f"description длиннее {MAX_DESCRIPTION_CHARS} символов ({len(description)})")
    if errors:
        raise SkillError(f"{skill_dir.name}/SKILL.md: " + "; ".join(errors))
    return meta


def full_listing() -> str:
    """Все рубрики, которые можно поставить обращению: «КОД — полный путь»."""
    entries = json.loads(CLASSIFIER.read_text(encoding="utf-8"))
    return "\n".join(f"{e['code']} — {e['full_path']}"
                     for e in entries if e["level"] in (4, 5)) + "\n"


def skill_entries(skill_dir: Path) -> list[tuple[str, bytes]]:
    """(путь в архиве, содержимое) — в постоянном порядке."""
    entries = []
    for path in sorted(skill_dir.rglob("*")):
        rel = path.relative_to(skill_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES:
            continue
        data = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES:
            data = data.replace(b"\r\n", b"\n")
        entries.append((rel.as_posix(), data))
    if skill_dir.name == FULL_SKILL:
        entries.append((FULL_LISTING, full_listing().encode("utf-8")))
    return entries


def check_archive(archive_path: Path) -> None:
    size = archive_path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise SkillError(f"{archive_path.name}: {size / 1024 / 1024:.1f} МБ — больше предела 50 МБ")
    with zipfile.ZipFile(archive_path) as archive:
        if "SKILL.md" not in archive.namelist():
            raise SkillError(f"{archive_path.name}: в корне архива нет SKILL.md")
        broken = archive.testzip()
        if broken is not None:
            raise SkillError(f"{archive_path.name}: повреждена запись {broken}")
        read_frontmatter(archive.read("SKILL.md").decode("utf-8"), archive_path.name)


def _write_zip(entries: list[tuple[str, bytes]], target: Path) -> None:
    """Атомарно: временный файл рядом и переименование — недописанного архива не остаётся."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(handle)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in entries:
                info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, data)
        check_archive(Path(temp_name))
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def package(skill_dir: Path, out_dir: Path = DEFAULT_OUT) -> list[Path]:
    validate_skill(skill_dir)
    entries = skill_entries(skill_dir)
    built = []
    for extension in EXTENSIONS:
        target = out_dir / f"{skill_dir.name}{extension}"
        _write_zip(entries, target)
        built.append(target)
    return built


def discover(root: Path = SKILLS_DIR) -> list[Path]:
    return [p for p in sorted(root.iterdir()) if p.is_dir() and (p / "SKILL.md").is_file()]


def build(out_dir: Path = DEFAULT_OUT, skills_dir: Path = SKILLS_DIR) -> list[Path]:
    built = []
    for skill_dir in discover(skills_dir):
        built.extend(package(skill_dir, out_dir))
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Упаковка навыков в .zip и .skill")
    parser.add_argument("skills", nargs="*", help="имена каталогов навыков (по умолчанию — все)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="куда положить архивы")
    args = parser.parse_args(argv)
    skill_dirs = [SKILLS_DIR / name for name in args.skills] if args.skills else discover()
    if not skill_dirs:
        print("Не найдено ни одного каталога с SKILL.md", file=sys.stderr)
        return 2
    failed = False
    for skill_dir in skill_dirs:
        try:
            for path in package(skill_dir, Path(args.out)):
                print(f"{path.name:28} {path.stat().st_size / 1024:8.1f} КБ")
        except SkillError as exc:
            failed = True
            print(f"Ошибка: {exc}", file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Непредвиденная ошибка: {exc}", file=sys.stderr)
        sys.exit(1)

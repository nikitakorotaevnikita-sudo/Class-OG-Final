"""Всё, что копирует Dockerfile, должно приезжать с `git clone`.

Ошибка, из-за которой появился тест: `COPY data/vector_db/ ./data/vector_db/`
работал на машине разработчика, где этот каталог остался с прежних экспериментов,
и падал на стенде — в `.gitignore` он исключён, на чистом клоне его нет.
Проверяется индекс git, а не рабочая копия: именно его видит стенд.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILES = (ROOT / "Dockerfile", ROOT / "Dockerfile.py313check")


def copy_sources(dockerfile: Path) -> list[str]:
    """Все источники COPY, включая многофайловые строки."""
    sources = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY "):
            continue
        parts = stripped.split()
        i = 1
        while i < len(parts) and parts[i].startswith("--"):
            i += 1
        tokens = parts[i:]
        if len(tokens) >= 2:
            sources.extend(tokens[:-1])
    return sources


def dockerfile_sources() -> list[str]:
    return copy_sources(ROOT / "Dockerfile")


def all_copy_cases() -> list[tuple[str, str]]:
    return [(df.name, src) for df in DOCKERFILES for src in copy_sources(df)]


def tracked_paths() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def test_dockerfile_has_copy_instructions():
    assert dockerfile_sources(), "в Dockerfile не нашлось ни одной строки COPY"


@pytest.mark.parametrize("dockerfile_name,source", all_copy_cases())
def test_every_copy_source_is_in_git(dockerfile_name, source):
    tracked = tracked_paths()
    if source in tracked:
        return
    # Каталог: достаточно, чтобы в git был хотя бы один файл внутри него.
    prefix = source.rstrip("/") + "/"
    assert any(path.startswith(prefix) for path in tracked), (
        f"{dockerfile_name} копирует «{source}», но этого пути нет в git — "
        f"на чистом клоне сборка упадёт с «not found in build context»"
    )

"""Всё, что копирует Dockerfile, должно приезжать с `git clone`.

Ошибка, из-за которой появился тест: `COPY data/vector_db/ ./data/vector_db/`
работал на машине разработчика, где этот каталог остался с прежних экспериментов,
и падал на стенде — в `.gitignore` он исключён, на чистом клоне его нет.
Проверяется индекс git, а не рабочая копия: именно его видит стенд.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COPY_RE = re.compile(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+(\S+)\s*$")


def dockerfile_sources() -> list[str]:
    sources = []
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        match = COPY_RE.match(line.strip())
        if match:
            sources.append(match.group(1))
    return sources


def tracked_paths() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def test_dockerfile_has_copy_instructions():
    assert dockerfile_sources(), "в Dockerfile не нашлось ни одной строки COPY"


@pytest.mark.parametrize("source", dockerfile_sources())
def test_every_copy_source_is_in_git(source):
    tracked = tracked_paths()
    if source in tracked:
        return
    # Каталог: достаточно, чтобы в git был хотя бы один файл внутри него.
    prefix = source.rstrip("/") + "/"
    assert any(path.startswith(prefix) for path in tracked), (
        f"Dockerfile копирует «{source}», но этого пути нет в git — "
        f"на чистом клоне сборка упадёт с «not found in build context»"
    )

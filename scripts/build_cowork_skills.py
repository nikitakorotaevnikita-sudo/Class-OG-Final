"""Собрать навыки агента ArioCowork в zip-архивы для загрузки в платформу.

    venv\\Scripts\\python scripts\\build_cowork_skills.py [out_dir]

По архиву на каждый каталог `cowork/skills/*`, `SKILL.md` — в корне архива.
В навык `classifier-full` кладётся сгенерированный перечень рубрик уровней 4 и 5.
По умолчанию архивы пишутся в `cowork/dist/` (в Git не попадает).
"""

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "cowork" / "skills"
CLASSIFIER = ROOT / "data" / "classifier_flat.json"
FULL_SKILL = "classifier-full"
FULL_LISTING = "classifier_full.txt"


def full_listing() -> str:
    """Все рубрики, которые можно поставить обращению: «КОД — полный путь»."""
    entries = json.loads(CLASSIFIER.read_text(encoding="utf-8"))
    return "\n".join(f"{e['code']} — {e['full_path']}"
                     for e in entries if e["level"] in (4, 5)) + "\n"


def build(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    built = []
    for skill in sorted(p for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file()):
        target = out_dir / f"{skill.name}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(skill.rglob("*")):
                if path.is_file():
                    # LF внутри архива: на Windows git отдаёт файлы с CRLF.
                    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                    z.writestr(path.relative_to(skill).as_posix(), text)
            if skill.name == FULL_SKILL:
                z.writestr(FULL_LISTING, full_listing())
        built.append(target)
    return built


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cowork" / "dist"
    for path in build(out):
        print(f"{path.name:28} {path.stat().st_size / 1024:8.1f} КБ")

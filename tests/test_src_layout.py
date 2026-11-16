"""Раскладка src/ по пакетам: модулей в корне нет, импорты только пакетные."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

PACKAGES = {
    "api": {"api_server", "integration_api", "job_api", "responses"},
    "classifier": {"classifier_agent", "hierarchy", "reranker", "section_router", "llm_schemas",
                   "llm_check", "fio_extractor", "email_extractor"},
    "queueing": {"dispatcher", "tasks", "waiter", "webhook"},
    "worker": {"worker_main", "run"},
    "storage": {"appeals_logger", "annotations_storage"},
    "training": {"finetune_model", "auto_finetune", "build_vectordb", "historical_loader",
                 "auto_import_historical"},
    "integrations": {"rx_client", "text_extractor", "proxy_policy"},
    "core": {"config", "env_bootstrap", "settings_store", "service_urls"},
    "cli": {"operator_cli", "classify_manual", "test_agent"},
}
# Имена модулей, которые раньше лежали прямо в src/ (queueing и storage-ядро — уже были пакетами).
MOVED = sorted(set().union(*PACKAGES.values()) - PACKAGES["queueing"])
NEW_PACKAGES = ("api", "classifier", "worker", "training", "integrations", "core", "cli")

_NAMES = "|".join(MOVED)
OLD_IMPORT = re.compile(rf"^\s*(import ({_NAMES})\b|from ({_NAMES}) import\b)", re.MULTILINE)


def _src_modules() -> list[str]:
    mods = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        mods.append(".".join(parts))
    return mods


def test_no_python_modules_directly_in_src():
    assert sorted(p.name for p in SRC.glob("*.py")) == []


@pytest.mark.parametrize("package", sorted(PACKAGES))
def test_package_contents(package):
    pkg = SRC / package
    assert (pkg / "__init__.py").is_file()
    present = {p.stem for p in pkg.glob("*.py")}
    assert PACKAGES[package] <= present, sorted(PACKAGES[package] - present)


@pytest.mark.parametrize("package", NEW_PACKAGES)
def test_new_package_init_imports_nothing(package):
    import ast

    tree = ast.parse((SRC / package / "__init__.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not imports, f"src/{package}/__init__.py должен быть пустым"


def test_api_static_served_from_api_package():
    assert (SRC / "api" / "static" / "index.html").is_file()
    assert not (SRC / "static").exists()


def test_every_src_module_imports():
    """Каждый модуль src импортируется по пакетному имени (PYTHONPATH=src, чужой cwd)."""
    modules = _src_modules()
    code = (
        "import importlib, sys, traceback\n"
        "failed = []\n"
        f"for name in {modules!r}:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception:\n"
        "        failed.append(name + ': ' + traceback.format_exc(limit=1).strip().splitlines()[-1])\n"
        "print('FAILED=' + repr(failed))\n"
        "sys.exit(1 if failed else 0)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONUTF8": "1", "STORAGE_BACKEND": "memory"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT / "tests"), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=600)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]


def test_api_process_does_not_import_model_stack():
    code = ("import sys, api.api_server; "
            "bad = [m for m in ('classifier.classifier_agent', 'sentence_transformers', 'torch') "
            "if m in sys.modules]; print('BAD=' + repr(bad)); sys.exit(1 if bad else 0)")
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_no_module_importable_under_two_names():
    """Никаких sys.path-хаков на подпапки src: модуль грузится только по пакетному имени."""
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"sys\.path\.insert\(0, str\(_?Path\(__file__\)(\.resolve\(\))?\.parent\)\)", text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders


def test_no_leftover_top_level_imports_of_moved_modules():
    offenders = []
    for top in ("src", "scripts", "tests"):
        for path in (ROOT / top).rglob("*.py"):
            if "__pycache__" in path.parts or path.name == "test_src_layout.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in OLD_IMPORT.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0).strip()}")
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("script", ["cli/operator_cli.py", "cli/classify_manual.py",
                                    "cli/test_agent.py", "training/auto_finetune.py",
                                    "training/auto_import_historical.py",
                                    "training/build_vectordb.py", "training/finetune_model.py",
                                    "worker/run.py"])
def test_entry_script_bootstraps_src_root(script):
    """`python src/<пакет>/x.py`: папка скрипта в sys.path заменяется на src."""
    text = (SRC / script).read_text(encoding="utf-8")
    assert "_SRC = Path(__file__).resolve().parents[1]" in text
    assert "sys.path[0] = str(_SRC)" in text


def test_entry_script_runs_from_its_package_folder():
    """Реальный запуск `python src/training/auto_finetune.py --help` из корня проекта."""
    proc = subprocess.run(
        [sys.executable, str(SRC / "training" / "auto_finetune.py"), "--help"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": ""},
    )
    assert proc.returncode == 0, proc.stderr
    assert "--force" in proc.stdout

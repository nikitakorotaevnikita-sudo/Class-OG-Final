"""Установщики не строят numpy-базу: векторы переносит в PostgreSQL шаг migrate.

`src/build_vectordb.py` без аргументов печатает usage и выходит с кодом 2 —
вызов из установщика обрывал бы свежую установку. Проверка готовности
(`scripts/check_offline.py`) не требует `embeddings.npy` в `data/vector_db`:
файлы `VECTOR_DB_DIR` нужны лишь как источник миграции.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLERS = ["install.bat", "setup.ps1", "install.sh", "install_offline.sh",
              "install_offline.bat"]


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_does_not_build_vector_db(name):
    text = _text(name)
    assert "build_vectordb" not in text, f"{name}: вызов build_vectordb.py обрывает установку"


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_points_to_migrate(name):
    text = _text(name)
    assert re.search(r"start(\.cmd|\.sh)? migrate|start\.cmd migrate|\./start\.sh migrate",
                     text), f"{name}: нет подсказки про перенос векторов (start migrate)"
    assert "PostgreSQL" in text


def test_setup_ps1_stays_ascii():
    assert (ROOT / "setup.ps1").read_bytes().isascii()


def test_install_sh_has_no_systemd_unit_generation():
    text = _text("install.sh")
    assert "classifier.service.template" not in text
    assert not (ROOT / "deploy" / "classifier.service.template").exists()


def test_install_sh_systemd_flag_is_refused():
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash недоступен")
    # Флаг разбирается до любых действий установщика — дальше он не доходит.
    proc = subprocess.run([bash, "install.sh", "--systemd"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
    assert proc.returncode == 1
    out = proc.stdout + proc.stderr
    assert "systemd" in out and "docker compose" in out and "./start.sh local" in out


# ── check_offline.py ────────────────────────────────────────────────────────

def _load_check_offline(monkeypatch, root: Path):
    spec = importlib.util.spec_from_file_location(
        "check_offline_under_test", ROOT / "scripts" / "check_offline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "BUNDLE", root / "offline_bundle")
    monkeypatch.setattr(mod, "_results", [])
    monkeypatch.setattr(mod, "ONLINE", True)
    return mod


def _vdb(root: Path, rel: str) -> None:
    d = root / rel
    d.mkdir(parents=True)
    (d / "embeddings.npy").write_bytes(b"x")
    (d / "metadata.json").write_text("[]", encoding="utf-8")


def test_check_offline_does_not_mention_build_vectordb():
    assert "build_vectordb" not in _text("scripts/check_offline.py")


def test_check_env_file_without_numpy_db_is_not_a_failure(monkeypatch, tmp_path):
    mod = _load_check_offline(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("HF_HUB_OFFLINE=1\nLLM_PROVIDER=custom\n", encoding="utf-8")
    mod.check_env_file()
    fails = [r for r in mod._results if r[0] == mod.FAIL]
    assert fails == [], fails


def test_check_env_file_reports_migration_source(monkeypatch, tmp_path):
    mod = _load_check_offline(monkeypatch, tmp_path)
    _vdb(tmp_path, "data/vector_db_adapted_v3")
    (tmp_path / ".env").write_text("VECTOR_DB_DIR=data/vector_db_adapted_v3\n", encoding="utf-8")
    mod.check_env_file()
    oks = [r for r in mod._results if r[0] == mod.OK and "VECTOR_DB_DIR" in r[1]]
    assert oks, mod._results


def test_check_vector_db_uses_configured_dir_and_never_promises_a_build(monkeypatch, tmp_path):
    mod = _load_check_offline(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("VECTOR_DB_DIR=data/vector_db_adapted_v3\n", encoding="utf-8")
    mod.check_vector_db()
    [(status, title, detail)] = mod._results
    assert status == mod.WARN
    assert "соберёт" not in title + detail and "migrate" in detail

    mod._results.clear()
    _vdb(tmp_path, "data/vector_db_adapted_v3")
    mod.check_vector_db()
    [(status, title, _)] = mod._results
    assert status == mod.OK and "vector_db_adapted_v3" in title

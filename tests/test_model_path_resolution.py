"""resolve_model_path: реестр хранит имя папки, путь собирается из MODELS_DIR воркера."""

import pytest

from core import config


@pytest.fixture
def models_dir(monkeypatch, tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setattr(config, "MODELS_DIR", str(d))
    return d


def test_hf_name_unchanged(models_dir):
    assert config.resolve_model_path("intfloat/multilingual-e5-base", 0) == \
        "intfloat/multilingual-e5-base"


def test_folder_name_resolved_under_models_dir(models_dir):
    (models_dir / "e5-finetuned-v3").mkdir()
    assert config.resolve_model_path("e5-finetuned-v3", 3) == str(models_dir / "e5-finetuned-v3")


@pytest.mark.parametrize("legacy", [
    r"D:\AIOG\Class-OG-Final\models\e5-finetuned-v3",
    "/srv/app/models/e5-finetuned-v3",
    "models/e5-finetuned-v3",
])
def test_legacy_absolute_path_resolved_by_last_component(models_dir, legacy):
    (models_dir / "e5-finetuned-v3").mkdir()
    assert config.resolve_model_path(legacy, 3) == str(models_dir / "e5-finetuned-v3")


@pytest.mark.parametrize("path", ["e5-finetuned-v3", r"D:\old\models\e5-finetuned-v3"])
def test_missing_folder_clear_error(models_dir, path):
    with pytest.raises(RuntimeError) as exc:
        config.resolve_model_path(path, 3)
    msg = str(exc.value)
    assert "нет папки модели v3" in msg and f"MODELS_DIR={models_dir}" in msg


def test_existing_absolute_dir_outside_models_dir_returned_as_is(models_dir, tmp_path):
    offline = tmp_path / "offline" / "multilingual-e5-base"
    offline.mkdir(parents=True)
    assert config.resolve_model_path(str(offline), 0) == str(offline)
    assert config.resolve_model_path(str(offline), 2) == str(offline)


def test_models_dir_copy_wins_over_absolute_path(models_dir, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "e5-finetuned-v3"
    elsewhere.mkdir(parents=True)
    (models_dir / "e5-finetuned-v3").mkdir()
    assert config.resolve_model_path(str(elsewhere), 3) == str(models_dir / "e5-finetuned-v3")


def test_existing_relative_dir_returned_as_is(models_dir, tmp_path, monkeypatch):
    (tmp_path / "offline_bundle" / "models" / "bundle-model").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert config.resolve_model_path("offline_bundle/models/bundle-model", 0) == \
        "offline_bundle/models/bundle-model"


def test_version_zero_bare_name_passes_through(models_dir):
    assert config.resolve_model_path("all-MiniLM-L6-v2", 0) == "all-MiniLM-L6-v2"

"""Правки Docker под свежий Linux-стенд: CPU-torch, libgomp, .env, host gateway.

Сборка прода на чистом клоне не должна тянуть CUDA-колесо и не должна
ссылаться на gitignored-пути. Compose должен монтировать хостовый .env
и давать контейнеру имя host.docker.internal.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_prod_dockerfile_installs_cpu_torch():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "download.pytorch.org/whl/cpu" in text, (
        "прод-Dockerfile должен ставить CPU-сборку torch до requirements.txt — "
        "иначе pip тянет CUDA (~8 ГБ) на сервер без GPU"
    )


def test_prod_dockerfile_installs_libgomp():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "libgomp1" in text, (
        "python-slim без libgomp1 падает на numpy/torch: libgomp.so.1 not found"
    )


def test_compose_mounts_host_env():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./.env:/app/.env" in text.replace(" ", ""), (
        "без тома .env бэк-офис пишет настройки внутрь контейнера, "
        "и они пропадают при recreate"
    )


def test_compose_has_host_gateway():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "host.docker.internal" in text
    assert "host-gateway" in text


def test_compose_vector_db_is_writable():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "vector_db_adapted_v3:/app/data/vector_db_adapted_v3" in text.replace(" ", "")
    assert "vector_db_adapted_v3:/app/data/vector_db_adapted_v3:ro" not in text.replace(" ", ""), (
        ":ro на боевой базе блокирует rebuild/adapter apply внутри контейнера"
    )


def test_dockerignore_excludes_heavy_paths():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for path in ("offline_bundle/", ".hf-cache/", "node_modules/", "Template/"):
        assert path in text, f".dockerignore должен исключать {path}"


def test_py313check_does_not_copy_gitignored_vector_db():
    text = (ROOT / "Dockerfile.py313check").read_text(encoding="utf-8")
    assert "COPY data/vector_db/" not in text, (
        "data/vector_db/ в .gitignore — COPY уронит сборку на чистом клоне"
    )
    assert "COPY data/vector_db_adapted_v3/" in text

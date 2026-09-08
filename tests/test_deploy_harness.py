"""Обвязка развёртывания должна нести те же правки, что и код.

Код уезжает на стенд не сам по себе, а вместе с образом, `.env.example` и
установщиками. Каждый раз, когда появляется новая настройка или новый провайдер,
эти три места отстают молча: сборка проходит, сервис поднимается — и работает не
так, как проверяли. Здесь фиксируются те связи, которые уже расходились.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import settings_store
from classifier_agent import SUPPORTED_LLM_PROVIDERS

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
INSTALL_BAT = (ROOT / "install.bat").read_text(encoding="utf-8", errors="replace")


def env_value(key: str) -> str | None:
    match = re.search(rf"^\s*{key}=(.*)$", ENV_EXAMPLE, re.MULTILINE)
    return match.group(1).strip() if match else None


# ── .env.example едет в образ как рабочий .env ──────────────────────────────

def test_image_default_provider_is_one_we_actually_run():
    """`docker run` без смонтированного .env берёт запечённый .env.example.

    С `groq` он поднимется на провайдере, ключа к которому на стенде нет.
    """
    assert env_value("LLM_PROVIDER") in ("ario", "custom")


@pytest.mark.parametrize("key", [
    "API_PORT",            # порт лаунчеров
    "RX_VIA_PROXY",        # обход системного прокси
    "LLM_VIA_PROXY",
    "LLM_TIMEOUT_SEC",     # 300 с вместо прежних жёстких 120
    "JOB_TTL_HOURS",       # срок хранения результатов асинхронных задач
    "JOB_MAX_QUEUED",
    "VECTOR_DB_DIR",
    "HOST_PORT",           # публикуемый порт docker-compose
    "HF_CACHE_DIR",
])
def test_setting_is_declared_in_the_example(key):
    assert env_value(key) is not None, f"{key} не объявлен в .env.example"


def test_every_backoffice_field_is_in_the_example():
    """Бэк-офис правит .env по этим ключам — их не должно приходиться дописывать."""
    missing = [key for key in settings_store.EDITABLE_KEYS if env_value(key) is None]
    assert not missing, f"нет в .env.example: {missing}"


def test_vector_db_matches_the_image_and_the_mount():
    """База в .env.example, в COPY образа и в монтировании compose — одна и та же."""
    db = (env_value("VECTOR_DB_DIR") or "").strip("/")
    assert db, "VECTOR_DB_DIR не задан"
    assert db in DOCKERFILE.replace("\\", "/"), "образ копирует другую базу"
    assert db in COMPOSE.replace("\\", "/"), "compose монтирует другую базу"


# ── Образ ───────────────────────────────────────────────────────────────────

def test_image_copies_the_whole_src():
    """Пофайловый COPY означал бы, что новый модуль в образ не попадёт."""
    assert re.search(r"^COPY\s+src/\s+\./src/", DOCKERFILE, re.MULTILINE)


def test_real_env_never_enters_the_image():
    """В .env лежат ключи и пароль RX — в слое образа им не место."""
    assert re.search(r"^\.env$", DOCKERIGNORE, re.MULTILINE)


def test_compose_mounts_the_host_env():
    """Иначе настройки из бэк-офиса пропадают при пересоздании контейнера."""
    assert "./.env:/app/.env" in COMPOSE


# ── Установщик Windows ──────────────────────────────────────────────────────

def test_installer_offers_every_supported_provider():
    """Через `custom` подключается модель Заказчика — без него установка неполна."""
    missing = [p for p in SUPPORTED_LLM_PROVIDERS if p not in INSTALL_BAT.lower()]
    assert not missing, f"install.bat не предлагает: {missing}"


def test_installer_does_not_name_a_retired_model():
    """Набор моделей на endpoint меняется, конкретное имя устаревает."""
    assert "Qwen3.6-35B-A3B" not in INSTALL_BAT

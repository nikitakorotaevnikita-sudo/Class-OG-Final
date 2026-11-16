"""DATABASE_URL по умолчанию — из учётных данных compose и порта.

operator_cli, test_agent и `launch check` идут мимо start-скриптов. Без этой
сборки config взял бы localhost:5432 — на машине с «родным» PostgreSQL это чужой
сервер, а не контейнер проекта на POSTGRES_PORT.
"""

import os
import subprocess
import sys
from pathlib import Path

from core.service_urls import database_url

ROOT = Path(__file__).resolve().parent.parent


def test_defaults_when_nothing_is_set():
    assert database_url({}) == "postgresql://classifier:classifier@localhost:5432/classifier"


def test_built_from_credentials_and_port():
    env = {"POSTGRES_USER": "u1", "POSTGRES_PASSWORD": "p1", "POSTGRES_DB": "d1",
           "POSTGRES_PORT": "5434"}
    assert database_url(env) == "postgresql://u1:p1@localhost:5434/d1"


def test_explicit_url_wins():
    env = {"DATABASE_URL": "postgresql://a:b@db.local:6000/x", "POSTGRES_PORT": "5434"}
    assert database_url(env) == "postgresql://a:b@db.local:6000/x"


def test_empty_values_count_as_unset():
    """.env.example держит ключи пустыми — установщик заполняет их сам."""
    env = {"DATABASE_URL": "  ", "POSTGRES_PORT": "", "POSTGRES_PASSWORD": ""}
    assert database_url(env) == "postgresql://classifier:classifier@localhost:5432/classifier"


def test_special_characters_in_credentials_are_url_encoded():
    env = {"POSTGRES_USER": "us@r", "POSTGRES_PASSWORD": "p@ss:w/rd#1"}
    assert database_url(env) == "postgresql://us%40r:p%40ss%3Aw%2Frd%231@localhost:5432/classifier"


def test_config_uses_the_builder():
    """config.DATABASE_URL берётся из service_urls (в отдельном процессе: config читает
    окружение один раз, при импорте; настоящий .env не подменяется)."""
    code = ("import sys; sys.path.insert(0, 'src'); from core import config;"
            " print(config.DATABASE_URL)")
    env = {k: v for k, v in os.environ.items()}
    env.update({"DATABASE_URL": "", "POSTGRES_USER": "cu", "POSTGRES_PASSWORD": "cp",
                "POSTGRES_DB": "cd", "POSTGRES_PORT": "5499"})
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "postgresql://cu:cp@localhost:5499/cd"

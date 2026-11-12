"""DATABASE_URL / RABBITMQ_URL по умолчанию — из учётных данных compose и портов.

operator_cli, test_agent и `launch check` идут мимо start-скриптов. Без этой
сборки config взял бы localhost:5432/5672 — на машине с «родными» PostgreSQL /
RabbitMQ это чужие серверы, а не контейнеры проекта на POSTGRES_PORT / RABBITMQ_PORT.
"""

import os
import subprocess
import sys
from pathlib import Path

from service_urls import database_url, rabbitmq_url

ROOT = Path(__file__).resolve().parent.parent


def test_defaults_when_nothing_is_set():
    assert database_url({}) == "postgresql://classifier:classifier@localhost:5432/classifier"
    assert rabbitmq_url({}) == "amqp://classifier:classifier@localhost:5672//"


def test_built_from_credentials_and_ports():
    env = {"POSTGRES_USER": "u1", "POSTGRES_PASSWORD": "p1", "POSTGRES_DB": "d1",
           "POSTGRES_PORT": "5434", "RABBITMQ_DEFAULT_USER": "u2",
           "RABBITMQ_DEFAULT_PASS": "p2", "RABBITMQ_PORT": "5673"}
    assert database_url(env) == "postgresql://u1:p1@localhost:5434/d1"
    assert rabbitmq_url(env) == "amqp://u2:p2@localhost:5673//"


def test_explicit_urls_win():
    env = {"DATABASE_URL": "postgresql://a:b@db.local:6000/x", "POSTGRES_PORT": "5434",
           "RABBITMQ_URL": "amqp://a:b@mq.local:6001//", "RABBITMQ_PORT": "5673"}
    assert database_url(env) == "postgresql://a:b@db.local:6000/x"
    assert rabbitmq_url(env) == "amqp://a:b@mq.local:6001//"


def test_empty_values_count_as_unset():
    """.env.example держит ключи пустыми — установщик заполняет их сам."""
    env = {"DATABASE_URL": "", "RABBITMQ_URL": "  ", "POSTGRES_PORT": "",
           "RABBITMQ_PORT": "", "POSTGRES_PASSWORD": ""}
    assert database_url(env) == "postgresql://classifier:classifier@localhost:5432/classifier"
    assert rabbitmq_url(env) == "amqp://classifier:classifier@localhost:5672//"


def test_special_characters_in_credentials_are_url_encoded():
    env = {"POSTGRES_USER": "us@r", "POSTGRES_PASSWORD": "p@ss:w/rd#1",
           "RABBITMQ_DEFAULT_USER": "mq", "RABBITMQ_DEFAULT_PASS": "a/b@c:d"}
    assert database_url(env) == "postgresql://us%40r:p%40ss%3Aw%2Frd%231@localhost:5432/classifier"
    assert rabbitmq_url(env) == "amqp://mq:a%2Fb%40c%3Ad@localhost:5672//"


def test_config_uses_the_builder():
    """config.DATABASE_URL / RABBITMQ_URL берутся из service_urls (в отдельном процессе:
    config читает окружение один раз, при импорте; настоящий .env не подменяется)."""
    code = ("import sys; sys.path.insert(0, 'src'); import config;"
            " print(config.DATABASE_URL); print(config.RABBITMQ_URL)")
    env = {k: v for k, v in os.environ.items()}
    env.update({"DATABASE_URL": "", "RABBITMQ_URL": "", "POSTGRES_USER": "cu",
                "POSTGRES_PASSWORD": "cp", "POSTGRES_DB": "cd", "POSTGRES_PORT": "5499",
                "RABBITMQ_DEFAULT_USER": "ru", "RABBITMQ_DEFAULT_PASS": "rp",
                "RABBITMQ_PORT": "5698"})
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[-2] == "postgresql://cu:cp@localhost:5499/cd"
    assert lines[-1] == "amqp://ru:rp@localhost:5698//"

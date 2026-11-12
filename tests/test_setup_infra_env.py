"""scripts/setup_infra_env.py — порты PostgreSQL/RabbitMQ на хосте и URL в .env.

Установщик выбирает свободные порты (на машине могут работать «родные»
PostgreSQL :5432 / RabbitMQ :5672) и пишет их в .env вместе с DATABASE_URL /
RABBITMQ_URL. Файл с ключами правится бережно: кодировка, BOM, концы строк и
комментарии сохраняются, пароли не печатаются.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "setup_infra_env.py"

_spec = importlib.util.spec_from_file_location("setup_infra_env", SCRIPT)
infra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(infra)


def free_except(*busy):
    return lambda port: port not in busy


# ── Выбор порта ─────────────────────────────────────────────────────────────

def test_existing_free_value_is_kept():
    port, why = infra.choose_port("5440", 5432, [5434], is_free=free_except(), ours=None)
    assert port == 5440 and "env" in why


def test_existing_value_held_by_own_container_is_kept():
    port, why = infra.choose_port("5440", 5432, [5434], is_free=free_except(5440), ours=5440)
    assert port == 5440 and "env" in why


def test_existing_default_value_busy_is_treated_as_unset():
    """.env из старого .env.example держит POSTGRES_PORT=5432; на хосте с «родным»
    PostgreSQL этот порт занят — выбирается запасной, а не молча сохраняется."""
    port, why = infra.choose_port("5432", 5432, [5434, 5435], is_free=free_except(5432, 5434),
                                  ours=None)
    assert port == 5435
    assert "5432" in why and "busy" in why


def test_existing_custom_value_busy_is_an_error():
    with pytest.raises(infra.PortError) as err:
        infra.choose_port("5440", 5432, [5434], is_free=free_except(5440), ours=None)
    assert "5440" in str(err.value) and "busy" in str(err.value)


@pytest.mark.parametrize("value", ["54x2", "0", "70000", "-1", "5432.0"])
def test_existing_value_must_be_a_port_number(value):
    with pytest.raises(infra.PortError):
        infra.choose_port(value, 5432, [5434], is_free=free_except(), ours=None)


def test_default_used_when_free():
    port, _ = infra.choose_port("", 5432, [5434, 5435], is_free=free_except(), ours=None)
    assert port == 5432


def test_first_free_fallback_when_default_busy():
    port, why = infra.choose_port("", 5432, [5434, 5435, 5436],
                                  is_free=free_except(5432, 5434), ours=None)
    assert port == 5435 and "5432" in why


def test_port_of_own_container_is_kept():
    """Порт, который держит контейнер ЭТОГО проекта, — свой: не «занят»."""
    port, why = infra.choose_port("", 5432, [5434], is_free=free_except(5432, 5434), ours=5434)
    assert port == 5434 and "container" in why


def test_no_free_port_raises():
    with pytest.raises(infra.NoFreePort):
        infra.choose_port("", 5432, [5434], is_free=lambda p: False, ours=None)


def test_fallback_lists_skip_the_test_database_port():
    """5433 занят тестовым PostgreSQL (TEST_DATABASE_URL) — список начинается с 5434."""
    assert infra.PORTS["POSTGRES_PORT"][0] == 5432
    assert infra.PORTS["POSTGRES_PORT"][1][:2] == [5434, 5435]
    assert infra.PORTS["RABBITMQ_PORT"][0] == 5672
    assert infra.PORTS["RABBITMQ_PORT"][1][:2] == [5673, 5674]
    assert infra.PORTS["RABBITMQ_MGMT_PORT"][0] == 15672
    assert infra.PORTS["RABBITMQ_MGMT_PORT"][1][:2] == [15673, 15674]


# ── Порты своих контейнеров из docker compose ps ────────────────────────────

PS_ITEMS = [
    {"Service": "postgres", "State": "running",
     "Publishers": [{"URL": "127.0.0.1", "TargetPort": 5432, "PublishedPort": 5434,
                     "Protocol": "tcp"}]},
    {"Service": "rabbitmq", "State": "running",
     "Publishers": [{"URL": "127.0.0.1", "TargetPort": 5672, "PublishedPort": 5673,
                     "Protocol": "tcp"},
                    {"URL": "127.0.0.1", "TargetPort": 15672, "PublishedPort": 15673,
                     "Protocol": "tcp"},
                    {"URL": "", "TargetPort": 4369, "PublishedPort": 0, "Protocol": "tcp"}]},
    {"Service": "api", "State": "running",
     "Publishers": [{"URL": "0.0.0.0", "TargetPort": 8005, "PublishedPort": 8010,
                     "Protocol": "tcp"}]},
]


@pytest.mark.parametrize("output", [
    json.dumps(PS_ITEMS),                                # старый compose: массив
    "\n".join(json.dumps(item) for item in PS_ITEMS),     # новый: объект на строку
])
def test_own_ports_parsed_from_compose_ps(output):
    assert infra.parse_compose_ps(output) == {
        "POSTGRES_PORT": 5434, "RABBITMQ_PORT": 5673, "RABBITMQ_MGMT_PORT": 15673}


def test_compose_ps_garbage_gives_nothing():
    assert infra.parse_compose_ps("") == {}
    assert infra.parse_compose_ps("not json") == {}


# ── Разбор и правка .env ────────────────────────────────────────────────────

def test_parse_env_strips_comments_and_quotes():
    text = ("# POSTGRES_PORT=1111\nPOSTGRES_PORT=5434  # host port\n"
            'POSTGRES_PASSWORD="se#cret"\nEMPTY=\n')
    env = infra.parse_env(text)
    assert env["POSTGRES_PORT"] == "5434"
    assert env["POSTGRES_PASSWORD"] == "se#cret"
    assert env["EMPTY"] == ""


def test_set_keys_replaces_and_appends_keeping_comments():
    text = "# POSTGRES_PORT=1\r\nPOSTGRES_PORT=\r\nOTHER=x\r\n"
    out = infra.set_keys(text, {"POSTGRES_PORT": "5434", "RABBITMQ_PORT": "5673"}, "\r\n")
    assert out == "# POSTGRES_PORT=1\r\nPOSTGRES_PORT=5434\r\nOTHER=x\r\nRABBITMQ_PORT=5673\r\n"


def test_set_keys_adds_newline_before_append_when_missing():
    out = infra.set_keys("A=1", {"B": "2"}, "\n")
    assert out == "A=1\nB=2\n"


def run_main(env_path, *extra, busy=(), ours=None):
    return infra.main(["--env-file", str(env_path), *extra],
                      is_free=free_except(*busy), own_ports=lambda: dict(ours or {}))


def test_main_writes_ports_and_urls_preserving_bom_and_crlf(tmp_path, capsys):
    env = tmp_path / ".env"
    original = ("# Настройки\r\nPOSTGRES_USER=classifier\r\nPOSTGRES_PASSWORD=s3cr@t:pw\r\n"
                "POSTGRES_DB=classifier\r\nPOSTGRES_PORT=\r\nDATABASE_URL=\r\n"
                "RABBITMQ_DEFAULT_USER=mq\r\nRABBITMQ_DEFAULT_PASS=topsecret\r\n")
    env.write_bytes(b"\xef\xbb\xbf" + original.encode("utf-8"))

    assert run_main(env, busy=(5432, 5672, 15672)) == 0

    raw = env.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8")
    assert "\n" not in text.replace("\r\n", "")
    assert text.startswith("# Настройки\r\n")
    values = infra.parse_env(text)
    assert values["POSTGRES_PORT"] == "5434"
    assert values["RABBITMQ_PORT"] == "5673"
    assert values["RABBITMQ_MGMT_PORT"] == "15673"
    assert values["DATABASE_URL"] == "postgresql://classifier:s3cr%40t%3Apw@localhost:5434/classifier"
    assert values["RABBITMQ_URL"] == "amqp://mq:topsecret@localhost:5673//"

    out = capsys.readouterr()
    printed = out.out + out.err
    assert "5434" in printed and "5673" in printed and "15673" in printed
    for secret in ("s3cr", "topsecret", "%40"):
        assert secret not in printed


def test_main_without_bom_writes_no_bom_and_keeps_lf(tmp_path):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=5434\nRABBITMQ_PORT=5673\nRABBITMQ_MGMT_PORT=15673\n",
                   encoding="utf-8", newline="")
    assert run_main(env) == 0
    raw = env.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    values = infra.parse_env(raw.decode("utf-8"))
    assert values["POSTGRES_PORT"] == "5434"  # своё значение не трогается
    assert values["DATABASE_URL"] == "postgresql://classifier:classifier@localhost:5434/classifier"
    assert values["RABBITMQ_URL"] == "amqp://classifier:classifier@localhost:5673//"


def test_main_keeps_external_database_url(tmp_path):
    """DATABASE_URL на другую машину — осознанная настройка, не переписывается."""
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://a:b@db.example:5432/x\n"
                   "RABBITMQ_URL=amqp://a:b@localhost:5672//\n", encoding="utf-8")
    assert run_main(env, busy=(5432,)) == 0
    values = infra.parse_env(env.read_text(encoding="utf-8"))
    assert values["DATABASE_URL"] == "postgresql://a:b@db.example:5432/x"
    # локальный адрес — пересобирается под выбранный порт
    assert values["RABBITMQ_URL"] == "amqp://classifier:classifier@localhost:5672//"


def test_main_uses_own_container_ports(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=ario\n", encoding="utf-8")
    assert run_main(env, busy=(5434, 5673, 15673),
                    ours={"POSTGRES_PORT": 5434, "RABBITMQ_PORT": 5673,
                          "RABBITMQ_MGMT_PORT": 15673}) == 0
    values = infra.parse_env(env.read_text(encoding="utf-8"))
    assert (values["POSTGRES_PORT"], values["RABBITMQ_PORT"],
            values["RABBITMQ_MGMT_PORT"]) == ("5434", "5673", "15673")


def test_main_is_idempotent(tmp_path):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=\n", encoding="utf-8")
    assert run_main(env, busy=(5432,)) == 0
    first = env.read_bytes()
    # теперь 5434 держит наш контейнер
    assert run_main(env, busy=(5432, 5434), ours={"POSTGRES_PORT": 5434}) == 0
    assert env.read_bytes() == first


def test_main_dry_run_does_not_write(tmp_path):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=\n", encoding="utf-8")
    assert run_main(env, "--dry-run") == 0
    assert env.read_text(encoding="utf-8") == "POSTGRES_PORT=\n"


def test_main_missing_env_file_fails(tmp_path, capsys):
    assert run_main(tmp_path / ".env") == 2


def test_main_no_free_port_fails_without_writing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    code = infra.main(["--env-file", str(env)], is_free=lambda p: False, own_ports=dict)
    assert code == 1
    assert env.read_text(encoding="utf-8") == "A=1\n"


def test_cli_runs_as_script_on_a_copy(tmp_path):
    """Запуск как у установщика (интерпретатор venv, без Docker) — на копии .env."""
    env = tmp_path / ".env"
    # Редкие порты: 5434/5673 может держать живой стек проекта, а --no-docker
    # не узнает в нём «свой» контейнер.
    env.write_text("POSTGRES_PORT=5451\nRABBITMQ_PORT=5691\nRABBITMQ_MGMT_PORT=15691\n",
                   encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--env-file", str(env), "--no-docker"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "DATABASE_URL=postgresql://classifier:classifier@localhost:5451/classifier" in \
        env.read_text(encoding="utf-8")


# ── Fix round 1: занятый порт из .env, кодировка, комментарии, сокет ────────

def test_main_busy_custom_port_from_env_stops_with_clear_error(tmp_path, capsys):
    env = tmp_path / ".env"
    original = "POSTGRES_PASSWORD=topsecret\nPOSTGRES_PORT=5440\n"
    env.write_text(original, encoding="utf-8")
    assert run_main(env, busy=(5440,)) == 1
    assert env.read_text(encoding="utf-8") == original
    out = capsys.readouterr()
    printed = out.out + out.err
    assert "POSTGRES_PORT" in printed and "5440" in printed
    assert "topsecret" not in printed


def test_main_busy_default_port_from_old_template_gets_a_fallback(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=5432\nRABBITMQ_PORT=5672\nRABBITMQ_MGMT_PORT=15672\n"
                   "DATABASE_URL=postgresql://classifier:classifier@localhost:5432/classifier\n",
                   encoding="utf-8")
    assert run_main(env, busy=(5432, 5672, 15672)) == 0
    values = infra.parse_env(env.read_text(encoding="utf-8"))
    assert (values["POSTGRES_PORT"], values["RABBITMQ_PORT"],
            values["RABBITMQ_MGMT_PORT"]) == ("5434", "5673", "15673")
    assert values["DATABASE_URL"] == "postgresql://classifier:classifier@localhost:5434/classifier"
    printed = capsys.readouterr().out
    assert "busy" in printed and "5434" in printed


def test_main_invalid_port_value_fails(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=54x2\n", encoding="utf-8")
    assert run_main(env) == 1
    assert "POSTGRES_PORT" in capsys.readouterr().err


def test_main_non_utf8_env_gives_one_line_error(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_bytes("POSTGRES_PASSWORD=пароль\n".encode("cp1251"))
    assert run_main(env) == 2
    err = capsys.readouterr().err
    assert "UTF-8" in err and "Traceback" not in err
    assert len(err.strip().splitlines()) == 1


def test_set_keys_keeps_trailing_inline_comment():
    out = infra.set_keys("POSTGRES_PORT=5432  # host port\n", {"POSTGRES_PORT": "5434"}, "\n")
    assert out == "POSTGRES_PORT=5434  # host port\n"


def test_main_keeps_unix_socket_database_url(tmp_path):
    """Пустой hostname (unix-сокет) — не «localhost»: такой адрес не переписывается на TCP."""
    env = tmp_path / ".env"
    url = "postgresql:///classifier?host=/run/postgresql"
    env.write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    assert run_main(env) == 0
    assert infra.parse_env(env.read_text(encoding="utf-8"))["DATABASE_URL"] == url


def test_main_reports_replaced_local_url_without_secrets(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=5434\n"
                   "DATABASE_URL=postgresql://native:n4tivepw@localhost:5432/other\n",
                   encoding="utf-8")
    assert run_main(env) == 0
    printed = capsys.readouterr().out
    line = next(l for l in printed.splitlines() if "DATABASE_URL" in l)
    assert "replaced" in line and "localhost:5432" in line
    assert "n4tivepw" not in printed and "native" not in printed


def test_main_does_not_report_replacement_when_url_unchanged(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=5434\n"
                   "DATABASE_URL=postgresql://classifier:classifier@localhost:5434/classifier\n",
                   encoding="utf-8")
    assert run_main(env) == 0
    assert "replaced" not in capsys.readouterr().out


def test_bom_is_written_as_an_escape_in_source():
    assert infra.BOM == chr(0xFEFF)
    assert chr(0xFEFF) not in SCRIPT.read_text(encoding="utf-8")


# ── WP2b: повторная проверка ────────────────────────────────────────────────

def test_busy_default_prefers_own_container_port():
    """.env держит занятый чужим 5432, а свой контейнер уже публикует 5434:
    берётся 5434 (контейнер не пересоздаётся на новом порту)."""
    port, why = infra.choose_port("5432", 5432, [5434, 5435],
                                  is_free=free_except(5432, 5434), ours=5434)
    assert port == 5434
    assert "5432" in why and "container" in why


def test_value_that_is_only_a_comment_parses_as_empty():
    env = infra.parse_env("POSTGRES_PORT= # host port\nDATABASE_URL=   # url\n"
                          "POSTGRES_PASSWORD=#secret\n")
    assert env["POSTGRES_PORT"] == ""
    assert env["DATABASE_URL"] == ""
    assert env["POSTGRES_PASSWORD"] == "#secret"  # без пробела перед # — это значение


def test_main_builds_url_when_value_is_only_a_comment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=5434\nDATABASE_URL= # set by installer\n", encoding="utf-8")
    assert run_main(env) == 0
    text = env.read_text(encoding="utf-8")
    assert ("DATABASE_URL=postgresql://classifier:classifier@localhost:5434/classifier"
            in text)


def test_main_reports_replaced_url_with_bad_port_without_traceback(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PORT=5434\n"
                   "DATABASE_URL=postgresql://u:pw@localhost:abc/db\n", encoding="utf-8")
    assert run_main(env) == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "DATABASE_URL" in l)
    assert "replaced" in line and "localhost" in line
    assert "pw" not in line.split("(", 1)[1]

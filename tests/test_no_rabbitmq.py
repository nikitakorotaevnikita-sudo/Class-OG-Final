"""RabbitMQ / Celery убраны из кода, compose, образа, зависимостей, .env.example
и скриптов запуска, остановки и установки (.cmd/.bat/.sh/.ps1)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = re.compile(r"rabbit|celery|kombu|amqp", re.IGNORECASE)

NEW_KEYS = ("QUEUE_POLL_SEC", "JOB_LEASE_SEC", "JOB_DELIVERED_GRACE_SEC")
REMOVED_KEYS = ("RABBITMQ_URL", "RABBITMQ_PORT", "RABBITMQ_MGMT_PORT", "RABBITMQ_BIND",
                "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS", "BROKER_HEARTBEAT_SEC",
                "WORKER_PROC_ALIVE_TIMEOUT_SEC", "CLASSIFY_CONCURRENCY")


def _files():
    files = [p for p in (ROOT / "src").rglob("*.py")]
    files += sorted((ROOT / "scripts").glob("*.py"))
    files += [ROOT / n for n in ("docker-compose.yml", "Dockerfile", "requirements.txt",
                                 ".env.example", ".gitattributes")]
    files += SCRIPT_FILES
    return files


SCRIPT_FILES = [ROOT / n for n in (
    "start.cmd", "stop.cmd", "launch.bat", "install.bat", "install_offline.bat",
    "scripts/setup_infra.cmd", "start.sh", "stop.sh", "launch.sh", "install.sh",
    "install_offline.sh", "scripts/install_common.sh", "setup.ps1")]


def test_every_project_script_is_checked():
    """Новый скрипт в корне или scripts/ не должен пройти мимо проверки."""
    found = set()
    for pattern in ("*.cmd", "*.bat", "*.sh", "*.ps1"):
        found |= set(ROOT.glob(pattern)) | set((ROOT / "scripts").glob(pattern))
    assert found <= set(SCRIPT_FILES), sorted(str(p) for p in found - set(SCRIPT_FILES))


def test_no_rabbit_celery_kombu_mentions():
    bad = []
    for path in _files():
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{no}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_rabbitmq_deploy_dir_removed():
    assert not (ROOT / "deploy" / "rabbitmq").exists()


def test_env_example_has_new_keys_and_no_removed_ones():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in NEW_KEYS:
        assert len(re.findall(rf"^{key}=", text, re.MULTILINE)) == 1, key
    for key in REMOVED_KEYS:
        assert not re.search(rf"^\s*#?\s*{key}\b", text, re.MULTILINE), key


def test_config_has_no_removed_keys():
    from core import config

    for name in REMOVED_KEYS:
        assert not hasattr(config, name), name


def test_requirements_without_celery():
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(celery|kombu)\b", reqs, re.IGNORECASE | re.MULTILINE)

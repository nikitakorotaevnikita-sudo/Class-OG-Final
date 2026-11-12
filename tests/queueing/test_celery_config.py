"""Конфигурация Celery: quorum-очереди, acks_late, маршруты."""
from queueing.celery_app import app


def test_queues_are_quorum():
    queues = {q.name: q for q in app.conf.task_queues}
    assert set(queues) == {"classify_sync", "classify", "training"}
    for q in queues.values():
        assert q.queue_arguments["x-queue-type"] == "quorum"


def test_acks_late_and_prefetch_one():
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_ignore_result is True
    assert app.conf.broker_connection_retry_on_startup is True


def test_routes():
    assert app.conf.task_default_queue == "classify"
    routes = app.conf.task_routes
    assert routes["classify_appeal"] == {"queue": "classify"}
    assert routes["search_classifier"] == {"queue": "classify_sync"}
    assert routes["embed_annotation"] == {"queue": "classify"}
    assert routes["finetune"] == {"queue": "training"}


def test_short_broker_timeouts_for_publishing():
    """API не должен зависать на отправке, когда RabbitMQ недоступен."""
    assert app.conf.broker_connection_timeout == 3
    assert app.conf.task_publish_retry_policy == {
        "max_retries": 2, "interval_start": 0, "interval_step": 0.5, "interval_max": 1,
    }


def test_broker_heartbeat_from_config_default_disabled():
    """solo-пул не шлёт heartbeat во время задачи — по умолчанию heartbeat выключен."""
    import config

    assert config.BROKER_HEARTBEAT_SEC == 0
    assert app.conf.broker_heartbeat == config.BROKER_HEARTBEAT_SEC


def test_worker_proc_alive_timeout_from_config():
    """Страховка prefork: дочерний процесс не убивается за долгую инициализацию."""
    import config

    from pathlib import Path

    # Дефолт кода, независимо от .env разработчика.
    src = (Path(__file__).resolve().parents[2] / "src" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("WORKER_PROC_ALIVE_TIMEOUT_SEC", "300")' in src
    assert app.conf.worker_proc_alive_timeout == config.WORKER_PROC_ALIVE_TIMEOUT_SEC


def test_env_example_documents_worker_proc_alive_timeout():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "WORKER_PROC_ALIVE_TIMEOUT_SEC=300" in text.splitlines()

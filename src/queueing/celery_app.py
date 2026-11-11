"""Celery-приложение (брокер RabbitMQ, quorum-очереди).

Запуск воркера (src должен быть в PYTHONPATH):

    PYTHONPATH=src celery -A queueing.celery_app worker -Q classify_sync,classify -P solo

Подключения при импорте нет — брокер нужен только при отправке/приёме задач.

Задачи (`classify_appeal`, `search_classifier`, `embed_annotation`, `finetune`)
объявлены в модуле `queueing.tasks` с `name=`; воркер импортирует его через
`include`. Отправителю (API) импорт модуля задач не нужен: маршруты работают
по строковым именам.
"""
from celery import Celery
from kombu import Queue

import config

QUEUE_NAMES = ("classify_sync", "classify", "training")


def _quorum(name: str) -> Queue:
    return Queue(name, routing_key=name, queue_arguments={"x-queue-type": "quorum"})


app = Celery("classifier", broker=config.RABBITMQ_URL, include=["queueing.tasks"])

app.conf.update(
    task_queues=[_quorum(n) for n in QUEUE_NAMES],
    task_default_queue="classify",
    task_default_exchange="classify",
    task_default_routing_key="classify",
    task_routes={
        "classify_appeal": {"queue": "classify"},
        "search_classifier": {"queue": "classify_sync"},
        "embed_annotation": {"queue": "classify"},
        "finetune": {"queue": "training"},
    },
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    # Короткие таймауты отправки: при недоступном RabbitMQ запрос API не должен
    # висеть — диспетчер превращает сбой в DispatchError (503).
    broker_connection_timeout=3,
    task_publish_retry_policy={
        "max_retries": 2, "interval_start": 0, "interval_step": 0.5, "interval_max": 1,
    },
    broker_transport_options={"confirm_publish": True},
    worker_detect_quorum_queues=True,
)

"""Celery-приложение (брокер RabbitMQ, quorum-очереди).

Запуск воркера (src должен быть в PYTHONPATH):

    PYTHONPATH=src celery -A queueing.celery_app worker -Q classify_sync,classify -P solo

Подключения при импорте нет — брокер нужен только при отправке/приёме задач.

Задачи (`classify_appeal`, `search_classifier`, `embed_annotation`, `finetune`)
регистрирует Task 7 в модуле `queueing.tasks` с `name=`; `include` здесь
намеренно не задан — модуль подключит сам воркер (Task 7 добавит его в
`include`/`imports`). Маршруты работают по строковым именам и не требуют
импорта модуля задач.
"""
from celery import Celery
from kombu import Queue

import config

QUEUE_NAMES = ("classify_sync", "classify", "training")


def _quorum(name: str) -> Queue:
    return Queue(name, routing_key=name, queue_arguments={"x-queue-type": "quorum"})


app = Celery("classifier", broker=config.RABBITMQ_URL)

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
    broker_transport_options={"confirm_publish": True},
    worker_detect_quorum_queues=True,
)

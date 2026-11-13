"""Прогрев агента по сигналам Celery без опасного fork.

Модуль лёгкий: worker_main (а с ним classifier_agent, torch, модель)
импортируется только внутри обработчиков, в процессе, который будет
выполнять задачи.

- worker_process_init — в каждом дочернем процессе prefork. Только сброс
  состояния, унаследованного от родителя через fork (пул соединений БД,
  storage, агент). Модель здесь НЕ грузится: Celery убивает дочерний
  процесс, если обработчик не уложился в worker_proc_alive_timeout
  (по умолчанию 4 с, у нас WORKER_PROC_ALIVE_TIMEOUT_SEC), а загрузка
  SentenceTransformer занимает 15+ с → бесконечный цикл перезапуска детей.
  Агент создаётся лениво первой задачей (worker_main.get_agent).
- worker_ready — прогрев только для solo/threads (там worker_process_init
  не приходит, задачи выполняются в этом же процессе). В prefork этот сигнал
  приходит в РОДИТЕЛЕ: открытый там пул psycopg (сокеты, фоновые потоки) и
  загруженные torch/SentenceTransformer унаследовали бы дочерние процессы →
  PoolTimeout, зависшие задачи, взаимоблокировки OpenMP.
"""
import logging
import sys

from celery.signals import worker_process_init, worker_ready

log = logging.getLogger(__name__)


def _warm() -> None:
    try:
        from worker import worker_main  # лениво: тянет classifier_agent (модель)

        worker_main.get_agent()
    except Exception:
        log.exception("Прогрев агента не удался; повторится при первой задаче")


def pool_is_in_process(consumer) -> bool:
    """True, если задачи выполняются в этом же процессе (solo/threads), без fork."""
    from celery.concurrency import solo, thread

    pool = getattr(consumer, "pool", None)
    return isinstance(pool, (solo.TaskPool, thread.TaskPool))


def reset_inherited_state() -> None:
    """После fork: забыть пул БД, storage и агента родителя.

    Пул родителя не закрываем — его сокеты и потоки принадлежат родителю;
    здесь при первом обращении создадутся свои.
    """
    from storage import db, factory

    db._pool = None
    factory.reset_storage()
    worker_main = sys.modules.get("worker.worker_main")
    if worker_main is not None:
        worker_main._agent = None


def on_worker_process_init(sender=None, **_kwargs) -> None:
    # Без прогрева: см. docstring модуля (worker_proc_alive_timeout).
    reset_inherited_state()


def on_worker_ready(sender=None, **_kwargs) -> None:
    if pool_is_in_process(sender):
        _warm()
    else:
        log.info("Пул воркера с fork: модель загрузится в дочернем процессе "
                 "при первой задаче")


worker_process_init.connect(on_worker_process_init, weak=False)
worker_ready.connect(on_worker_ready, weak=False)

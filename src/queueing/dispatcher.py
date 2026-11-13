"""Отправка задач воркерам (сторона API).

- `CeleryDispatcher` — публикация в RabbitMQ (`send_task` по строковому имени;
  модуль задач и модель API не импортирует).
- `LocalDispatcher` — выполнение `tasks.run_*` в пуле потоков этого же процесса
  (тесты и `STORAGE_BACKEND=memory`): `await waiter.wait(...)` ведёт себя как в
  проде. Повторов нет — любой сбой завершает задачу ошибкой. Агент берётся из
  `agent_factory()` на каждую задачу (кэширует и перезагружает его worker_main).

Модуль импортирует API, поэтому classifier_agent/torch здесь не грузятся:
`worker_main` и `queueing.tasks` импортируются лениво.
"""
from __future__ import annotations

import logging
import threading
import time
from queue import SimpleQueue
from typing import Callable, Optional, Protocol

from kombu.exceptions import OperationalError as KombuOperationalError

from core import config
from storage.models import STATUS_DONE, STATUS_ERROR

log = logging.getLogger(__name__)


class DispatchError(Exception):
    """Задачу не удалось отправить (брокер недоступен и т. п.)."""


class Dispatcher(Protocol):
    def send(self, task_name: str, msg: dict, queue: str) -> None: ...


class CeleryDispatcher:
    """Публикация задачи в RabbitMQ через Celery."""

    def send(self, task_name: str, msg: dict, queue: str) -> None:
        from queueing.celery_app import app

        try:
            app.send_task(task_name, args=[msg], queue=queue)
        except (KombuOperationalError, OSError) as exc:
            raise DispatchError(f"брокер недоступен: {type(exc).__name__}: {exc}") from exc


# имя задачи → (функция в queueing.tasks, нужен ли агент)
_LOCAL_TASKS = {
    "classify_appeal": ("run_classify_appeal", True),
    "search_classifier": ("run_search_classifier", True),
    "embed_annotation": ("run_embed_annotation", True),
    "finetune": ("run_finetune", False),
}


class LocalDispatcher:
    """Выполняет задачи в ограниченном пуле daemon-потоков текущего процесса.

    Одновременно — не больше max_workers задач (по умолчанию
    CLASSIFY_CONCURRENCY, как у воркера; при 0 — 2), остальные ждут в очереди.
    Не ThreadPoolExecutor: его потоки не daemon, и зависшая задача не дала бы
    процессу (тестам) завершиться. Дообучение (finetune) в пул не входит:
    оно идёт в отдельном daemon-потоке, чтобы не занимать слот классификации.
    shutdown() дожидается начатых задач и останавливает потоки (тесты).
    """

    def __init__(self, storage, agent_factory: Callable[[], object],
                 max_workers: Optional[int] = None) -> None:
        self._storage = storage
        # Фабрика вызывается на каждую задачу: кэш агента, горячая перезагрузка
        # модели после дообучения и применение .env живут в worker_main.get_agent.
        self._agent_factory = agent_factory
        self.max_workers = max(1, max_workers or config.CLASSIFY_CONCURRENCY or 2)
        self._queue: "SimpleQueue[Optional[tuple[str, dict]]]" = SimpleQueue()
        self._threads: list[threading.Thread] = []
        self._finetune_threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()
        self._closed = False

    def send(self, task_name: str, msg: dict, queue: str) -> None:
        if task_name not in _LOCAL_TASKS:
            raise DispatchError(f"неизвестная задача: {task_name!r}")
        with self._threads_lock:
            if self._closed:
                raise DispatchError("локальный диспетчер остановлен")
            if task_name == "finetune":
                thread = threading.Thread(target=self._run_safe, args=(task_name, msg),
                                          daemon=True, name="local-dispatcher-finetune")
                self._finetune_threads.append(thread)
                thread.start()
                return
            self._queue.put((task_name, msg))
            if len(self._threads) < self.max_workers:
                thread = threading.Thread(
                    target=self._worker, daemon=True,
                    name=f"local-dispatcher-{len(self._threads) + 1}")
                self._threads.append(thread)
                thread.start()

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # сигнал shutdown()
                return
            self._run_safe(*item)

    def shutdown(self, timeout: float = 10.0) -> None:
        """Не принимать новых задач, доделать поставленные и остановить потоки.

        Ждёт не дольше timeout секунд в сумме; зависшая задача не держит
        процесс (потоки daemon).
        """
        with self._threads_lock:
            self._closed = True
            threads = list(self._threads)
            finetune = list(self._finetune_threads)
        for _ in threads:
            self._queue.put(None)  # после всех задач: очередь FIFO
        deadline = time.monotonic() + timeout
        for thread in threads + finetune:
            thread.join(max(0.0, deadline - time.monotonic()))

    def _run_safe(self, task_name: str, msg: dict) -> None:
        try:
            self._run(task_name, msg)
        except Exception:  # noqa: BLE001 — поток не должен умирать
            log.exception("Локальный диспетчер: сбой задачи %s", task_name)

    def _run(self, task_name: str, msg: dict) -> None:
        job_id = msg.get("job_id")
        error: Optional[str] = None
        try:
            from queueing import tasks

            fn_name, with_agent = _LOCAL_TASKS[task_name]
            fn = getattr(tasks, fn_name)
            if with_agent:
                fn(msg, self._storage, self._agent_factory())
            else:
                fn(msg, self._storage)
        except Exception as exc:  # noqa: BLE001 — в т.ч. RetryableError: повторов нет
            log.exception("Локальная задача %s (%s) упала", task_name, job_id)
            error = f"{type(exc).__name__}: {exc}"
        if not job_id:
            return
        # Задача должна стать конечной, иначе ожидатели повиснут до таймаута.
        try:
            job = self._storage.jobs.get(job_id)
            if job is not None and job.status not in (STATUS_DONE, STATUS_ERROR):
                self._storage.jobs.finish(
                    job_id, result=None,
                    error=error or "задача завершилась без результата",
                )
        except Exception:  # noqa: BLE001
            log.exception("Не удалось завершить локальную задачу %s", job_id)


def _worker_agent():
    from worker import worker_main  # лениво: тянет classifier_agent (модель)

    return worker_main.get_agent()


_dispatcher: Optional[Dispatcher] = None
_lock = threading.Lock()


def get_dispatcher() -> Dispatcher:
    """Singleton по STORAGE_BACKEND: memory → LocalDispatcher, иначе Celery."""
    global _dispatcher
    if _dispatcher is None:
        with _lock:
            if _dispatcher is None:
                if config.STORAGE_BACKEND == "memory":
                    from storage import get_storage

                    _dispatcher = LocalDispatcher(get_storage(), agent_factory=_worker_agent)
                else:
                    _dispatcher = CeleryDispatcher()
    return _dispatcher


def reset_dispatcher() -> None:
    global _dispatcher
    with _lock:
        _dispatcher = None

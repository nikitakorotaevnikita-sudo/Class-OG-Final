"""Воркер очереди задач в PostgreSQL (таблица jobs) — без брокера и Celery.

Запуск (из корня проекта):

    PYTHONPATH=src python -m worker.run --queues classify_sync,classify
    python src/worker/run.py --queues training

Один процесс — одна задача за раз (модель загружена один раз на процесс);
параллельность — числом процессов. Цикл:

1. Перед захватом: задачи дообучения с истёкшей арендой → ошибка (если слушаем
   training) + webhook; чистка завершённых задач не чаще раза в минуту.
2. Агент (`worker_main.get_agent`: смена модели и `.env` подхватываются здесь).
   Агент не создаётся (нет активной модели) — задачи классификации не берём,
   пишем ошибку в журнал и ждём следующего опроса.
3. `jobs.claim_next(...)` — одна задача; синхронные (classify_sync) первыми.
4. Работа — `queueing.tasks.run_*`; фоновый поток продлевает аренду каждые
   lease/3. Сбой БД → `release_for_retry` с паузой 5, 10, 20, 40, 60… с, пока
   attempts < JOB_MAX_ATTEMPTS, иначе ошибка. Дообучение не повторяется.
5. Нет задач — ждём NOTIFY job_queued на отдельном соединении, но не дольше
   QUEUE_POLL_SEC (контрольный опрос: потерянный сигнал, отложенный повтор,
   истёкшие аренды). У соединения application_name
   'classifier-worker:<очереди>' — по нему /health видит живых воркеров.

Ctrl+C / SIGTERM: текущая задача доделывается, затем процесс выходит.
Если процесс убит, задачу вернёт истечение аренды.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# Корень импорта — src/. При запуске `python src/<пакет>/<файл>.py` Python кладёт
# в sys.path[0] папку самого скрипта: заменяем её на src, чтобы модули пакета
# не импортировались под двумя именами. `python -m <пакет>.<файл>` с PYTHONPATH=src
# и импорт из тестов работают без этого.
_SRC = Path(__file__).resolve().parents[1]
if sys.path and Path(sys.path[0] or ".").resolve() == Path(__file__).resolve().parent:
    sys.path[0] = str(_SRC)
elif str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from core import config  # noqa: E402
from queueing import tasks  # noqa: E402
from storage.models import JOB_QUEUES, WORKER_APP_PREFIX  # noqa: E402

log = logging.getLogger("worker.run")

CLASSIFY_QUEUES = ("classify_sync", "classify")
CLEANUP_EVERY_SEC = 60.0
RECONNECT_PAUSE_SEC = 1.0

_HANDLERS = {
    "classify": tasks.run_classify_appeal,
    "search": tasks.run_search_classifier,
    "embed_annotation": tasks.run_embed_annotation,
    "finetune": tasks.run_finetune,
}


def parse_queues(text: str) -> list[str]:
    """'classify_sync, classify' → ['classify_sync', 'classify']; неизвестная очередь — ValueError."""
    queues: list[str] = []
    for name in (text or "").split(","):
        name = name.strip()
        if not name:
            continue
        if name not in JOB_QUEUES:
            raise ValueError(f"неизвестная очередь {name!r} (допустимо: {', '.join(JOB_QUEUES)})")
        if name not in queues:
            queues.append(name)
    if not queues:
        raise ValueError("не указано ни одной очереди")
    return queues


def application_name(queues: list[str]) -> str:
    return WORKER_APP_PREFIX + ",".join(queues)


def retry_delay(attempts: int) -> float:
    """Пауза перед повтором после сбоя инфраструктуры: 5, 10, 20, 40, 60, 60… с."""
    return min(60, 5 * 2 ** max(0, attempts - 1))


class _Listener:
    """Соединение LISTEN job_queued (autocommit) с application_name воркера."""

    def __init__(self, dsn: str, app_name: str, queues: list[str]) -> None:
        self._dsn = dsn
        self._app_name = app_name
        self._queues = set(queues)
        self._conn = None

    def connect(self) -> bool:
        import psycopg

        from queueing.waiter import KEEPALIVE_KWARGS

        try:
            conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=5,
                                   application_name=self._app_name, **KEEPALIVE_KWARGS)
            conn.execute("LISTEN job_queued")
        except Exception as exc:  # noqa: BLE001
            log.warning("LISTEN job_queued: нет соединения с БД (%s: %s)",
                        type(exc).__name__, exc)
            return False
        self._conn = conn
        return True

    def wait(self, timeout: float, stop: threading.Event) -> None:
        """Ждать сигнала для своей очереди не дольше timeout секунд.

        stop проверяется только между уведомлениями: остановка простаивающего
        воркера занимает до timeout (QUEUE_POLL_SEC, 5 с) — это допустимо.
        """
        if self._conn is None or self._conn.closed:
            if not self.connect():
                stop.wait(timeout)
            # Только что подключились: сигналы могли пропасть — сразу опрос.
            return
        try:
            for notify in self._conn.notifies(timeout=timeout):
                if not notify.payload or notify.payload in self._queues:
                    return
                if stop.is_set():
                    return
        except Exception as exc:  # noqa: BLE001 — переподключимся при следующем ожидании
            log.warning("LISTEN job_queued: соединение потеряно (%s: %s)",
                        type(exc).__name__, exc)
            self.close()
            stop.wait(min(timeout, RECONNECT_PAUSE_SEC))

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


class Worker:
    """Цикл воркера над очередями `queues` (одна задача за раз)."""

    def __init__(self, storage, queues: list[str], worker_id: str,
                 agent_factory: Optional[Callable[[], object]], *,
                 poll_sec: Optional[float] = None, lease_sec: Optional[float] = None,
                 listen_dsn: Optional[str] = None) -> None:
        self.storage = storage
        self.queues = list(queues)
        self.worker_id = worker_id
        self.agent_factory = agent_factory
        self.poll_sec = config.QUEUE_POLL_SEC if poll_sec is None else poll_sec
        self.lease_sec = config.JOB_LEASE_SEC if lease_sec is None else lease_sec
        self.listen_dsn = listen_dsn
        self.app_name = application_name(self.queues)
        self._next_cleanup = 0.0  # time.monotonic(): первая чистка — сразу

    # ── один шаг ─────────────────────────────────────────────────────────────

    def run_once(self) -> bool:
        """Взять и выполнить одну задачу. True — задача была взята."""
        self._housekeeping()
        queues, agent = self._claimable()
        if not queues:
            return False
        try:
            job = self.storage.jobs.claim_next(queues, self.worker_id, self.lease_sec)
        except Exception as exc:  # noqa: BLE001 — БД недоступна: следующий опрос
            log.warning("Захват задачи не удался (%s: %s)", type(exc).__name__, exc)
            return False
        if job is None:
            return False
        log.info("Задача %s (%s, очередь %s, попытка %d) взята",
                 job.id, job.job_type, job.queue, job.attempts)
        done = threading.Event()
        keeper = threading.Thread(target=self._keep_lease, args=(job, done),
                                  name=f"lease-{job.id[:8]}", daemon=True)
        keeper.start()
        try:
            self._execute(job, agent)
        finally:
            done.set()
            keeper.join(5)
        return True

    def _claimable(self) -> tuple[list[str], object]:
        """Очереди, из которых можно брать сейчас, и агент (None — не нужен/не создан)."""
        queues = list(self.queues)
        if not any(q in CLASSIFY_QUEUES for q in queues):
            return queues, None
        try:
            agent = self.agent_factory()
        except Exception as exc:  # noqa: BLE001 — задачи не валим, ждём модель
            log.error("Агент не создан, задачи классификации не берём: %s: %s",
                      type(exc).__name__, exc)
            return [q for q in queues if q not in CLASSIFY_QUEUES], None
        return queues, agent

    def _housekeeping(self) -> None:
        if "training" in self.queues:
            try:
                lost = self.storage.jobs.fail_lost_training()
            except Exception as exc:  # noqa: BLE001
                log.warning("Проверка пропавших дообучений не удалась (%s: %s)",
                            type(exc).__name__, exc)
                lost = []
            for job in lost:
                log.error("Задача дообучения %s: аренда истекла, задача завершена с ошибкой",
                          job.id)
                tasks.notify_webhook(self.storage, job)
        now = time.monotonic()
        if now >= self._next_cleanup:
            self._next_cleanup = now + CLEANUP_EVERY_SEC
            try:
                removed = self.storage.jobs.cleanup_expired(config.JOB_TTL_HOURS)
                if removed:
                    log.info("Удалено завершённых задач: %d", removed)
            except Exception as exc:  # noqa: BLE001
                log.warning("Чистка задач не удалась (%s: %s)", type(exc).__name__, exc)

    def _keep_lease(self, job, done: threading.Event) -> None:
        """Продлевать аренду каждые lease/3, пока задача выполняется."""
        interval = max(self.lease_sec / 3.0, 0.01)
        while not done.wait(interval):
            try:
                if not self.storage.jobs.extend_lease(job.id, job.attempts, self.lease_sec):
                    if not done.is_set():  # не гонка с только что завершённой задачей
                        log.warning("Задача %s: аренда потеряна (задачей владеет другая "
                                    "попытка)", job.id)
                    return
            except Exception as exc:  # noqa: BLE001 — БД недоступна: пробуем снова
                log.warning("Задача %s: продление аренды не удалось (%s: %s)",
                            job.id, type(exc).__name__, exc)

    # ── выполнение ───────────────────────────────────────────────────────────

    def _execute(self, job, agent) -> None:
        handler = _HANDLERS.get(job.job_type)
        if handler is None:
            self._finish_error(job, f"неизвестный вид задачи: {job.job_type!r}")
            return
        try:
            transitioned = handler(job, self.storage, agent)
        except tasks.RetryableError as exc:
            self._retry(job, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — run_* сами пишут ошибки работы
            log.exception("Задача %s: непредвиденный сбой", job.id)
            self._finish_error(job, f"{type(exc).__name__}: {exc}")
            return
        tasks.delete_if_embed_done(self.storage, job, transitioned)

    def _retry(self, job, error: str) -> None:
        """Сбой инфраструктуры: вернуть задачу в очередь с паузой или завершить ошибкой."""
        if job.job_type != "finetune" and job.attempts < config.JOB_MAX_ATTEMPTS:
            delay = retry_delay(job.attempts)
            try:
                if self.storage.jobs.release_for_retry(job.id, job.attempts, delay, error):
                    log.warning("Задача %s: сбой инфраструктуры (%s), повтор через %.0f с",
                                job.id, error, delay)
            except Exception as exc:  # noqa: BLE001
                log.warning("Задача %s: вернуть в очередь не удалось (%s: %s); её подберёт "
                            "другой воркер по истечении аренды", job.id, type(exc).__name__, exc)
            return
        # Последняя попытка или дообучение (не перезапускается): ошибка.
        self._finish_error(job, error)

    def _finish_error(self, job, error: str) -> None:
        try:
            transitioned = tasks._finish_error(self.storage, job.id, error,
                                               attempts=job.attempts)
        except Exception as exc:  # noqa: BLE001
            log.warning("Задача %s: записать ошибку не удалось (%s)", job.id, exc)
            return
        tasks.notify_webhook(self.storage, job, transitioned)

    # ── цикл ─────────────────────────────────────────────────────────────────

    def run_forever(self, stop: threading.Event) -> None:
        listener: Optional[_Listener] = None
        registered = False
        if self.listen_dsn:
            listener = _Listener(self.listen_dsn, self.app_name, self.queues)
            listener.connect()
        elif hasattr(self.storage.jobs, "register_worker"):
            # Без PostgreSQL (memory): эмуляция pg_stat_activity для /health.
            self.storage.jobs.register_worker(self.app_name)
            registered = True
        log.info("Воркер %s слушает очереди: %s", self.worker_id, ", ".join(self.queues))
        try:
            while not stop.is_set():
                try:
                    took = self.run_once()
                except Exception:  # noqa: BLE001 — цикл не должен умирать
                    log.exception("Сбой цикла воркера")
                    took = False
                if took or stop.is_set():
                    continue
                if listener is not None:
                    listener.wait(self.poll_sec, stop)
                else:
                    stop.wait(self.poll_sec)
        finally:
            if listener is not None:
                listener.close()
            if registered:
                self.storage.jobs.unregister_worker(self.app_name)
            log.info("Воркер %s остановлен", self.worker_id)


_STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGBREAK")


def _install_signal_handlers(stop: threading.Event) -> None:
    """Первый сигнал — мягкая остановка (задача доделывается); после него
    обработчики сброшены по умолчанию, и второй Ctrl+C завершает процесс сразу."""
    sigs = [getattr(signal, name) for name in _STOP_SIGNALS if hasattr(signal, name)]

    def handler(signum, _frame):
        log.info("Сигнал %s: доделываем текущую задачу и выходим "
                 "(повторный сигнал — немедленный выход)", signum)
        stop.set()
        for sig in sigs:
            try:
                signal.signal(sig, signal.SIG_DFL)
            except (ValueError, OSError):
                pass

    for sig in sigs:
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):  # не главный поток / сигнал недоступен
            pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worker.run",
        description="Воркер очереди задач (таблица jobs в PostgreSQL).")
    parser.add_argument("--queues", required=True,
                        help="очереди через запятую: classify_sync,classify или training")
    args = parser.parse_args(argv)
    try:
        queues = parse_queues(args.queues)
    except ValueError as exc:
        parser.error(str(exc))

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from storage import get_storage

    storage = get_storage()
    agent_factory: Optional[Callable[[], object]] = None
    if any(q in CLASSIFY_QUEUES for q in queues):
        from worker import worker_main  # тянет classifier_agent (модель)

        agent_factory = worker_main.get_agent
        try:
            agent_factory()  # прогрев: модель грузится при старте процесса
        except Exception:  # noqa: BLE001 — повторится перед захватом задачи
            log.exception("Прогрев агента не удался; повтор перед захватом задачи")

    stop = threading.Event()
    _install_signal_handlers(stop)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    listen_dsn = None if config.STORAGE_BACKEND == "memory" else config.DATABASE_URL
    Worker(storage, queues, worker_id, agent_factory, listen_dsn=listen_dsn).run_forever(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())

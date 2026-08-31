"""Очередь фоновых задач классификации.

Зачем: классификация занимает от десятков секунд до нескольких минут — новые
сборки модели отвечают дольше двух минут. Пока запрос выполняется синхронно,
на стороне Directum RX висит заблокированный процесс воркера. Поэтому приём
возвращает идентификатор задачи сразу, а результат RX забирает опросом.

Обработка идёт **одним** рабочим потоком. Пайплайн последовательный по своей
природе (см. docs/EPIC_load-scaling.md): модель эмбеддингов и агент — общий
разделяемый объект, и параллельный запуск дал бы конкуренцию за него без
выигрыша в пропускной способности. Очередь честно показывает позицию, чтобы
вызывающая сторона видела, что задача ждёт, а не потерялась.

Состояния: `queued` → `running` → `done` | `error`.

Задачи пишутся в файлы (`data/jobs/<id>.json`), поэтому результат переживает
перезапуск сервиса. Задачи, не успевшие выполниться до перезапуска, при старте
помечаются ошибкой: вызвать их повторно нечем — функция обработки жила в памяти.
"""

import json
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

_ACTIVE = (STATUS_QUEUED, STATUS_RUNNING)

# Идентификатор приходит из URL и подставляется в путь к файлу, поэтому формат
# проверяется строго: uuid4().hex и ничего кроме него. Иначе «../../.env»
# превратился бы в чтение произвольного файла.
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class Job:
    """Одна задача классификации."""
    id: str
    status: str = STATUS_QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    # Служебное: что за документ, чтобы задачу можно было опознать в логах.
    meta: dict = field(default_factory=dict)

    @property
    def elapsed_sec(self) -> float:
        end = self.finished_at or time.time()
        start = self.started_at or self.created_at
        return round(end - start, 1)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["elapsed_sec"] = self.elapsed_sec
        return data


class JobQueue:
    """Очередь с одним рабочим потоком и файловым хранением результатов."""

    def __init__(self, jobs_dir: Path, ttl_hours: float = 24.0, max_queued: int = 100):
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_hours * 3600
        self._max_queued = max_queued

        self._jobs: dict[str, Job] = {}
        self._funcs: dict[str, Callable[[], dict]] = {}
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

        self._load_from_disk()

    # ── публичный интерфейс ──────────────────────────────────────────────────

    def submit(self, fn: Callable[[], dict], meta: Optional[dict] = None) -> Job:
        """Поставить задачу в очередь. Бросает `QueueFull`, если очередь переполнена."""
        with self._lock:
            queued = sum(1 for j in self._jobs.values() if j.status == STATUS_QUEUED)
            if queued >= self._max_queued:
                raise QueueFull(f"в очереди уже {queued} задач, лимит {self._max_queued}")

            job = Job(id=uuid.uuid4().hex, meta=meta or {})
            self._jobs[job.id] = job
            self._funcs[job.id] = fn
            self._save(job)

        self._queue.put(job.id)
        self._ensure_worker()
        self._cleanup_expired()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        """Задача из памяти, а если её там нет — с диска.

        Чтение с диска нужно, когда опрос попал в другой процесс: при запуске
        uvicorn с несколькими воркерами у каждого своя очередь в памяти, но
        файлы задач общие. Такой опрос отдаст актуальное состояние вместо 404.
        """
        if not _ID_RE.match(job_id or ""):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
        return job if job is not None else self._load_one(job_id)

    def position(self, job_id: str) -> Optional[int]:
        """Место в очереди начиная с 1; None, если задача уже не ждёт."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != STATUS_QUEUED:
                return None
            waiting = sorted(
                (j for j in self._jobs.values() if j.status == STATUS_QUEUED),
                key=lambda j: j.created_at,
            )
            return next((i + 1 for i, j in enumerate(waiting) if j.id == job_id), None)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def stats(self) -> dict:
        with self._lock:
            counts: dict[str, int] = {}
            for job in self._jobs.values():
                counts[job.status] = counts.get(job.status, 0) + 1
        return {
            "queued": counts.get(STATUS_QUEUED, 0),
            "running": counts.get(STATUS_RUNNING, 0),
            "done": counts.get(STATUS_DONE, 0),
            "error": counts.get(STATUS_ERROR, 0),
            "worker_alive": bool(self._worker and self._worker.is_alive()),
        }

    # ── внутреннее ───────────────────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        """Поток создаётся при первой задаче и живёт до остановки процесса."""
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="job-worker", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._execute(job_id)
            finally:
                self._queue.task_done()

    def _execute(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            fn = self._funcs.pop(job_id, None)
            if job is None or fn is None:
                return
            job.status = STATUS_RUNNING
            job.started_at = time.time()
            self._save(job)

        try:
            result = fn()
            status, error = STATUS_DONE, None
        except Exception as exc:  # noqa: BLE001 — текст ошибки нужен вызывающей стороне
            result, status = None, STATUS_ERROR
            error = f"{type(exc).__name__}: {exc}"

        with self._lock:
            job.status = status
            job.result = result
            job.error = error
            job.finished_at = time.time()
            # Пока задача выполнялась, уборка могла снять её по TTL. Тогда файл
            # уже удалён, и записывать его снова нельзя — иначе на диске
            # останется задача, которой нет в памяти.
            if job.id in self._jobs:
                self._save(job)

    def _path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def _save(self, job: Job) -> None:
        """Файл переписывается целиком через временный, чтобы опрос не поймал обрывок."""
        tmp = self._path(job.id).with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(job.id))

    def _load_one(self, job_id: str) -> Optional[Job]:
        """Прочитать задачу с диска, не втягивая её в память этого процесса."""
        path = self._path(job_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        data.pop("elapsed_sec", None)
        try:
            return Job(**data)
        except TypeError:
            return None

    def _load_from_disk(self) -> None:
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            data.pop("elapsed_sec", None)
            try:
                job = Job(**data)
            except TypeError:
                continue
            if job.status in _ACTIVE:
                # Функцию обработки восстановить нечем — она была замыканием в памяти.
                job.status = STATUS_ERROR
                job.error = "Сервис был перезапущен до завершения задачи, отправьте обращение заново"
                job.finished_at = time.time()
                self._save(job)
            self._jobs[job.id] = job

    def _cleanup_expired(self) -> None:
        """Старые завершённые задачи не должны накапливаться на диске."""
        deadline = time.time() - self._ttl
        with self._lock:
            # Сравнение нестрогое: на Windows разрешение time.time() около 15 мс,
            # и метка завершения задачи может совпасть с моментом уборки.
            stale = [j.id for j in self._jobs.values()
                     if j.status not in _ACTIVE and (j.finished_at or j.created_at) <= deadline]
            for job_id in stale:
                self._jobs.pop(job_id, None)
        for job_id in stale:
            self._path(job_id).unlink(missing_ok=True)


class QueueFull(RuntimeError):
    """Очередь заполнена — вызывающей стороне нужно повторить позже."""

"""Выбор бэкенда хранилища (кэшируется)."""

from __future__ import annotations

import threading
from typing import Optional

import config

from .models import Storage

_storage: Optional[Storage] = None
_lock = threading.Lock()


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        with _lock:
            if _storage is None:
                backend = config.STORAGE_BACKEND
                if backend == "memory":
                    from .memory import MemoryStorage

                    _storage = MemoryStorage()
                elif backend == "postgres":
                    from .pg import PgStorage

                    _storage = PgStorage()
                else:
                    raise ValueError(f"Неизвестный STORAGE_BACKEND: {backend!r}")
    return _storage


def reset_storage() -> None:
    global _storage
    with _lock:
        _storage = None

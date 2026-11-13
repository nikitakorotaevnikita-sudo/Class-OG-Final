import pytest

from core import config
from storage import get_storage, reset_storage
from storage.memory import MemoryStorage


def test_get_storage_memory_cached_and_reset(monkeypatch):
    monkeypatch.setattr(config, "STORAGE_BACKEND", "memory")
    reset_storage()
    try:
        s = get_storage()
        assert isinstance(s, MemoryStorage) and get_storage() is s
        reset_storage()
        assert get_storage() is not s
    finally:
        reset_storage()


def test_get_storage_unknown_backend(monkeypatch):
    monkeypatch.setattr(config, "STORAGE_BACKEND", "sqlite")
    reset_storage()
    with pytest.raises(ValueError):
        get_storage()
    reset_storage()

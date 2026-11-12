"""Каркас тестов интеграции RX: memory-хранилище + LocalDispatcher с фейковым агентом
+ настоящий lifespan (старт ожидателя job_done). Без модели и брокера."""
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import integration_api
from queueing import dispatcher as dispatcher_mod
from queueing import waiter as waiter_mod
from queueing.dispatcher import LocalDispatcher
from storage import get_storage

_open: list = []
_dispatchers: list = []


def _lifespan_factory():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        get_storage()
        w = waiter_mod.get_waiter()
        await w.start()
        try:
            yield
        finally:
            await w.stop()
    return lifespan


def make_client(agent, dispatcher=None) -> TestClient:
    """TestClient приложения с роутером интеграции. Закрывается cleanup()."""
    waiter_mod.reset_waiter()
    storage = get_storage()
    mp = pytest.MonkeyPatch()
    if dispatcher is None:
        dispatcher = LocalDispatcher(storage, agent_factory=lambda: agent)
        _dispatchers.append(dispatcher)
    mp.setattr(dispatcher_mod, "_dispatcher", dispatcher)
    mp.setattr(config, "SYNC_WAIT_TIMEOUT_SEC", 30)
    app = FastAPI(lifespan=_lifespan_factory())
    app.include_router(integration_api.router)
    client = TestClient(app)
    client.__enter__()
    _open.append((client, mp))
    return client


def cleanup() -> None:
    while _open:
        client, mp = _open.pop()
        client.__exit__(None, None, None)
        mp.undo()
    while _dispatchers:  # задачи, пережившие тест (504), не доделываются в следующих
        _dispatchers.pop().shutdown(timeout=10)
    waiter_mod.reset_waiter()


def poll_until_done(api, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api.get(f"/integration/jobs/{job_id}").json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"задача {job_id} не завершилась за {timeout}с")

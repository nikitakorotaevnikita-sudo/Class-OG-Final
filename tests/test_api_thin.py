"""Тонкий API: классификация и поиск — через задачу (jobs) + диспетчер + ожидатель.

STORAGE_BACKEND=memory (conftest), LocalDispatcher с фейковым агентом — без
модели и без брокера. TestClient запускается в `with`, чтобы отработал lifespan
(старт ожидателя job_done).
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from queueing import dispatcher as dispatcher_mod
from queueing import waiter as waiter_mod
from queueing.dispatcher import DispatchError, LocalDispatcher
from storage import get_storage
from storage.models import AppealQuestionRow, AppealResult, ModelVersion

SRC = Path(__file__).resolve().parents[1] / "src"
CODE = "0005.0005.0051.1103"


def _question(code=CODE):
    return SimpleNamespace(
        question_text="вопрос", code=code, name="Вывоз мусора", level=4, full_path="путь",
        predmet_vedeniya="", confidence=0.9, reasoning="потому что", alternatives=[],
    )


class FakeAgent:
    """Агент без модели: пишет карточку в storage, как настоящий."""

    def __init__(self, storage, delay=0.0, error=None):
        self.storage = storage
        self.delay = delay
        self.error = error
        self.calls = []

    def classify(self, text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None):
        self.calls.append(dict(text=text, llm_provider=llm_provider, llm_model=llm_model,
                               appeal_id=appeal_id))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        q = _question()
        self.storage.appeals.save_result(appeal_id, AppealResult(
            summary="суть", applicant_fio=None, applicant_email=None,
            vid_obrascheniya="Заявление", tip_obrascheniya="Письменное", is_ustnoe=False,
            overall_confidence=0.9, needs_verification=False, full_fallback_used=False,
            candidates=[], llm_provider=llm_provider or "ario", llm_model=llm_model or "qwen",
            elapsed_seconds=0.01,
            questions=[AppealQuestionRow(ordinal=1, question_text=q.question_text,
                                         selected_code=q.code, confidence=q.confidence,
                                         alternative_codes=[])],
        ), conn=log_conn)
        return SimpleNamespace(
            questions=[q], log_id=appeal_id, vid_obrascheniya="Заявление",
            tip_obrascheniya="Письменное", is_ustное=False, overall_confidence=0.9,
            needs_verification=False, llm_provider=llm_provider or "ario",
            llm_model=llm_model or "qwen", full_fallback_used=False,
            applicant_fio=None, applicant_email=None, summary="суть",
        )

    def format_for_operator(self, result):
        return "карточка"

    def _search_candidates(self, q, top_k=None):
        return [{"code": f"{i:04d}.0000.0000.0000", "similarity": 1 - i / 10} for i in range(10)]

    def embed_passage(self, text):
        import numpy as np
        return np.zeros(768, dtype=np.float32)


@pytest.fixture
def env(monkeypatch):
    """Свежие singletons storage/waiter/dispatcher; LocalDispatcher с фейковым агентом."""
    waiter_mod.reset_waiter()
    storage = get_storage()
    agent = FakeAgent(storage)
    monkeypatch.setattr(dispatcher_mod, "_dispatcher",
                        LocalDispatcher(storage, agent_factory=lambda: agent))
    monkeypatch.setattr(config, "SYNC_WAIT_TIMEOUT_SEC", 30)
    import api_server
    yield SimpleNamespace(storage=storage, agent=agent, app=api_server.app)
    waiter_mod.reset_waiter()


@pytest.fixture
def client(env):
    with TestClient(env.app, client=("192.168.1.10", 50000)) as c:
        yield c


def test_api_does_not_import_heavy_modules():
    code = (
        "import sys; sys.path.insert(0, r'%s'); import api_server; "
        "heavy = [m for m in ('torch', 'classifier_agent', 'sentence_transformers') "
        "if m in sys.modules]; print('HEAVY=' + ','.join(heavy))" % SRC
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120,
                          env={**os.environ, "PYTHONUTF8": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "HEAVY=\n" in proc.stdout or proc.stdout.strip().endswith("HEAVY="), proc.stdout


def test_classify_sync_roundtrip(env, client):
    resp = client.post("/classify", json={"appeal_text": "Не вывозят мусор",
                                          "appeal_id": "rx-1", "llm_provider": "groq"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["appeal_id"] == "rx-1"
    assert data["questions"][0]["code"] == CODE
    assert data["operator_card"] == "карточка"
    assert data["llm_provider"] == "groq"
    # log_id — id карточки в хранилище; карточка получила ip и время запроса
    [entry] = env.storage.appeals.as_log_entries()
    assert data["log_id"] == entry["id"]
    assert env.storage.appeals.ip_addresses([entry["id"]]) == {entry["id"]: "192.168.1.10"}
    assert env.agent.calls[0]["llm_provider"] == "groq"
    job = env.storage.jobs.latest("classify")
    assert job.status == "done"
    assert job.payload["queue"] == "classify_sync"


def test_classify_card_keeps_request_ip_and_worker_elapsed(env, client):
    """ip — из запроса (пишется при создании карточки); elapsed_seconds — время
    классификации, записанное воркером (§7.1.5), а не время HTTP-запроса."""
    env.agent.delay = 0.3  # запрос длится ≥0.3 с, воркер пишет elapsed_seconds=0.01
    resp = client.post("/classify", json={"appeal_text": "Не вывозят мусор"})
    assert resp.status_code == 200, resp.text
    aid = resp.json()["log_id"]
    assert env.storage.appeals.ip_addresses([aid]) == {aid: "192.168.1.10"}
    card = env.storage.appeals._cards[aid]
    assert card["elapsed_seconds"] == 0.01


def test_classify_empty_text_400(client):
    resp = client.post("/classify", json={"appeal_text": "  "})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Текст обращения пустой"


def test_classify_file_roundtrip(env, client):
    resp = client.post("/classify/file",
                       files={"file": ("a.txt", "Не вывозят мусор".encode("utf-8"), "text/plain")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["questions"][0]["code"] == CODE
    assert resp.json()["was_truncated"] is False


def test_classify_sync_timeout_returns_504(env, client, monkeypatch):
    env.agent.delay = 1.5
    monkeypatch.setattr(config, "SYNC_WAIT_TIMEOUT_SEC", 0.2)
    resp = client.post("/classify", json={"appeal_text": "Не вывозят мусор"})
    assert resp.status_code == 504
    job = env.storage.jobs.latest("classify")
    assert resp.json()["detail"] == (
        f"Классификация не завершилась за 0.2 с, задача {job.id} продолжает выполняться: "
        f"GET /integration/jobs/{job.id}")


def test_classify_job_error_returns_500(env, client):
    env.agent.error = RuntimeError("LLM недоступна")
    resp = client.post("/classify", json={"appeal_text": "Не вывозят мусор"})
    assert resp.status_code == 500
    assert resp.json()["detail"].startswith("Ошибка классификации: ")
    assert "LLM недоступна" in resp.json()["detail"]


def test_classify_dispatch_failure_returns_503(env, client, monkeypatch):
    class Down:
        def send(self, task_name, msg, queue):
            raise DispatchError("брокер недоступен: OSError: refused")

    monkeypatch.setattr(dispatcher_mod, "_dispatcher", Down())
    resp = client.post("/classify", json={"appeal_text": "Не вывозят мусор"})
    assert resp.status_code == 503
    job = env.storage.jobs.latest("classify")
    assert job.status == "error"
    assert job.error.startswith("Брокер недоступен: ")


def test_classify_db_failure_returns_503(env, client, monkeypatch):
    import psycopg

    def boom(*a, **kw):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(env.storage.appeals, "create", boom)
    resp = client.post("/classify", json={"appeal_text": "Не вывозят мусор"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "База данных недоступна"


def test_classifier_search_via_job(env, client):
    resp = client.get("/classifier/search", params={"q": "мусор", "top_k": 3})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["query"] == "мусор"
    assert len(data["results"]) == 3
    job = env.storage.jobs.latest("search")
    assert job.payload["queue"] == "classify_sync"


def test_health_shape(client):
    data = client.get("/health").json()
    assert set(data) == {"status", "agent_ready", "classifier_entries", "db", "broker"}
    assert data["status"] == "ok"
    assert data["agent_ready"] is True and data["broker"] is True and data["db"] is True
    assert data["classifier_entries"] == 0


def test_health_db_down_never_raises(env, client, monkeypatch):
    import psycopg

    def boom():
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(env.storage.classifier, "count_entries", boom)
    data = client.get("/health").json()
    assert data["db"] is False and data["classifier_entries"] == 0


def test_finetune_conflict_409(env, client):
    env.storage.jobs.create("finetune", {"source": "combined", "force": False},
                            queue_class="training")
    resp = client.post("/api/finetune", json={"source": "combined"})
    assert resp.status_code == 409


def test_finetune_start_and_status(env, client, monkeypatch):
    sent = []

    class Rec:
        def send(self, task_name, msg, queue):
            sent.append((task_name, msg, queue))

    monkeypatch.setattr(dispatcher_mod, "_dispatcher", Rec())
    idle = client.get("/api/finetune/status").json()
    assert idle["status"] == "idle"
    assert idle["message"] == "Дообучение не запускалось"

    resp = client.post("/api/finetune", json={"source": "verified", "force": True})
    assert resp.status_code == 200, resp.text
    snap = resp.json()
    assert snap["status"] == "queued"
    assert snap["source"] == "verified"
    assert set(snap) >= {"job_id", "status", "source", "force", "started_at", "finished_at",
                         "message", "model_path", "returncode", "log_tail"}
    assert sent[0][0] == "finetune" and sent[0][2] == "training"
    assert sent[0][1]["job_id"] == snap["job_id"]

    env.storage.jobs.claim(snap["job_id"])
    assert client.get("/api/finetune/status").json()["status"] == "running"
    env.storage.jobs.finish(snap["job_id"], result={
        "status": "completed", "message": "Модель v1 активирована", "model_path": "models/v1",
        "log_tail": "ok"}, error=None)
    done = client.get("/api/finetune/status").json()
    assert done["status"] == "completed"
    assert done["model_path"] == "models/v1"
    assert done["returncode"] == 0
    assert done["message"] == "Модель v1 активирована"


def test_finetune_status_failed(env, client):
    job = env.storage.jobs.create("finetune", {"source": "combined", "force": False},
                                  queue_class="training")
    env.storage.jobs.finish(job.id, result=None, error="RuntimeError: нет данных")
    data = client.get("/api/finetune/status").json()
    assert data["status"] == "failed"
    assert "нет данных" in data["message"]


def _register_models(storage):
    storage.models.register(ModelVersion(version=0, base_model_name="e5", model_path="e5",
                                         is_active=True))
    storage.models.register(ModelVersion(version=1, base_model_name="e5",
                                         model_path="models/e5-finetuned-v1", is_active=False,
                                         recall5_before=0.5, recall5_after=0.6,
                                         training_samples_count=10))


def test_models_list(env, client):
    _register_models(env.storage)
    items = client.get("/api/models").json()
    assert [m["version"] for m in items] == [0, 1]
    assert set(items[1]) == {"version", "base_model_name", "model_path", "is_active",
                             "recall5_before", "recall5_after", "training_samples_count",
                             "trained_at"}
    assert items[0]["is_active"] is True and items[1]["recall5_after"] == 0.6


def test_models_activate(env, client, monkeypatch):
    monkeypatch.setattr(config, "BACKOFFICE_USER", "admin")
    monkeypatch.setattr(config, "BACKOFFICE_PASSWORD", "secret")
    _register_models(env.storage)
    assert client.post("/api/models/1/activate").status_code == 401
    resp = client.post("/api/models/1/activate", auth=("admin", "secret"))
    assert resp.status_code == 200, resp.text
    assert env.storage.models.active().version == 1
    missing = client.post("/api/models/7/activate", auth=("admin", "secret"))
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Версия модели 7 не найдена"


def test_verify_correct_with_annotation_dispatches_embedding(env, client, monkeypatch):
    sent = []

    class Rec:
        def send(self, task_name, msg, queue):
            sent.append((task_name, msg, queue))

    _register_models(env.storage)
    aid = env.storage.appeals.create("текст")
    monkeypatch.setattr(dispatcher_mod, "_dispatcher", Rec())
    resp = client.post("/verify", json={"log_id": aid, "action": "correct",
                                        "operator_codes": [CODE], "annotation": "про мусор"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["annotation_count"] == 1
    assert env.storage.classifier.annotations_for(CODE) == ["про мусор"]
    assert [(t, q) for t, _, q in sent] == [("embed_annotation", "classify")]
    job = env.storage.jobs.get(sent[0][1]["job_id"])
    assert job.job_type == "embed_annotation"
    assert job.payload["vector_id"] == env.storage.classifier.pending_annotations(0)[0][0]


def test_historical_count(env, client):
    assert client.get("/api/historical-count").json() == {"count": 0, "status": "no_data"}
    env.storage.historical.add([{"appeal_text": "т", "assigned_code": CODE}], "f.xlsx")
    assert client.get("/api/historical-count").json() == {"count": 1, "status": "ok"}


def test_settings_include_sync_wait_timeout():
    import settings_store
    data = settings_store.read_settings()
    queue = [g for g in data["groups"] if g["group"] == "queue"]
    assert queue and queue[0]["label"] == "Очередь"
    assert [f["key"] for f in queue[0]["fields"]] == ["SYNC_WAIT_TIMEOUT_SEC"]


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1.5"])
def test_settings_sync_wait_timeout_rejects_non_positive_int(bad):
    import settings_store
    with pytest.raises(settings_store.SettingsError):
        settings_store.normalize_updates({"SYNC_WAIT_TIMEOUT_SEC": bad})


def test_settings_sync_wait_timeout_applied_at_runtime(monkeypatch, tmp_path):
    import settings_store
    monkeypatch.setattr(settings_store, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config, "SYNC_WAIT_TIMEOUT_SEC", 1800)
    monkeypatch.setenv("SYNC_WAIT_TIMEOUT_SEC", "1800")
    settings_store.save_settings({"SYNC_WAIT_TIMEOUT_SEC": "600"})
    assert config.SYNC_WAIT_TIMEOUT_SEC == 600


def test_finetune_double_submit_race_is_409(env, client, monkeypatch):
    """Проверка has_active прошла у обоих запросов — второй ловит уникальный индекс."""
    monkeypatch.setattr(dispatcher_mod, "_dispatcher",
                        SimpleNamespace(send=lambda *a, **k: None))
    env.storage.jobs.create("finetune", {"source": "combined", "force": False},
                            queue_class="training")
    monkeypatch.setattr(env.storage.jobs, "has_active", lambda job_type: False)
    resp = client.post("/api/finetune", json={"source": "combined"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Дообучение уже выполняется"

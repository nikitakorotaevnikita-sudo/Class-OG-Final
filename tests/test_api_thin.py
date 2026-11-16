"""Тонкий API: классификация и поиск — через задачу (jobs) + ожидатель.

STORAGE_BACKEND=memory (conftest): строку jobs исполняет LocalDispatcher с фейковым
агентом — без модели и без воркеров. TestClient запускается в `with`, чтобы отработал lifespan
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

from core import config
from queueing import dispatcher as dispatcher_mod
from queueing import waiter as waiter_mod
from queueing.dispatcher import LocalDispatcher
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

    def classify(self, text, llm_provider=None, llm_model=None, appeal_id=None, log_conn=None,
                 defer_card=False):
        self.calls.append(dict(text=text, llm_provider=llm_provider, llm_model=llm_model,
                               appeal_id=appeal_id))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        q = _question()
        card = AppealResult(
            summary="суть", applicant_fio=None, applicant_email=None,
            vid_obrascheniya="Заявление", tip_obrascheniya="Письменное", is_ustnoe=False,
            overall_confidence=0.9, needs_verification=False, full_fallback_used=False,
            candidates=[], llm_provider=llm_provider or "ario", llm_model=llm_model or "qwen",
            elapsed_seconds=0.01,
            questions=[AppealQuestionRow(ordinal=1, question_text=q.question_text,
                                         selected_code=q.code, confidence=q.confidence,
                                         alternative_codes=[])],
        )
        if not defer_card:  # с defer_card карточку пишет задача воркера
            self.storage.appeals.save_result(appeal_id, card, conn=log_conn)
        return SimpleNamespace(
            questions=[q], log_id=appeal_id, vid_obrascheniya="Заявление",
            tip_obrascheniya="Письменное", is_ustное=False, overall_confidence=0.9,
            needs_verification=False, llm_provider=llm_provider or "ario",
            llm_model=llm_model or "qwen", full_fallback_used=False,
            applicant_fio=None, applicant_email=None, summary="суть",
            appeal_result=card if defer_card else None,
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
    local = LocalDispatcher(storage, agent_factory=lambda: agent)
    monkeypatch.setattr(dispatcher_mod, "_dispatcher", local)
    monkeypatch.setattr(config, "SYNC_WAIT_TIMEOUT_SEC", 30)
    from api import api_server
    yield SimpleNamespace(storage=storage, agent=agent, app=api_server.app, dispatcher=local)
    # Задача, пережившая 504 (test_classify_sync_timeout_returns_504), не должна
    # доделываться в следующих тестах: ждём её и останавливаем потоки пула.
    local.shutdown(timeout=10)
    assert not any(t.is_alive() for t in local._threads)
    waiter_mod.reset_waiter()


@pytest.fixture
def client(env):
    with TestClient(env.app, client=("192.168.1.10", 50000)) as c:
        yield c


def test_api_process_does_not_import_celery_or_model():
    """API без брокера и модели: ни celery/kombu, ни classifier_agent/torch."""
    code = (
        "import sys; sys.path.insert(0, r'%s'); import api.api_server; "
        "heavy = [m for m in ('torch', 'classifier.classifier_agent', 'sentence_transformers', "
        "'celery', 'kombu') "
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
    assert set(data) == {"status", "agent_ready", "classifier_entries", "db"}
    assert data["status"] == "ok"
    assert data["agent_ready"] is True and data["db"] is True
    assert data["classifier_entries"] == 0


def test_health_has_no_broker_and_agent_ready_from_workers(env, client):
    """agent_ready — живой воркер очереди classify (pg_stat_activity; в memory —
    register_worker). Memory без зарегистрированных воркеров: True (LocalDispatcher
    выполняет задачи в процессе API)."""
    jobs = env.storage.jobs
    assert "broker" not in client.get("/health").json()
    jobs.register_worker("classifier-worker:training")
    assert client.get("/health").json()["agent_ready"] is False
    assert client.get("/integration/jobs").json()["stats"]["worker_alive"] is False
    jobs.register_worker("classifier-worker:classify_sync,classify")
    assert client.get("/health").json()["agent_ready"] is True
    assert client.get("/integration/jobs").json()["stats"]["worker_alive"] is True
    jobs.unregister_worker("classifier-worker:classify_sync,classify")
    assert client.get("/health").json()["agent_ready"] is False


def test_sync_classify_marks_delivered(env, client):
    resp = client.post("/classify", json={"appeal_text": "Не вывозят мусор"})
    assert resp.status_code == 200, resp.text
    job = env.storage.jobs.latest("classify")
    assert job.status == "done" and job.delivered_at is not None
    # забранная задача удаляется чисткой по JOB_DELIVERED_GRACE_SEC
    assert env.storage.jobs.cleanup_expired(24, delivered_grace_sec=0) == 1
    assert env.storage.jobs.get(job.id) is None


def test_sync_classify_error_marks_delivered(env, client):
    env.agent.error = RuntimeError("LLM недоступна")
    assert client.post("/classify", json={"appeal_text": "т"}).status_code == 500
    job = env.storage.jobs.latest("classify")
    assert job.status == "error" and job.delivered_at is not None


def test_sync_timeout_does_not_mark_delivered(env, client, monkeypatch):
    env.agent.delay = 1.0
    monkeypatch.setattr(config, "SYNC_WAIT_TIMEOUT_SEC", 0.2)
    assert client.post("/classify", json={"appeal_text": "т"}).status_code == 504
    job = env.storage.jobs.latest("classify")
    assert job.delivered_at is None


def test_finetune_status_falls_back_to_registry_report(env, client):
    """Строки finetune нет (удалена по TTL) — итог последней версии из реестра."""
    from datetime import datetime, timezone

    _register_models(env.storage)
    trained = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    env.storage.models.register(ModelVersion(
        version=2, base_model_name="models/e5-finetuned-v1",
        model_path="models/e5-finetuned-v2", is_active=True, recall5_before=0.6,
        recall5_after=0.7, training_samples_count=20, trained_at=trained,
        report={"version": 2, "source": "historical", "recall5_before": 0.6,
                "recall5_after": 0.7, "improved": True}))
    data = client.get("/api/finetune/status").json()
    assert set(data) == {"job_id", "status", "source", "force", "started_at", "finished_at",
                         "message", "model_path", "returncode", "log_tail"}
    assert data["status"] == "completed"
    assert data["job_id"] is None
    assert data["source"] == "historical"
    assert data["model_path"] == "models/e5-finetuned-v2"
    assert data["returncode"] == 0
    assert "v2" in data["message"] and "0.600" in data["message"] and "0.700" in data["message"]
    assert data["finished_at"] == trained.astimezone().replace(tzinfo=None).isoformat(
        timespec="seconds")


def test_finetune_status_idle_when_registry_has_no_report(env, client):
    _register_models(env.storage)  # v0/v1 без report
    data = client.get("/api/finetune/status").json()
    assert data["status"] == "idle"
    assert data["message"] == "Дообучение не запускалось"


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
        def send(self, job):
            sent.append(job)

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
    assert sent[0].job_type == "finetune" and sent[0].queue == "training"
    assert sent[0].id == snap["job_id"]

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
        def send(self, job):
            sent.append(job)

    _register_models(env.storage)
    aid = env.storage.appeals.create("текст")
    monkeypatch.setattr(dispatcher_mod, "_dispatcher", Rec())
    resp = client.post("/verify", json={"log_id": aid, "action": "correct",
                                        "operator_codes": [CODE], "annotation": "про мусор"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["annotation_count"] == 1
    assert env.storage.classifier.annotations_for(CODE) == ["про мусор"]
    assert [(j.job_type, j.queue) for j in sent] == [("embed_annotation", "classify")]
    job = env.storage.jobs.get(sent[0].id)
    assert job.job_type == "embed_annotation"
    assert job.payload["vector_id"] == env.storage.classifier.pending_annotations(0)[0][0]


def test_historical_count(env, client):
    assert client.get("/api/historical-count").json() == {"count": 0, "status": "no_data"}
    env.storage.historical.add([{"appeal_text": "т", "assigned_code": CODE}], "f.xlsx")
    assert client.get("/api/historical-count").json() == {"count": 1, "status": "ok"}


def test_settings_include_sync_wait_timeout():
    from core import settings_store
    data = settings_store.read_settings()
    queue = [g for g in data["groups"] if g["group"] == "queue"]
    assert queue and queue[0]["label"] == "Очередь"
    assert [f["key"] for f in queue[0]["fields"]] == ["SYNC_WAIT_TIMEOUT_SEC"]


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1.5"])
def test_settings_sync_wait_timeout_rejects_non_positive_int(bad):
    from core import settings_store
    with pytest.raises(settings_store.SettingsError):
        settings_store.normalize_updates({"SYNC_WAIT_TIMEOUT_SEC": bad})


def test_settings_sync_wait_timeout_applied_at_runtime(monkeypatch, tmp_path):
    from core import settings_store
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


def test_search_timeout_504_does_not_point_to_jobs_endpoint(env, client, monkeypatch):
    """/integration/jobs/{id} отвечает 404 для задач поиска — ссылаться на него нельзя."""
    from api import api_server

    real = env.agent._search_candidates

    def slow(q, top_k=None):
        time.sleep(1.5)
        return real(q, top_k)

    monkeypatch.setattr(env.agent, "_search_candidates", slow)
    monkeypatch.setattr(api_server, "SEARCH_WAIT_TIMEOUT_SEC", 0.2)
    resp = client.get("/classifier/search", params={"q": "мусор"})
    assert resp.status_code == 504
    detail = resp.json()["detail"]
    assert "/integration/jobs" not in detail
    assert detail == "Поиск по классификатору не завершился за 0.2 с, повторите запрос позже"


def test_confirm_historical_reports_actually_inserted(env, client, monkeypatch):
    rec = {"appeal_text": "т", "assigned_code": CODE}
    env.storage.historical.add([rec], "f.xlsx")  # уже есть: дубль не вставится
    from api import api_server

    monkeypatch.setattr(api_server, "_last_historical_upload", {
        "valid_records": [rec, {"appeal_text": "новое", "assigned_code": CODE}],
        "filename": "f.xlsx"})
    resp = client.post("/api/confirm-historical", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "records_saved": 1, "duplicates_skipped": 1}
    assert env.storage.historical.count() == 2


# ── WP4: /verify, ошибки БД, снимок дообучения, /health, блокирующие вызовы ──

def test_verify_enqueues_exactly_inserted_annotation_ids(env, client, monkeypatch):
    """Эмбеддинг ставится ровно для строк, вставленных этим исправлением: чужая
    строка без эмбеддинга, появившаяся одновременно, не попадает в задачи."""
    sent = []

    class Rec:
        def send(self, job):
            sent.append(env.storage.jobs.get(job.id).payload["vector_id"])

    _register_models(env.storage)
    aid = env.storage.appeals.create("текст")
    classifier = env.storage.classifier
    real_add = classifier.add_annotation
    neighbours = []

    def add_with_neighbour(code, text, version):
        neighbours.append(real_add("0001.0001.0001.0001", "чужая аннотация", version))
        return real_add(code, text, version)

    monkeypatch.setattr(classifier, "add_annotation", add_with_neighbour)
    monkeypatch.setattr(dispatcher_mod, "_dispatcher", Rec())
    resp = client.post("/verify", json={"log_id": aid, "action": "correct",
                                        "operator_codes": [CODE, "0002.0002.0002.0002"],
                                        "annotation": "про мусор"})
    assert resp.status_code == 200, resp.text
    own = {vid for vid, _ in classifier.pending_annotations(0)} - set(neighbours)
    assert len(own) == 2
    assert sorted(sent) == sorted(own)


def test_logger_correct_with_annotations_returns_inserted_ids(env):
    from storage.appeals_logger import AppealsLogger

    _register_models(env.storage)
    logger = AppealsLogger(storage=env.storage)
    aid = env.storage.appeals.create("текст")
    updated, ids = logger.correct_with_annotations(aid, [CODE], comment="про мусор")
    assert updated is True
    assert ids == [vid for vid, _ in env.storage.classifier.pending_annotations(0)]
    assert logger.correct_with_annotations(aid, [CODE]) == (True, [])
    assert logger.correct(aid, [CODE], comment="ещё") is True


_DB_DOWN_ENDPOINTS = [
    ("get", "/api/historical-count", "historical", "count"),
    ("get", "/api/models", "models", "list"),
    ("post", "/api/models/1/activate", "models", "activate"),
    ("get", "/stats", "appeals", "stats"),
    ("get", "/api/backoffice/stats", "appeals", "backoffice_stats"),
    ("get", "/api/finetune/status", "jobs", "latest"),
    ("post", "/api/finetune", "jobs", "has_active"),
]


@pytest.mark.parametrize("method, path, repo, fn", _DB_DOWN_ENDPOINTS)
def test_db_errors_map_to_503(env, client, monkeypatch, method, path, repo, fn):
    import psycopg

    monkeypatch.setattr(config, "BACKOFFICE_USER", "admin")
    monkeypatch.setattr(config, "BACKOFFICE_PASSWORD", "secret")

    def boom(*a, **kw):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(getattr(env.storage, repo), fn, boom)
    kw = {"auth": ("admin", "secret")} if "activate" in path else {}
    if path == "/api/finetune":
        kw["json"] = {"source": "verified"}
    resp = getattr(client, method)(path, **kw)
    assert resp.status_code == 503, resp.text
    assert resp.json() == {"detail": "База данных недоступна"}


def test_finetune_snapshot_timestamps_in_legacy_format(env, client, monkeypatch):
    """Прежний формат — локальное время без зоны, до секунд (datetime.now().isoformat)."""
    import re
    from datetime import datetime

    monkeypatch.setattr(dispatcher_mod, "_dispatcher",
                        SimpleNamespace(send=lambda *a, **k: None))
    snap = client.post("/api/finetune", json={"source": "verified", "force": True}).json()
    env.storage.jobs.claim(snap["job_id"])
    env.storage.jobs.finish(snap["job_id"], result={"status": "completed"}, error=None)
    done = client.get("/api/finetune/status").json()
    legacy = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
    assert legacy.match(done["started_at"]), done["started_at"]
    assert legacy.match(done["finished_at"]), done["finished_at"]
    started = datetime.fromisoformat(done["started_at"])
    assert abs((datetime.now() - started).total_seconds()) < 60  # локальное время


@pytest.mark.parametrize("source, force, effective", [
    ("verified", False, False), ("verified", True, True),
    ("historical", False, True), ("combined", False, True),
])
def test_finetune_force_reports_effective_flag(env, client, monkeypatch, source, force,
                                               effective):
    monkeypatch.setattr(dispatcher_mod, "_dispatcher",
                        SimpleNamespace(send=lambda *a, **k: None))
    snap = client.post("/api/finetune", json={"source": source, "force": force}).json()
    assert snap["force"] is effective
    assert client.get("/api/finetune/status").json()["force"] is effective


def test_health_probe_is_single_flight_and_cached(monkeypatch):
    """Параллельные /health ждут одну проверку воркеров; результат кэшируется ~2 с."""
    import asyncio
    from api import job_api

    monkeypatch.setattr(config, "STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(job_api, "_ping_cache", {"at": None, "value": False, "task": None})
    calls = []

    def slow_probe():
        calls.append(1)
        time.sleep(0.2)
        return True

    monkeypatch.setattr(job_api, "_classify_workers_alive", slow_probe)

    async def burst():
        return await asyncio.gather(*(job_api.worker_status() for _ in range(5)))

    assert asyncio.run(burst()) == [True] * 5
    assert len(calls) == 1
    assert asyncio.run(job_api.worker_status()) is True
    assert len(calls) == 1, "в пределах кэша воркеры не опрашиваются"
    assert job_api._PING_CACHE_SEC <= 2.5
    job_api._ping_cache["at"] -= job_api._PING_CACHE_SEC + 0.1
    asyncio.run(job_api.worker_status())
    assert len(calls) == 2


@pytest.mark.parametrize("names, expected", [
    (["classifier-worker:classify_sync,classify"], True),
    (["classifier-worker:training"], False),
    (["classifier-worker:training", "classifier-worker:classify"], True),
    ([], False),
])
def test_workers_alive_counts_only_classify_workers(env, names, expected):
    from api import job_api

    for name in names:
        env.storage.jobs.register_worker(name)
    assert job_api._classify_workers_alive() is expected


def test_workers_probe_db_down_is_false(env, monkeypatch):
    import psycopg
    from api import job_api

    def down(queue):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(env.storage.jobs, "live_workers", down)
    assert job_api._classify_workers_alive() is False


def _off_loop() -> bool:
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


def test_classify_file_extracts_text_off_event_loop(env, client, monkeypatch):
    from api import api_server

    seen = []

    def fake_extract(content, filename):
        seen.append(_off_loop())
        return "Не вывозят мусор", False

    monkeypatch.setattr(api_server, "extract_text", fake_extract)
    resp = client.post("/classify/file",
                       files={"file": ("a.txt", "x".encode("utf-8"), "text/plain")})
    assert resp.status_code == 200, resp.text
    assert seen == [True]


def test_upload_historical_parses_off_event_loop(env, client, monkeypatch):
    from api import api_server

    seen = []

    def fake_parse(path):
        seen.append(("parse", _off_loop()))
        return [{"appeal_text": "т", "assigned_code": CODE}]

    def fake_validate(records):
        seen.append(("validate", _off_loop()))
        return SimpleNamespace(stats={"total": 1, "valid": 1, "invalid": 0}, errors=[],
                               valid_records=records)

    monkeypatch.setattr(api_server, "parse_file", fake_parse)
    monkeypatch.setattr(api_server, "validate_codes", fake_validate)
    monkeypatch.setattr(api_server, "_last_historical_upload", {})
    resp = client.post("/api/upload-historical",
                       files={"file": ("h.csv", b"a,b\n", "text/csv")})
    assert resp.status_code == 200, resp.text
    assert seen == [("parse", True), ("validate", True)]


def test_settings_read_and_save_off_event_loop(env, client, monkeypatch):
    from core import settings_store

    seen = []
    monkeypatch.setattr(settings_store, "read_settings",
                        lambda: seen.append(("read", _off_loop())) or {"groups": []})
    monkeypatch.setattr(settings_store, "save_settings",
                        lambda values: seen.append(("save", _off_loop())) or {"saved": []})
    assert client.get("/api/settings").status_code == 200
    assert client.post("/api/settings", json={"values": {}}).json()["message"] == "Изменений нет"
    assert seen == [("read", True), ("save", True)]


def test_examples_read_off_event_loop(env, client, tmp_path, monkeypatch):
    from api import api_server

    path = tmp_path / "ex.json"
    path.write_text('[{"text": "пример"}]', encoding="utf-8")
    seen = []
    real_load = api_server.json.load

    def spy_load(f, *a, **kw):
        seen.append(_off_loop())
        return real_load(f, *a, **kw)

    monkeypatch.setattr(api_server.json, "load", spy_load)
    env.app.dependency_overrides[api_server.get_examples_path] = lambda: path
    try:
        assert client.get("/examples").json() == {"examples": [{"text": "пример"}]}
    finally:
        env.app.dependency_overrides.pop(api_server.get_examples_path, None)
    assert seen == [True]

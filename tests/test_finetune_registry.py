"""Дообучение с реестром моделей: данные из БД, регистрация версии, активация по recall@5."""

import builtins
import io
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from training import build_vectordb
from core import config
from training import finetune_model
from finetune_helpers import (ANNOTATION, BASE_MODEL, CODES, FakeSentenceTransformer,
                              FakeTrain, seed)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(config, "FINETUNE_THRESHOLD", 5)
    monkeypatch.setattr(build_vectordb, "SentenceTransformer", FakeSentenceTransformer)
    FakeSentenceTransformer.loaded = []


@pytest.fixture(params=["memory", "pg"])
def store(request):
    """Пустое хранилище: MemoryStorage и PgStorage (пропуск, если PostgreSQL нет)."""
    from storage.memory import MemoryStorage
    from storage.pg import PgStorage

    if request.param == "memory":
        return MemoryStorage()
    from pgvector.psycopg import register_vector

    pg_conn = request.getfixturevalue("pg_conn")
    register_vector(pg_conn)

    @contextmanager
    def connect():
        with pg_conn.transaction():
            yield pg_conn

    return PgStorage(connect)


def test_first_finetune_from_version_zero_is_never_auto_activated(store, tmp_path):
    """v0 — векторы прода с адаптером, а recall_before мерится на «сыром» e5 без него:
    сравнение нечестное, первую дообученную версию активируют вручную."""
    storage = seed(store)
    train = FakeTrain(before=0.4, after=0.6)

    result = finetune_model.run_training(storage, source="combined", train_fn=train)

    assert result["status"] == "completed", result
    assert result["version"] == 1 and result["activated"] is False
    assert "вручную" in result["message"] and "бэк-офисе" in result["message"]
    assert storage.models.active().version == 0
    assert storage.models.get(1).is_active is False
    assert result["recall5_before"] == 0.4 and result["recall5_after"] == 0.6
    expected_path = str(Path(config.MODELS_DIR) / "e5-finetuned-v1")
    assert result["model_path"] == expected_path
    assert set(result) >= {"status", "message", "model_path", "version", "recall5_before",
                           "recall5_after", "activated", "log_tail"}
    # обучение стартует с модели активной версии
    assert train.calls[0]["base"] == BASE_MODEL
    assert train.calls[0]["output"] == Path(expected_path)
    mv = storage.models.get(1)
    assert mv.base_model_name == BASE_MODEL and mv.adapter is None
    assert mv.recall5_before == pytest.approx(0.4) and mv.recall5_after == pytest.approx(0.6)
    assert mv.training_samples_count == len(train.calls[0]["train"])
    assert mv.trained_at is not None and mv.trained_at.tzinfo is not None
    assert mv.report["recall5_after"] == 0.6 and mv.report["output_path"] == expected_path
    assert (Path(expected_path) / "training_report.json").exists()
    assert FakeSentenceTransformer.loaded == [expected_path]


def test_finetune_registers_folder_name(store):
    storage = seed(store)

    result = finetune_model.run_training(storage, train_fn=FakeTrain())

    assert result["status"] == "completed", result
    assert storage.models.get(1).model_path == "e5-finetuned-v1"
    assert result["model_path"] == str(Path(config.MODELS_DIR) / "e5-finetuned-v1")


def test_improved_model_from_finetuned_base_is_activated(store, tmp_path):
    storage = seed(store)
    finetune_model.run_training(storage, train_fn=FakeTrain(before=0.4, after=0.6))
    storage.models.activate(1)  # оператор проверил v1 и активировал её вручную
    v1_path = storage.models.get(1).model_path
    train = FakeTrain(before=0.6, after=0.7)

    result = finetune_model.run_training(storage, source="combined", train_fn=train)

    assert result["status"] == "completed", result
    assert result["version"] == 2 and result["activated"] is True
    assert train.calls[0]["base"] == str(Path(config.MODELS_DIR) / v1_path)
    assert storage.models.active().version == 2


def test_not_improved_model_from_finetuned_base_stays_inactive(store):
    storage = seed(store)
    finetune_model.run_training(storage, train_fn=FakeTrain(before=0.4, after=0.6))
    storage.models.activate(1)

    result = finetune_model.run_training(storage, train_fn=FakeTrain(before=0.7, after=0.7))

    assert result["status"] == "completed" and result["activated"] is False
    assert storage.models.active().version == 1
    assert storage.models.get(2).is_active is False


def test_not_improved_model_registered_inactive(store):
    storage = seed(store)

    result = finetune_model.run_training(storage, train_fn=FakeTrain(before=0.6, after=0.6))

    assert result["status"] == "completed" and result["activated"] is False
    assert storage.models.active().version == 0
    mv = storage.models.get(1)
    assert mv is not None and mv.is_active is False and mv.recall5_after == pytest.approx(0.6)


def test_new_version_has_vectors_for_all_entries(store):
    storage = seed(store)

    finetune_model.run_training(storage, train_fn=FakeTrain())

    rows = storage.classifier.vectors_for_version(1)
    assert sorted(c for c, st, _ in rows if st == "base") == sorted(CODES)
    assert [(c, t) for c, st, t in rows if st == "annotation"] == [(CODES[0], ANNOTATION)]
    # векторы версии 1 ищутся (у всех строк есть embedding)
    hits = storage.classifier.search(np.ones(768, dtype=np.float32), 1, 10, True)
    assert {c for c, _ in hits} == set(CODES)


def test_training_reads_db_not_files(monkeypatch, tmp_path):
    storage = seed(confirmed=8, corrected=4, historical=3)
    monkeypatch.setattr(config, "VECTOR_DB_DIR", str(tmp_path / "no_vector_db"))
    forbidden = ("appeals_log.jsonl", "historical_verified.jsonl", "metadata.json")
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if any(name in str(file) for name in forbidden):
            raise AssertionError(f"чтение файла вместо БД: {file}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    train = FakeTrain()

    result = finetune_model.run_training(storage, source="combined", train_fn=train)

    assert result["status"] == "completed", result
    pairs = train.calls[0]["train"] + train.calls[0]["val"]
    anchors = [p.texts[0] for p in pairs]
    # 8 confirmed + 4 corrected + 3 historical × 3 (вес исторических)
    assert len(pairs) == 8 + 4 + 3 * 3
    assert anchors.count("query: историческое обращение 0") == 3
    assert "query: подтверждённое обращение 0" in anchors
    corrected = [p for p in pairs if p.texts[0] == "query: исправленное обращение 0"]
    assert corrected and all("Вопрос 2" in p.texts[1] for p in corrected)
    # аннотации учтены в тексте записи классификатора
    first = [p for p in pairs if p.texts[0] == "query: подтверждённое обращение 0"][0]
    assert first.texts[1].startswith("passage: Вопрос 1. ")


def test_skipped_below_threshold(monkeypatch):
    monkeypatch.setattr(config, "FINETUNE_THRESHOLD", 100)
    storage = seed()
    train = FakeTrain()

    result = finetune_model.run_training(storage, source="verified", force=False, train_fn=train)

    assert result["status"] == "skipped"
    assert "100" in result["message"]
    assert train.calls == [] and [mv.version for mv in storage.models.list()] == [0]


def test_force_ignores_threshold(monkeypatch):
    monkeypatch.setattr(config, "FINETUNE_THRESHOLD", 100)
    result = finetune_model.run_training(seed(), force=True, train_fn=FakeTrain())
    assert result["status"] == "completed"


def test_failed_train_registers_nothing(store):
    storage = seed(store)

    result = finetune_model.run_training(storage, train_fn=FakeTrain(error=RuntimeError("CUDA OOM")))

    assert result["status"] == "failed"
    assert "CUDA OOM" in result["message"]
    assert "RuntimeError" in result["log_tail"]
    assert result["activated"] is False
    assert [mv.version for mv in storage.models.list()] == [0]
    assert storage.models.active().version == 0
    assert storage.classifier.vectors_for_version(1) == []


def test_failed_vector_build_registers_nothing(store, monkeypatch):
    storage = seed(store)

    class BrokenST(FakeSentenceTransformer):
        def encode(self, texts, **kw):
            raise OSError("диск переполнен")

    monkeypatch.setattr(build_vectordb, "SentenceTransformer", BrokenST)

    result = finetune_model.run_training(storage, train_fn=FakeTrain())

    assert result["status"] == "failed" and "диск переполнен" in result["message"]
    assert [mv.version for mv in storage.models.list()] == [0]
    assert storage.classifier.vectors_for_version(1) == []


def test_cli_main_exit_codes(monkeypatch, capsys):
    from storage import get_storage

    seed(get_storage())
    monkeypatch.setattr(finetune_model, "sentence_transformers_train", FakeTrain())
    assert finetune_model.main(["--source", "combined"]) == 0
    monkeypatch.setattr(finetune_model, "sentence_transformers_train",
                        FakeTrain(error=RuntimeError("boom")))
    assert finetune_model.main(["--source", "verified"]) == 1


# ── auto_finetune / auto_import_historical ───────────────────────────────────

def test_auto_finetune_enqueues_training_job(monkeypatch):
    """Постановка — строка jobs в очереди training (NOTIFY job_queued в PostgreSQL)."""
    from training import auto_finetune
    from storage import get_storage

    storage = seed(get_storage())

    job_id = auto_finetune.main(force=False)

    assert job_id is not None
    job = storage.jobs.get(job_id)
    assert job.job_type == "finetune" and job.status == "queued"
    assert job.queue == "training"
    assert job.payload["source"] == "combined" and job.payload["force"] is False
    assert job.payload["queue"] == "training"


def test_auto_finetune_below_threshold_enqueues_nothing(monkeypatch):
    from training import auto_finetune
    from storage import get_storage

    monkeypatch.setattr(config, "FINETUNE_THRESHOLD", 100)
    storage = seed(get_storage())

    assert auto_finetune.main(force=False) is None
    assert storage.jobs.latest("finetune") is None


def test_auto_import_saves_valid_records_to_db(monkeypatch, tmp_path):
    from training import auto_import_historical as aih
    from training.historical_loader import ValidationResult
    from storage import get_storage

    hist = tmp_path / "historical"
    monkeypatch.setattr(aih, "HISTORICAL_DIR", hist)
    monkeypatch.setattr(aih, "PROCESSED_DIR", hist / "processed")
    monkeypatch.setattr(aih, "ERRORS_DIR", hist / "errors")
    aih.ensure_dirs()
    src = hist / "batch.csv"
    src.write_text("x", encoding="utf-8")
    records = [{"appeal_text": "текст", "assigned_code": CODES[0]}]
    monkeypatch.setattr(aih, "parse_file", lambda path: records)
    monkeypatch.setattr(aih, "validate_codes", lambda recs: ValidationResult(
        recs, [], [], {"total": 1, "valid": 1, "invalid": 0}))

    result = aih.process_file(src)

    assert result["success"] is True and result["saved_to"] == "historical_appeals"
    assert result["records_saved"] == 1
    assert result["report_path"] is None
    assert get_storage().historical.count() == 1
    assert not src.exists() and len(list((hist / "processed").iterdir())) == 1


# ── classifier_agent: запрос в пространстве активной версии ─────────────────

class _RecordingEmbedder:
    def __init__(self):
        self.texts = []

    def encode(self, text, normalize_embeddings=True):
        self.texts.append(text)
        v = np.zeros(768, dtype=np.float32)
        v[0] = 1.0
        return v


def _agent(model_path, version):
    from classifier import classifier_agent

    agent = classifier_agent.ClassifierAgent.__new__(classifier_agent.ClassifierAgent)
    agent.embedder = _RecordingEmbedder()
    agent.model_path = model_path
    agent.model_version = version
    perm = np.roll(np.eye(768, dtype=np.float32), 1, axis=0)  # e0 -> e1
    agent.adapter_W = perm
    agent.adapter_b = np.zeros(768, dtype=np.float32)
    return agent


def test_query_prefix_follows_active_model_path(monkeypatch):
    from classifier import classifier_agent

    monkeypatch.setattr(classifier_agent, "EMBEDDING_MODEL", "BAAI/bge-m3")
    agent = _agent("models/e5-finetuned-v2", 2)
    agent._embed_query("текст")
    assert agent.embedder.texts == ["query: текст"]

    monkeypatch.setattr(classifier_agent, "EMBEDDING_MODEL", BASE_MODEL)
    agent = _agent("BAAI/bge-m3", 2)
    agent._embed_query("текст")
    assert agent.embedder.texts == ["текст"]


def test_adapter_applies_only_to_version_zero(monkeypatch):
    from classifier import classifier_agent

    monkeypatch.setattr(classifier_agent, "ENABLE_EMBEDDING_ADAPTER", True)
    v0 = _agent(BASE_MODEL, 0)._embed_query("т")
    v1 = _agent("models/e5-finetuned-v1", 1)._embed_query("т")
    assert v0[1] == pytest.approx(1.0) and v0[0] == pytest.approx(0.0)  # адаптер применён
    assert v1[0] == pytest.approx(1.0)  # новая версия — без адаптера

    monkeypatch.setattr(classifier_agent, "ENABLE_EMBEDDING_ADAPTER", False)
    assert _agent(BASE_MODEL, 0)._embed_query("т")[0] == pytest.approx(1.0)


# ── Защита от гонки версий и от повторной сборки векторов ───────────────────

def _mv(version, path="models/other"):
    from storage.models import ModelVersion

    return ModelVersion(version=version, base_model_name=BASE_MODEL, model_path=path,
                        is_active=False)


def test_register_new_refuses_taken_version(store):
    from storage.models import ModelVersionExists

    seed(store)
    store.models.register_new(_mv(1))
    with pytest.raises(ModelVersionExists):
        store.models.register_new(_mv(1, "models/second"))
    assert store.models.get(1).model_path == "models/other"
    # upsert register (миграция) по-прежнему перезаписывает
    store.models.register(_mv(1, "models/third"))
    assert store.models.get(1).model_path == "models/third"


def test_concurrent_registration_of_same_version_fails(store):
    """Другое дообучение заняло версию N, пока шло это: результат не пишется."""
    storage = seed(store)

    class RacingTrain(FakeTrain):
        def __call__(self, base, train, val, output_path):
            storage.models.register_new(_mv(1, "models/winner"))
            return super().__call__(base, train, val, output_path)

    result = finetune_model.run_training(storage, train_fn=RacingTrain())

    assert result["status"] == "failed"
    assert "другое дообучение" in result["message"]
    assert storage.models.get(1).model_path == "models/winner"
    assert storage.classifier.vectors_for_version(1) == []
    assert storage.models.active().version == 0


def test_existing_model_dir_refuses_training():
    storage = seed()
    (Path(config.MODELS_DIR) / "e5-finetuned-v1").mkdir(parents=True)
    train = FakeTrain()

    result = finetune_model.run_training(storage, train_fn=train)

    assert result["status"] == "failed" and "уже существует" in result["message"]
    assert train.calls == [] and [mv.version for mv in storage.models.list()] == [0]


def _cli_storage():
    from storage import get_storage

    return seed(get_storage())


def test_build_vectordb_cli_refuses_version_zero(capsys):
    storage = _cli_storage()
    before = len(storage.classifier.vectors_for_version(0))
    assert build_vectordb.main(["--version", "0", "--model-path", BASE_MODEL]) == 2
    assert "версия 0" in capsys.readouterr().out
    assert len(storage.classifier.vectors_for_version(0)) == before
    assert FakeSentenceTransformer.loaded == []


def test_build_vectordb_cli_refuses_unregistered_version(capsys):
    storage = _cli_storage()
    assert build_vectordb.main(["--version", "5", "--model-path", "m"]) == 2
    assert "нет в реестре" in capsys.readouterr().out
    assert storage.classifier.vectors_for_version(5) == []


def test_build_vectordb_cli_refuses_version_with_vectors(capsys):
    storage = _cli_storage()
    storage.models.register(_mv(1, "m"))
    storage.classifier.add_vectors(1, [(CODES[0], "base", "t", np.ones(768, dtype=np.float32))])
    assert build_vectordb.main(["--version", "1", "--model-path", "m"]) == 2
    assert "уже есть векторы" in capsys.readouterr().out
    assert len(storage.classifier.vectors_for_version(1)) == 1


def test_build_vectordb_cli_builds_registered_empty_version():
    storage = _cli_storage()
    storage.models.register(_mv(1, "m"))
    assert build_vectordb.main(["--version", "1", "--model-path", "m"]) == 0
    assert sorted(c for c, st, _ in storage.classifier.vectors_for_version(1)
                  if st == "base") == sorted(CODES)


def test_build_vectordb_cli_usage_without_args(capsys):
    assert build_vectordb.main([]) == 2
    assert "--version" in capsys.readouterr().out


def test_auto_finetune_race_with_active_job_exits_cleanly(monkeypatch, capsys):
    from training import auto_finetune
    from storage import get_storage

    storage = seed(get_storage())
    storage.jobs.create("finetune", {}, queue_class="training")
    monkeypatch.setattr(storage.jobs, "has_active", lambda job_type: False)  # гонка

    assert auto_finetune._cli(["--force"]) == 0
    assert storage.jobs.stats("finetune")["queued"] == 1
    assert "уже в очереди или выполняется" in capsys.readouterr().out


# ── Папка модели: атомарное создание, имя от базовой модели, без сирот ────────

def test_failed_vector_build_removes_model_dir(store, monkeypatch):
    """Сирота (папка + training_report.json) без версии в реестре повторно
    регистрировалась бы перезапуском миграции — при сбое папка удаляется."""
    storage = seed(store)

    class BrokenST(FakeSentenceTransformer):
        def encode(self, texts, **kw):
            raise OSError("диск переполнен")

    monkeypatch.setattr(build_vectordb, "SentenceTransformer", BrokenST)

    result = finetune_model.run_training(storage, train_fn=FakeTrain())

    assert result["status"] == "failed"
    assert not (Path(config.MODELS_DIR) / "e5-finetuned-v1").exists()


def test_failed_train_removes_model_dir(store):
    storage = seed(store)

    class WritingTrain(FakeTrain):
        def __call__(self, base, train, val, output_path):
            (output_path / "checkpoints").mkdir()
            return super().__call__(base, train, val, output_path)

    result = finetune_model.run_training(
        storage, train_fn=WritingTrain(error=RuntimeError("CUDA OOM")))

    assert result["status"] == "failed"
    assert not (Path(config.MODELS_DIR) / "e5-finetuned-v1").exists()
    assert result["model_path"] is None  # папки уже нет — не указываем на неё


def test_cleanup_failure_is_logged_and_path_kept(store, monkeypatch):
    storage = seed(store)

    # Windows: файлы, отображённые в память, не удаляются — папка остаётся.
    monkeypatch.setattr(finetune_model.shutil, "rmtree", lambda *a, **kw: None)

    result = finetune_model.run_training(
        storage, train_fn=FakeTrain(error=RuntimeError("CUDA OOM")))

    assert result["status"] == "failed"
    assert "e5-finetuned-v1" in result["log_tail"]
    assert "Не удалось удалить" in result["log_tail"]
    assert result["model_path"] is not None  # папка осталась — путь сохранён


def test_lost_registration_race_removes_own_model_dir(store):
    storage = seed(store)

    class RacingTrain(FakeTrain):
        def __call__(self, base, train, val, output_path):
            storage.models.register_new(_mv(1, "models/winner"))
            return super().__call__(base, train, val, output_path)

    result = finetune_model.run_training(storage, train_fn=RacingTrain())

    assert result["status"] == "failed"
    assert not (Path(config.MODELS_DIR) / "e5-finetuned-v1").exists()


def test_existing_model_dir_is_kept_untouched():
    """Чужая папка (другой запуск, ручная копия) не удаляется и не перезаписывается."""
    storage = seed()
    foreign = Path(config.MODELS_DIR) / "e5-finetuned-v1"
    foreign.mkdir(parents=True)
    (foreign / "model.safetensors").write_text("чужая модель", encoding="utf-8")

    result = finetune_model.run_training(storage, train_fn=FakeTrain())

    assert result["status"] == "failed" and "уже существует" in result["message"]
    assert (foreign / "model.safetensors").read_text(encoding="utf-8") == "чужая модель"


def test_model_dir_is_created_exclusively(monkeypatch):
    """Проверка существования и создание папки — одна атомарная операция."""
    storage = seed()
    calls = []
    real_mkdir = Path.mkdir

    def spy(self, mode=0o777, parents=False, exist_ok=False):
        if self.name == "e5-finetuned-v1":
            calls.append(exist_ok)
        return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", spy)

    result = finetune_model.run_training(storage, train_fn=FakeTrain())

    assert result["status"] == "completed", result
    assert calls and calls[0] is False


@pytest.mark.parametrize("base, expected", [
    ("intfloat/multilingual-e5-base", "e5-finetuned-v1"),
    ("models/e5-finetuned-v3", "e5-finetuned-v1"),
    (r"D:\models\multilingual-e5-large", "e5-finetuned-v1"),
    ("BAAI/bge-m3", "bge-m3-finetuned-v1"),
    ("models/bge-m3-finetuned-v2", "bge-m3-finetuned-v1"),
    ("sergeyzh/rubert-tiny 2", "rubert-tiny-2-finetuned-v1"),
])
def test_model_dir_name_follows_base_model(base, expected):
    assert finetune_model.model_dir_name(base, 1) == expected


def test_non_e5_base_gets_own_dir_name():
    from storage.models import ModelVersion

    storage = seed()
    storage.models.register(ModelVersion(version=0, base_model_name="BAAI/bge-m3",
                                          model_path="BAAI/bge-m3", is_active=True))

    result = finetune_model.run_training(storage, train_fn=FakeTrain())

    assert result["status"] == "completed", result
    assert result["model_path"] == str(Path(config.MODELS_DIR) / "bge-m3-finetuned-v1")


# ── Владение задачей дообучения: регистрирует версию только владелец ──────────

def test_finetune_not_registered_when_not_owner(store):
    """Аренда задачи потеряна (still_owner → False): версия не регистрируется,
    папка модели удаляется, статус failed с понятной причиной."""
    storage = seed(store)
    checks = []

    def still_owner(conn):
        checks.append(conn)
        return False

    result = finetune_model.run_training(storage, train_fn=FakeTrain(),
                                         still_owner=still_owner)

    assert len(checks) == 1  # один вызов — внутри транзакции регистрации
    assert result["status"] == "failed"
    assert "больше не принадлежит этому воркеру" in result["message"]
    assert [mv.version for mv in storage.models.list()] == [0]
    assert storage.classifier.vectors_for_version(1) == []
    assert not (Path(config.MODELS_DIR) / "e5-finetuned-v1").exists()


def test_finetune_registered_when_still_owner(store):
    storage = seed(store)

    result = finetune_model.run_training(storage, train_fn=FakeTrain(),
                                         still_owner=lambda conn: True)

    assert result["status"] == "completed"
    assert storage.models.get(1) is not None

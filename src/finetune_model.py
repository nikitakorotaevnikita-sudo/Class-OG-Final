"""
finetune_model.py — Дообучение эмбеддинговой модели на верифицированных обращениях

Запуск (вручную; обычно дообучение выполняет воркер очереди training):
    python src/finetune_model.py --source combined
    python src/finetune_model.py --source verified --force

Что делает run_training(storage, ...):
    1. Читает из БД верифицированные карточки (confirmed/corrected) и исторические
       размеченные обращения
    2. Строит обучающие пары (текст обращения, запись классификатора)
    3. Дообучает модель АКТИВНОЙ версии через MultipleNegativesRankingLoss,
       считает recall@5 до и после
    4. Сохраняет модель в models/<семейство>-finetuned-vN/ (+ training_report.json),
       N = следующая версия реестра ml_model_registry, семейство — от имени
       базовой модели (для e5 — прежнее e5-finetuned-vN). Папка создаётся
       атомарно и удаляется, если версия не зарегистрирована
    5. Строит векторы классификатора версии N (build_vectordb.build_database)
    6. Регистрирует версию N; активирует её, только если recall@5 вырос

Требования:
    - Минимум FINETUNE_THRESHOLD верифицированных записей (или force)
    - PyTorch (входит в sentence-transformers)
    - CPU: ~1-4 часа | GPU: ~10-20 минут
"""

import argparse
import random
import re
import shutil
import sys
import traceback
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent))

import env_bootstrap  # noqa: F401  — .env до HF-библиотек

import build_vectordb
import config
from annotations_storage import build_search_text
from storage.models import ModelVersion, ModelVersionExists

SOURCES = ("verified", "historical", "combined")
HISTORICAL_WEIGHT = 3   # исторические пары размечены экспертами — вес 3x в "combined"
MIN_TRAINING_PAIRS = 10
LOG_TAIL_CHARS = 4000


class TrainingPair:
    """Обучающая пара (anchor, positive); совместима с InputExample по полю texts."""

    __slots__ = ("texts",)

    def __init__(self, anchor: str, positive: str):
        self.texts = [anchor, positive]

    def __repr__(self) -> str:
        return f"TrainingPair({self.texts[0][:40]!r}, {self.texts[1][:40]!r})"


class _ClassifierTexts:
    """Тексты записей классификатора для positive-стороны пар.

    Записи — из БД (classifier.load_entries), аннотации загружаются один раз
    и передаются в build_search_text (без запроса к БД на каждую пару).
    """

    def __init__(self, storage):
        self.entries = {e.code: e for e in storage.classifier.load_entries()}
        self.annotations = {
            code: list(dict.fromkeys(texts))
            for code, texts in storage.classifier.annotations_by_code().items()
        }

    def passage(self, code: str) -> Optional[str]:
        entry = self.entries.get(code)
        if entry is None:
            return None
        text = build_search_text(code, entry.name, entry.full_path,
                                 self.annotations.get(code, []))
        return f"passage: {text}"


def build_training_examples(verified_entries: list, texts: _ClassifierTexts) -> list[TrainingPair]:
    """
    Пары из карточек в формате старого лога (appeals.as_log_entries):
      confirmed → (appeal, agent_code)     — агент был прав
      corrected → (appeal, operator_code)  — берём код оператора
    anchor = "query: <текст обращения>", positive = "passage: <запись классификатора>".
    """
    examples = []
    for entry in verified_entries:
        appeal_text = (entry.get("appeal_text") or "")[:800]  # ограничение для модели
        verification = entry.get("verification") or {}
        status = verification.get("status")
        if status == "confirmed":
            codes = [q.get("selected_code") for q in entry.get("agent_questions") or []]
        elif status == "corrected":
            codes = verification.get("operator_codes") or []
        else:
            continue
        for code in codes:
            positive = texts.passage(code)
            if positive:
                examples.append(TrainingPair(f"query: {appeal_text}", positive))
    return examples


def build_historical_examples(records: list[dict], texts: _ClassifierTexts) -> list[TrainingPair]:
    """Пары из исторических обращений {appeal_text, assigned_code} (historical.all())."""
    examples = []
    for record in records:
        appeal_text = (record.get("appeal_text") or "")[:800]
        positive = texts.passage(record.get("assigned_code") or "")
        if positive:
            examples.append(TrainingPair(f"query: {appeal_text}", positive))
    return examples


def evaluate_recall(model, examples: list, k: int = 5) -> float:
    """
    Простая оценка recall@k: для каждого anchor проверяем,
    входит ли его positive в топ-k по cosine similarity.
    """
    if not examples:
        return 0.0

    anchors   = [e.texts[0] for e in examples]
    positives = [e.texts[1] for e in examples]

    anchor_emb   = model.encode(anchors,   normalize_embeddings=True, show_progress_bar=False)
    positive_emb = model.encode(positives, normalize_embeddings=True, show_progress_bar=False)

    scores = anchor_emb @ positive_emb.T  # (N, N) матрица сходства

    hits = 0
    for i in range(len(anchors)):
        top_k_indices = scores[i].argsort()[::-1][:k]
        if i in top_k_indices:
            hits += 1

    return hits / len(anchors)


def sentence_transformers_train(base_model_path: str, train_examples: list,
                                val_examples: list, output_path: Path) -> tuple[float, float]:
    """Дообучение SentenceTransformers (MultipleNegativesRankingLoss, 3 эпохи).

    Возвращает (recall@5 до, recall@5 после); модель сохраняется в output_path.
    """
    from sentence_transformers import InputExample, SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from torch.utils.data import DataLoader

    print(f"\n  Загрузка модели {base_model_path}...")
    model = SentenceTransformer(base_model_path)

    print("  Оценка базовой модели (recall@5)...")
    recall_before = evaluate_recall(model, val_examples, k=5)
    print(f"  recall@5 до дообучения: {recall_before:.3f}")

    print(f"\n  Дообучение... (выходная папка: {output_path})")
    print("  На CPU это займёт от 15 минут до нескольких часов.")
    train = [InputExample(texts=list(e.texts)) for e in train_examples]
    train_dataloader = DataLoader(train, shuffle=True, batch_size=16)
    train_loss = MultipleNegativesRankingLoss(model)
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=3,
        warmup_steps=max(10, len(train) // 10),
        output_path=str(output_path),
        show_progress_bar=True,
        checkpoint_path=str(output_path / "checkpoints"),
        checkpoint_save_steps=100,
    )

    print("\n  Оценка дообученной модели (recall@5)...")
    recall_after = evaluate_recall(model, val_examples, k=5)
    print(f"  recall@5 после дообучения: {recall_after:.3f}")
    return recall_before, recall_after


TrainFn = Callable[[str, list, list, Path], tuple[float, float]]


def model_dir_name(base_model: str, version: int) -> str:
    """Имя папки дообученной версии: `<семейство>-finetuned-v<N>`.

    Семейство — последний компонент пути/имени базовой модели без суффикса
    `-finetuned-vK`. Любое имя с отдельным «e5» даёт `e5`: прежние пути
    models/e5-finetuned-vN не меняются. Символы вне [A-Za-z0-9._-] → «-».
    """
    name = re.split(r"[\\/]", base_model.strip().rstrip("\\/"))[-1]
    name = re.sub(r"-finetuned-v\d+$", "", name)
    if re.search(r"(^|[^a-z0-9])e5([^a-z0-9]|$)", name.lower()):
        return f"e5-finetuned-v{version}"
    family = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "model"
    return f"{family}-finetuned-v{version}"


def _train_and_register(storage, active, version: int, output_path: Path, source: str,
                        train_fn: TrainFn, train_examples: list, val_examples: list,
                        annotated_codes: int, total_pairs: int, verified_pairs: int,
                        historical_pairs: int, say) -> tuple[bool, float, float]:
    """Обучение в output_path, векторы версии N, регистрация (одна транзакция).

    Возвращает (recall@5 вырос, recall@5 до, recall@5 после).
    """
    recall_before, recall_after = train_fn(active.model_path, train_examples,
                                           val_examples, output_path)
    recall_before, recall_after = float(recall_before), float(recall_after)
    improved = recall_after > recall_before
    delta = recall_after - recall_before
    say("─" * 52)
    say(f"  recall@5:  {recall_before:.3f} → {recall_after:.3f}  "
        f"({'↑ +' if delta >= 0 else '↓ '}{delta:.3f})")
    say("─" * 52)

    trained_at = datetime.now(timezone.utc)
    report = {
        "timestamp":      trained_at.astimezone().isoformat(timespec="seconds"),
        "version":        version,
        "base_model":     active.model_path,
        "output_path":    str(output_path),
        "source":         source,
        "train_size":     len(train_examples),
        "val_size":       len(val_examples),
        "recall5_before": round(recall_before, 4),
        "recall5_after":  round(recall_after, 4),
        "improved":       improved,
        "annotated_codes_count": annotated_codes,
        "historical_pairs": historical_pairs,
        "verified_records": verified_pairs,
        "total_training_pairs": total_pairs,
    }
    with open(output_path / "training_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # ── Векторы версии N и регистрация ────────────────────────────────────────
    # Векторы считаются заранее (долго, вне транзакции); строка реестра и векторы
    # пишутся одной транзакцией (векторы ссылаются на реестр внешним ключом):
    # версия без векторов не появляется, при сбое не остаётся ничего.
    say(f"  Построение векторов классификатора для v{version}...")
    rows = build_vectordb.compute_vectors(storage, str(output_path))
    mv = ModelVersion(
        version=version,
        base_model_name=active.model_path,
        model_path=str(output_path),
        is_active=False,
        recall5_before=recall_before,
        recall5_after=recall_after,
        training_samples_count=len(train_examples),
        trained_at=trained_at,
        report=report,
        adapter=None,
    )
    with storage.transaction() as conn:
        kw = {} if conn is None else {"conn": conn}
        try:
            storage.models.register_new(mv, **kw)
        except ModelVersionExists as exc:
            raise RuntimeError(
                f"Версию {version} уже зарегистрировало другое дообучение — результат этого "
                f"запуска не сохранён в реестре ({exc})") from exc
        n_vectors = storage.classifier.add_vectors(version, rows, **kw)
    say(f"  Версия v{version} зарегистрирована, векторов: {n_vectors}")
    return improved, recall_before, recall_after


def run_training(storage, source: str = "combined", force: bool = False,
                 train_fn: Optional[TrainFn] = None) -> dict:
    """Дообучение с регистрацией версии в ml_model_registry.

    Возвращает {status: completed|failed|skipped, message, model_path, version,
    recall5_before, recall5_after, activated, log_tail}. Исключения не
    пробрасываются (status "failed"); версия регистрируется только после того,
    как построены её векторы.
    """
    log: list[str] = []

    def say(line: str = "") -> None:
        print(line)
        log.append(line)

    result = {
        "status": "failed", "message": "", "model_path": None, "version": None,
        "recall5_before": None, "recall5_after": None, "activated": False, "log_tail": "",
    }
    try:
        _train(storage, source, force, train_fn or sentence_transformers_train, result, say)
    except Exception as exc:  # noqa: BLE001 — любой сбой → status failed
        tb = traceback.format_exc()
        say(f"  Ошибка дообучения: {type(exc).__name__}: {exc}")
        result.update(status="failed", activated=False,
                      message=str(exc) or type(exc).__name__)
        result["log_tail"] = ("\n".join(log) + "\n" + tb)[-LOG_TAIL_CHARS:]
        return result
    result["log_tail"] = "\n".join(log)[-LOG_TAIL_CHARS:]
    return result


def _train(storage, source: str, force: bool, train_fn: TrainFn, result: dict, say) -> None:
    if source not in SOURCES:
        raise ValueError(f"Неизвестный источник данных: {source!r} (ожидается {', '.join(SOURCES)})")

    active = storage.models.active()
    if active is None:
        raise RuntimeError("В реестре моделей нет активной версии")

    stats = storage.appeals.stats()
    say("═" * 52)
    say("  Fine-tuning эмбеддинговой модели")
    say(f"  Базовая модель: v{active.version} {active.model_path}")
    say(f"  Источник данных: {source}")
    say("═" * 52)
    say(f"  Верифицированных записей: {stats['verified']} "
        f"(подтверждено {stats['confirmed']}, исправлено {stats['corrected']})")

    threshold = config.FINETUNE_THRESHOLD
    if not force and stats["verified"] < threshold:
        result["status"] = "skipped"
        result["message"] = (f"Недостаточно верифицированных записей: {stats['verified']} "
                             f"из {threshold}. Дообучение не запускалось.")
        say(f"  {result['message']}")
        return

    # ── Обучающие пары (из БД) ────────────────────────────────────────────────
    texts = _ClassifierTexts(storage)
    verified_examples: list[TrainingPair] = []
    historical_examples: list[TrainingPair] = []
    if source in ("verified", "combined"):
        verified_examples = build_training_examples(
            storage.appeals.as_log_entries(("confirmed", "corrected")), texts)
    if source in ("historical", "combined"):
        historical_examples = build_historical_examples(storage.historical.all(), texts)

    if source == "verified":
        all_examples = verified_examples
    elif source == "historical":
        all_examples = historical_examples
    else:  # combined
        all_examples = historical_examples * HISTORICAL_WEIGHT + verified_examples

    say(f"  Обучающих пар (verified): {len(verified_examples)}")
    say(f"  Обучающих пар (historical): {len(historical_examples)}")
    say(f"  Итого: {len(all_examples)}; записей с аннотациями: {len(texts.annotations)}")
    if len(all_examples) < MIN_TRAINING_PAIRS:
        raise ValueError(f"Слишком мало обучающих пар ({len(all_examples)} < "
                         f"{MIN_TRAINING_PAIRS}). Проверьте качество данных.")

    # Train / val split (80/20)
    random.shuffle(all_examples)
    split = int(len(all_examples) * 0.8)
    train_examples = all_examples[:split]
    val_examples = all_examples[split:]
    say(f"  Train: {len(train_examples)}  |  Val: {len(val_examples)}")

    # ── Дообучение ────────────────────────────────────────────────────────────
    version = storage.models.next_version()
    output_path = Path(config.MODELS_DIR) / model_dir_name(active.model_path, version)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Проверка и создание — одна операция: два одновременных запуска не
        # пишут в одну папку.
        output_path.mkdir(exist_ok=False)
    except FileExistsError:
        raise RuntimeError(
            f"Папка {output_path} уже существует, а версии {version} нет в реестре: "
            f"возможно, идёт другое дообучение или осталась незарегистрированная модель. "
            f"Перенесите папку и запустите дообучение снова.") from None
    result.update(version=version, model_path=str(output_path))
    try:
        improved, recall_before, recall_after = _train_and_register(
            storage, active, version, output_path, source, train_fn, train_examples,
            val_examples, len(texts.annotations), len(all_examples),
            len(verified_examples), len(historical_examples), say)
    except BaseException:
        # Папку создал этот запуск, а версии в реестре нет: удаляем, иначе сирота
        # (модель + training_report.json) повторно зарегистрировалась бы миграцией.
        shutil.rmtree(output_path, ignore_errors=True)
        if output_path.exists():
            # Windows: файлы, отображённые в память, могут не удалиться; следующий
            # запуск упрётся в «уже существует» — пишем причину в лог результата.
            say(f"  Не удалось удалить папку {output_path}: удалите её вручную "
                f"перед следующим дообучением.")
        else:
            result["model_path"] = None  # папки больше нет
        raise
    result.update(recall5_before=recall_before, recall5_after=recall_after)

    if active.version == 0:
        # v0 — продовые векторы с адаптером, а recall@5 «до» измерен на исходной
        # модели без адаптера: сравнение нечестное, автоактивация могла бы
        # заменить лучший поиск. Первую дообученную версию включает человек.
        result["message"] = (f"Модель v{version} дообучена (recall@5 {recall_before:.3f} → "
                             f"{recall_after:.3f}) и зарегистрирована неактивной: первую "
                             f"дообученную версию нужно активировать вручную в бэк-офисе "
                             f"после проверки качества — текущая v0 использует адаптер, "
                             f"и автоматическое сравнение с ней некорректно")
    elif improved:
        storage.models.activate(version)
        result["activated"] = True
        result["message"] = (f"Модель v{version} дообучена и активирована "
                             f"(recall@5 {recall_before:.3f} → {recall_after:.3f})")
    else:
        result["message"] = (f"Модель v{version} дообучена, но recall@5 не вырос "
                             f"({recall_before:.3f} → {recall_after:.3f}); версия "
                             f"зарегистрирована неактивной — активировать можно в бэк-офисе")
    result["status"] = "completed"
    say(f"  {result['message']}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fine-tune embedding model")
    parser.add_argument(
        "--source",
        choices=list(SOURCES),
        default="verified",
        help="Data source: verified (operator-verified appeals), historical (expert-labeled), "
             "combined (3x weight for historical)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip FINETUNE_THRESHOLD check. Useful for historical-only imports and demos.",
    )
    args = parser.parse_args(argv)

    from storage import get_storage

    result = run_training(get_storage(), source=args.source, force=args.force)
    print()
    print(f"  Статус: {result['status']}")
    print(f"  {result['message']}")
    if result["status"] == "failed" and result["log_tail"]:
        print(result["log_tail"])
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())

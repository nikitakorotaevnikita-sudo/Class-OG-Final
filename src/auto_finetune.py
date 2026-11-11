"""
auto_finetune.py — Постановка дообучения эмбеддинговой модели в очередь

Проверяет порог верифицированных записей (FINETUNE_THRESHOLD) по БД и, если он
достигнут (или указан --force), создаёт задачу finetune и отправляет её в очередь
training. Дообучение, построение векторов новой версии и её активацию выполняет
воркер дообучения — .env не меняется. Активация автоматическая только при росте
recall@5 и только если база — дообученная версия (v1+); версию, обученную от v0
(векторы с адаптером), активируют вручную в бэк-офисе.

Запуск:
    python src/auto_finetune.py
    python src/auto_finetune.py --force              # без проверки порога
    python src/auto_finetune.py --source verified    # источник данных (по умолчанию combined)
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import config
from storage import get_storage
from storage.models import ActiveJobExists

FINETUNE_QUEUE = "training"


def main(force: bool = False, source: str = "combined") -> Optional[str]:
    """Ставит задачу finetune в очередь; возвращает её id (None — не поставлена)."""
    from queueing.dispatcher import CeleryDispatcher, DispatchError

    print()
    print("=" * 52)
    print("  АВТОМАТИЧЕСКОЕ ДООБУЧЕНИЕ")
    print("=" * 52)

    storage = get_storage()
    stats = storage.appeals.stats()
    active = storage.models.active()
    verified = stats["verified"]
    threshold = config.FINETUNE_THRESHOLD

    print(f"\n  Текущая модель: "
          f"{f'v{active.version} {active.model_path}' if active else 'нет активной версии'}")
    print(f"  Верификаций: {verified} (порог: {threshold})")
    print(f"    подтверждено: {stats['confirmed']}")
    print(f"    исправлено:   {stats['corrected']}")

    if not force and verified < threshold:
        print(f"\n  Недостаточно данных.")
        print(f"  Нужно ещё {threshold - verified} верификаций.")
        print(f"  Запустите с --force для принудительного дообучения.")
        return None

    if storage.jobs.has_active("finetune"):
        print("\n  Дообучение уже в очереди или выполняется — новая задача не создана.")
        return None

    try:
        job = storage.jobs.create("finetune", {"source": source, "force": force},
                                  queue_class=FINETUNE_QUEUE)
    except ActiveJobExists:  # параллельный запуск успел создать задачу
        print("\n  Дообучение уже в очереди или выполняется — новая задача не создана.")
        return None
    msg = {"job_id": job.id, "appeal_id": None, "document_id": None}
    try:
        CeleryDispatcher().send("finetune", msg, FINETUNE_QUEUE)
    except DispatchError as exc:
        storage.jobs.finish(job.id, None, f"Брокер недоступен: {exc}")
        print(f"\n  Не удалось поставить задачу в очередь: {exc}")
        return None

    print(f"\n  Задача дообучения поставлена в очередь {FINETUNE_QUEUE}: {job.id}")
    print("  Статус: GET /api/finetune/status")
    return job.id


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Поставить дообучение в очередь training")
    parser.add_argument("--force", action="store_true", help="не проверять FINETUNE_THRESHOLD")
    parser.add_argument("--source", choices=["verified", "historical", "combined"],
                        default="combined", help="источник обучающих данных")
    args = parser.parse_args(argv)
    main(force=args.force, source=args.source)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())

"""
auto_finetune.py — Автоматическое дообучение эмбеддинговой модели

Запускается после накопления порога верификаций (FINETUNE_THRESHOLD).
Автоматически:
1. Проверяет количество верифицированных записей
2. Запускает дообучение модели
3. Обновляет EMBEDDING_MODEL в .env
4. Перестраивает векторную базу

Запуск:
    python src/auto_finetune.py
    python src/auto_finetune.py --force  # без проверки порога
"""

import sys
import os
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import FINETUNE_THRESHOLD, MODELS_DIR
from appeals_logger import AppealsLogger, LOG_FILE


def check_verified_count() -> tuple[int, int, int]:
    """Возвращает (verified, confirmed, corrected)."""
    logger = AppealsLogger()
    if not LOG_FILE.exists():
        return 0, 0, 0
    stats = logger.stats()
    return stats["verified"], stats["confirmed"], stats["corrected"]


def get_current_embedding_model() -> str:
    """Читает текущую модель из .env."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return "intfloat/multilingual-e5-base"
    content = env_path.read_text(encoding="utf-8")
    match = re.search(r'^EMBEDDING_MODEL=(.+)$', content, re.MULTILINE)
    return match.group(1).strip() if match else "intfloat/multilingual-e5-base"


def update_env_embedding_model(new_model: str):
    """Обновляет EMBEDDING_MODEL в .env."""
    env_path = Path(__file__).parent.parent / ".env"
    content = env_path.read_text(encoding="utf-8")
    new_content = re.sub(
        r'^EMBEDDING_MODEL=.+$',
        f'EMBEDDING_MODEL={new_model}',
        content,
        flags=re.MULTILINE
    )
    env_path.write_text(new_content, encoding="utf-8")
    print(f"  Обновлено: EMBEDDING_MODEL={new_model}")


def run_finetune():
    """Запускает дообучение."""
    print("\n" + "=" * 52)
    print("  ЗАПУСК ДООБУЧЕНИЯ ЭМБЕДДИНГОВОЙ МОДЕЛИ")
    print("=" * 52)

    result = subprocess.run(
        [sys.executable, "src/finetune_model.py"],
        cwd=Path(__file__).parent.parent,
        capture_output=False
    )
    return result.returncode == 0


def run_build_vectordb():
    """Перестраивает векторную базу."""
    print("\n" + "=" * 52)
    print("  ПЕРЕСТРОЙКА ВЕКТОРНОЙ БАЗЫ")
    print("=" * 52)

    result = subprocess.run(
        [sys.executable, "src/build_vectordb.py"],
        cwd=Path(__file__).parent.parent,
        capture_output=False
    )
    return result.returncode == 0


def main(force: bool = False):
    print()
    print("=" * 52)
    print("  АВТОМАТИЧЕСКОЕ ДООБУЧЕНИЕ")
    print("=" * 52)

    verified, confirmed, corrected = check_verified_count()
    current_model = get_current_embedding_model()

    print(f"\n  Текущая модель: {current_model}")
    print(f"  Верификаций: {verified} (порог: {FINETUNE_THRESHOLD})")
    print(f"    подтверждено: {confirmed}")
    print(f"    исправлено:   {corrected}")

    if not force and verified < FINETUNE_THRESHOLD:
        print(f"\n  Недостаточно данных.")
        print(f"  Нужно {FINETUNE_THRESHOLD - verified} верификаций.")
        print(f"  Запустите с --force для принудительного дообучения.")
        return

    if not force:
        print(f"\n  Порог достигнут! Запускаю дообучение...")

    if not run_finetune():
        print("  Ошибка дообучения.")
        return

    latest_model = None
    for d in sorted(MODELS_DIR.glob("e5-finetuned-v*"), key=lambda x: x.name):
        latest_model = str(d)

    if latest_model:
        update_env_embedding_model(latest_model)

    if not run_build_vectordb():
        print("  Ошибка перестройки базы.")
        return

    print("\n" + "=" * 52)
    print("  ГОТОВО! Модель дообучена и векторная база обновлена.")
    print("=" * 52)


if __name__ == "__main__":
    force = "--force" in sys.argv
    main(force=force)
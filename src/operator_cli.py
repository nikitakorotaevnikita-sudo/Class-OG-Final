"""
operator_cli.py — Интерактивный CLI для классификации и верификации обращений

Запуск:
    python src/operator_cli.py

Сценарий работы:
    1. Введите текст обращения (Enter на пустой строке — конец ввода)
    2. Агент классифицирует и показывает результат
    3. Оператор выбирает: [1] Подтвердить  [2] Исправить  [3] Отклонить
    4. При достижении порога верифицированных записей — предлагается дообучение
"""

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from classifier_agent import ClassifierAgent
from appeals_logger import get_logger
from config import FINETUNE_THRESHOLD
import annotations_storage


# ── Вспомогательные функции ────────────────────────────────────────────────────

def print_separator(char="─", width=52):
    print(char * width)

def input_appeal_text() -> str:
    """Многострочный ввод текста обращения. Пустая строка — конец."""
    print()
    print("Введите текст обращения (пустая строка — завершить ввод):")
    print_separator()
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()

def ask_operator_choice() -> str:
    """Запрашивает выбор оператора: 1 / 2 / 3."""
    while True:
        print()
        print("  [1] Подтвердить   [2] Исправить   [3] Отклонить")
        choice = input("  Ваш выбор (1/2/3): ").strip()
        if choice in ("1", "2", "3"):
            return choice
        print("  Введите 1, 2 или 3.")

def ask_correct_codes(result) -> list[str]:
    """
    При ошибке агента оператор вводит правильные коды классификатора.
    Показывает что предложил агент, просит ввести верные коды.
    """
    print()
    print("  Агент предложил:")
    for i, q in enumerate(result.questions, 1):
        print(f"    Вопрос {i}: {q.code} — {q.name[:55]}")

    print()
    print("  Введите правильные коды классификатора через запятую.")
    print("  Пример: 0005.0005.0056.1160, 0005.0005.0063.1188")

    while True:
        raw = input("  Коды: ").strip()
        if not raw:
            print("  Необходимо ввести хотя бы один код.")
            continue
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if codes:
            return codes


def ask_annotation() -> str:
    """
    Запрашивает у оператора аннотацию (описание случаев применения кода).
    Минимум 10 символов.
    """
    print()
    print("  В каких случаях применяется этот код?")
    print("  (минимум 10 символов; Enter — пропустить)")

    while True:
        text = input("  Аннотация: ").strip()
        if not text:
            return ""
        if len(text) < 10:
            print("  Аннотация слишком короткая (минимум 10 символов).")
            continue
        return text

def check_and_offer_finetuning(logger):
    """
    Проверяет порог верифицированных записей.
    Если достигнут — предлагает запустить дообучение.
    """
    stats = logger.stats()
    verified = stats["verified"]
    total = stats["total"]

    print()
    print(f"  Лог: {verified} верифицировано из {total} обращений", end="")

    if verified < FINETUNE_THRESHOLD:
        remaining = FINETUNE_THRESHOLD - verified
        print(f" (ещё {remaining} до дообучения)")
        return

    # Порог достигнут
    print()
    print()
    print("━" * 52)
    print(f"  Накоплено {verified} верифицированных записей!")
    print(f"  Порог для дообучения ({FINETUNE_THRESHOLD}) достигнут.")
    print("━" * 52)
    print()
    answer = input("  Запустить дообучение эмбеддинговой модели? (y/n): ").strip().lower()

    if answer == "y":
        run_finetuning()
    else:
        print("  Дообучение отложено. Запустить вручную:")
        print("    python src/finetune_model.py")

def run_finetuning():
    """Запускает finetune_model.py как подпроцесс."""
    finetune_script = Path(__file__).parent / "finetune_model.py"
    print()
    print("  Запуск дообучения...")
    print_separator()
    result = subprocess.run(
        [sys.executable, str(finetune_script)],
        cwd=Path(__file__).parent.parent,
    )
    print_separator()
    if result.returncode == 0:
        print("  Дообучение завершено успешно.")
        print("  Обновите EMBEDDING_MODEL в .env, чтобы применить новую модель.")
    else:
        print("  Дообучение завершилось с ошибкой. Проверьте вывод выше.")


# ── Основной цикл ──────────────────────────────────────────────────────────────

def main():
    print()
    print("═" * 52)
    print("  Агент классификации обращений граждан")
    print("  Режим оператора (верификация + дообучение)")
    print("═" * 52)

    agent = ClassifierAgent()
    logger = get_logger()

    # Показать текущую статистику при старте
    stats = logger.stats()
    if stats["total"] > 0:
        print()
        print(f"  Статистика лога: {stats['total']} обращений, "
              f"{stats['verified']} верифицировано")

    while True:
        # ── Ввод обращения ────────────────────────────────────────────────────
        appeal_text = input_appeal_text()

        if not appeal_text:
            print("  Текст не введён. Выход.")
            break

        # ── Классификация ─────────────────────────────────────────────────────
        print()
        print("  Классифицирую...")
        try:
            result = agent.classify(appeal_text)
        except Exception as e:
            print(f"  Ошибка классификации: {e}")
            continue

        # ── Показ результата ──────────────────────────────────────────────────
        print()
        print(agent.format_for_operator(result))

        # ── Верификация оператором ────────────────────────────────────────────
        choice = ask_operator_choice()

        if choice == "1":
            logger.confirm(result.log_id)
            print("  Классификация подтверждена.")

        elif choice == "2":
            correct_codes = ask_correct_codes(result)
            annotation_text = ask_annotation()
            logger.correct(result.log_id, operator_codes=correct_codes)
            print(f"  Исправление сохранено. Правильные коды: {', '.join(correct_codes)}")

            if annotation_text:
                annotated_count = 0
                for code in correct_codes:
                    annotations_storage.save_annotation(code, "note", annotation_text)
                    ann = annotations_storage.get_annotations(code)
                    if ann:
                        annotated_count += 1
                print(f"  Аннотация сохранена для {annotated_count} код(ов).")

        elif choice == "3":
            logger.reject(result.log_id)
            print("  Обращение отклонено.")

        # ── Проверка порога дообучения ────────────────────────────────────────
        if choice in ("1", "2"):
            check_and_offer_finetuning(logger)

        # ── Следующее обращение или выход ─────────────────────────────────────
        print()
        again = input("  Следующее обращение? (Enter — да, q — выйти): ").strip().lower()
        if again == "q":
            break

    print()
    print("  До свидания.")


if __name__ == "__main__":
    main()

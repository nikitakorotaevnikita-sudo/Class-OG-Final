"""
classify_manual.py — Ручное тестирование классификации обращений

Запуск:
    python src/cli/classify_manual.py

Сценарий:
    1. Введите текст обращения (пустая строка — конец ввода)
    2. Агент показывает результат классификации
    3. Повторить или выйти
"""

import sys
from pathlib import Path

# Корень импорта — src/. При запуске `python src/<пакет>/<файл>.py` Python кладёт
# в sys.path[0] папку самого скрипта: заменяем её на src, чтобы модули пакета
# не импортировались под двумя именами. `python -m <пакет>.<файл>` с PYTHONPATH=src
# и импорт из тестов работают без этого.
_SRC = Path(__file__).resolve().parents[1]
if sys.path and Path(sys.path[0] or ".").resolve() == Path(__file__).resolve().parent:
    sys.path[0] = str(_SRC)
elif str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from classifier.classifier_agent import ClassifierAgent


def input_appeal_text() -> str:
    """Многострочный ввод текста. Пустая строка — конец."""
    print()
    print("  Введите текст обращения (пустая строка — завершить ввод):")
    print("  " + "─" * 50)
    lines = []
    while True:
        try:
            line = input("  ")
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def main():
    print()
    print("=" * 54)
    print("  Test: Citizens Appeals Classification Agent")
    print("=" * 54)

    print()
    print("  Loading agent...")
    agent = ClassifierAgent()
    print("  Agent ready.")

    while True:
        appeal_text = input_appeal_text()

        if not appeal_text:
            print()
            print("  No text entered. Exiting.")
            break

        print()
        print("  Classifying...")
        try:
            result = agent.classify(appeal_text)
            print()
            print(agent.format_for_operator(result))
        except Exception as e:
            print(f"  ERROR: {e}")

        print()
        again = input("  Try another appeal? (Enter = yes, q = quit): ").strip().lower()
        if again == "q":
            break

    print()
    print("  Done.")


if __name__ == "__main__":
    main()

"""Нормализация ФИО заявителя к формату «Фамилия И.О.»."""

import re

# Слово-токен: буква (кириллица/латиница) + буквы/дефис, опционально с точкой.
_WORD = re.compile(r"^[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z-]*\.?$")


def normalize_fio(raw: str | None) -> str | None:
    """«Иванов Иван Иванович» → «Иванов И.И.». None, если не распарсили."""
    if not raw:
        return None

    # Разбиваем инициалы вида «И.И.» на отдельные токены.
    tokens = [t for t in raw.replace(".", ". ").split() if t]
    tokens = [t for t in tokens if _WORD.match(t)]

    if len(tokens) < 2:
        return None

    surname = tokens[0].rstrip(".")
    surname = surname[:1].upper() + surname[1:]

    initials = "".join(f"{t[0].upper()}." for t in tokens[1:3])
    return f"{surname} {initials}"

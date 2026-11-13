"""ФИО заявителя: нормализация и выбор нужного человека из текста обращения.

Обращение приходит пачкой документов: сопроводительное письмо, тело обращения,
приложения — протоколы собраний, списки подписей, служебные записки. Фамилий в
такой пачке много, и почти все чужие: председатель собрания, секретарь,
руководитель организации, должностное лицо, которому обращение адресовано,
десятки подписавшихся.

Заявитель — тот, ОТ КОГО обращение. Ищется по указателям, а не по «первому ФИО
в тексте»: сначала сопроводительное письмо («направляем обращение ФИО»), затем
шапка («от кого», «заявитель»), затем первое лицо («я, ФИО, обращаюсь»),
контактное лицо для ответа и — у коллективного обращения — первая подпись.
Подписант протокола заявителем не считается, пока не подтверждён шапкой или
основным текстом: иначе обращение уедет в карточку чужого человека.
"""

import re
from typing import Optional

# Слово-токен ФИО: буква (кириллица/латиница), далее буквы, дефис, точки
# (чтобы принять инициалы вида «С.С.»). Отсекает email, цифры, пунктуацию.
_WORD = re.compile(r"^[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z.\-]*$")


def normalize_fio(raw: str | None) -> str | None:
    """«иванов иван иванович» → «Иванов Иван Иванович».

    Возвращает полное ФИО (до 3 токенов), очищенное от лишних пробелов и
    приведённое к заглавной первой букве каждого токена. None — если не
    распарсили (менее двух словных токенов, мусор, email и т.п.).
    """
    if not raw:
        return None

    tokens = [t for t in raw.split() if _WORD.match(t)]

    if len(tokens) < 2:
        return None

    # Первую букву каждого токена — в верхний регистр, остальное как есть
    # (не трогаем «ё», дефисные фамилии, уже проставленные инициалы).
    return " ".join(t[:1].upper() + t[1:] for t in tokens[:3])


# ── Как выглядит ФИО в тексте ───────────────────────────────────────────────

_T = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"          # Иванов, Петрова, Салтыкова-Щедрина
_INITIALS = r"[А-ЯЁ]\.\s?[А-ЯЁ]\."                # И.И. / И. И.
# Регистр важен: «обращение от инициативной группы жителей» — не ФИО. Указатели
# вокруг ищутся без учёта регистра, поэтому здесь он выключается локально.
_NAME = rf"(?-i:{_T}\s+{_T}\s+{_T}|{_T}\s+{_INITIALS})"

# Указатели на заявителя, в порядке приоритета. `genitive` — стоит ли ожидать
# родительный падеж: после «от» и «обращение …» ФИО почти всегда склонено.
_SOURCES: list[tuple[str, re.Pattern, bool]] = [
    # 1. Сопроводительное письмо: «направляем обращение ФИО»
    ("cover",
     re.compile(rf"(?:направля\w+|препровожда\w+|пересыла\w+|поступи\w+|"
                rf"прошу\s+рассмотреть)[^.\n]{{0,80}}?обращени\w+\s+"
                rf"(?:гр\.?\s*|граждан\w+\s+)?(?P<name>{_NAME})", re.IGNORECASE), True),
    ("cover_from",
     re.compile(rf"обращени\w+\s+от\s+(?:гр\.?\s*|граждан\w+\s+)?(?P<name>{_NAME})",
                re.IGNORECASE), True),
    # 2. Шапка обращения: «от кого»
    ("header",
     re.compile(rf"(?:^|\n)\s*от\s+(?:кого\s*[:\-]?\s*)?(?:гр\.?\s*)?(?P<name>{_NAME})",
                re.IGNORECASE), True),
    ("applicant",
     re.compile(rf"(?:заявител\w+|обращается)\s*[:\-]?\s*(?P<name>{_NAME})",
                re.IGNORECASE), False),
    # 3. Первое лицо и контактное лицо для ответа
    ("first_person", re.compile(rf"\bЯ,\s*(?P<name>{_NAME})"), False),
    ("contact",
     re.compile(rf"контактн\w+\s+(?:лиц\w+|данн\w+)[^:\n]{{0,40}}[:\-]\s*(?P<name>{_NAME})",
                re.IGNORECASE), False),
    # 4. Коллективное обращение — первый в списке подписей
    ("collective",
     re.compile(rf"(?:подпис\w+|список\s+подписавш\w+)\s*[:\-]?\s*\n"
                rf"\s*(?:\d+[.)]\s*)?(?P<name>{_NAME})", re.IGNORECASE), False),
]

# Признаки, что ФИО в этой строке принадлежит не заявителю. Проверяются только
# в пределах строки: слово из соседнего абзаца отбрасывало бы верное ФИО.
_NOT_APPLICANT_LINE = re.compile(
    r"председател\w+|секретар\w+|директор\w*|начальник\w*|руководител\w+"
    r"|глав[аеиуы]\b|главе\b|мэр\w*|министр\w*|депутат\w*"
    r"|исполнител\w+|заместител\w+|пресс-служб\w+"
    r"|управляющ\w+\s+компани\w+|застройщик\w*|подрядчик\w*",
    re.IGNORECASE,
)


def _line_of(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return text[start:end if end != -1 else len(text)]


# ── Родительный падеж шапки → именительный ──────────────────────────────────

_PATRONYMIC_MASC_GEN = re.compile(r"(?:ича|ичем)$", re.IGNORECASE)
_PATRONYMIC_FEM_GEN = re.compile(r"(?:овны|евны|ичны|иничны)$", re.IGNORECASE)
_PATRONYMIC_NOM = re.compile(r"(?:ич|на)$", re.IGNORECASE)

_SURNAME_MASC_GEN = re.compile(r"(?:ова|ева|ёва|ина|ына)$", re.IGNORECASE)


def _masc_nominative(surname: str, first: str, patronymic: str) -> tuple[str, str, str]:
    if _SURNAME_MASC_GEN.search(surname):
        surname = surname[:-1]                      # Иванова → Иванов
    if first.endswith(("ея", "ия")):
        first = first[:-1] + "й"                    # Сергея → Сергей, Юрия → Юрий
    elif first.endswith("а"):
        first = first[:-1]                          # Ивана → Иван
    if patronymic.endswith(("а", "ем")):
        patronymic = patronymic[:-1] if patronymic.endswith("а") else patronymic[:-2]
    return surname, first, patronymic


def _fem_nominative(surname: str, first: str, patronymic: str) -> tuple[str, str, str]:
    if surname.endswith("ой"):
        surname = surname[:-2] + "а"                # Петровой → Петрова
    elif surname.endswith("ей"):
        surname = surname[:-2] + "я"
    if first.endswith("ии"):
        first = first[:-2] + "ия"                   # Марии → Мария
    elif first.endswith(("ы", "и")):
        first = first[:-1] + "а"                    # Анны → Анна, Ольги → Ольга
    if patronymic.endswith("ы"):
        patronymic = patronymic[:-1] + "а"          # Ивановны → Ивановна
    return surname, first, patronymic


def _to_nominative(name: str) -> str:
    """«Иванова Ивана Ивановича» → «Иванов Иван Иванович».

    Работает только на типовых окончаниях и только когда форма действительно
    выглядит склонённой: имя в именительном падеже остаётся нетронутым.
    """
    tokens = name.split()

    if len(tokens) == 3:
        surname, first, patronymic = tokens
        if _PATRONYMIC_MASC_GEN.search(patronymic):
            tokens = list(_masc_nominative(surname, first, patronymic))
        elif _PATRONYMIC_FEM_GEN.search(patronymic):
            tokens = list(_fem_nominative(surname, first, patronymic))
        # Отчество уже в именительном («Дмитриевна», «Иванович») — не трогаем.
        return " ".join(tokens)

    if len(tokens) >= 2 and _INITIALS_ONLY.match(" ".join(tokens[1:])):
        surname = tokens[0]
        if surname.endswith("ой"):
            surname = surname[:-2] + "а"            # Ивановой И.И. → Иванова И.И.
        elif _SURNAME_MASC_GEN.search(surname):
            surname = surname[:-1]                  # Иванова И.И. → Иванов И.И.
        return " ".join([surname] + tokens[1:])

    return name


_INITIALS_ONLY = re.compile(rf"^{_INITIALS}$")


# ── Выбор заявителя ─────────────────────────────────────────────────────────

def _rule_candidate(text: str) -> Optional[str]:
    """Первый указатель по приоритету источников, а не по порядку в тексте."""
    for _tag, pattern, genitive in _SOURCES:
        for match in pattern.finditer(text):
            name = match.group("name")
            if _NOT_APPLICANT_LINE.search(_line_of(text, match.start("name"))):
                continue
            return normalize_fio(_to_nominative(name) if genitive else name)
    return None


def _surname_stem(name: str) -> str:
    """Основа фамилии — чтобы найти её в тексте в любом падеже."""
    surname = name.split()[0]
    return surname[:max(3, len(surname) - 2)]


def _mentioned_only_as_third_party(text: str, name: str) -> bool:
    """Все упоминания фамилии — в строках с должностью или подписью протокола."""
    stem = _surname_stem(name).lower()
    lines = [line for line in text.splitlines() if stem in line.lower()]
    if not lines:
        return False
    return all(_NOT_APPLICANT_LINE.search(line) for line in lines)


def extract_applicant_fio(text: Optional[str], llm_hint: Optional[str] = None) -> Optional[str]:
    """ФИО заявителя или `None`.

    Порядок принятия решения:

    1. подсказка модели — она видит структуру документа целиком. Принимается,
       только если фамилия действительно есть в тексте и встречается не только
       в строках с должностью (типовая ошибка — подписант протокола);
    2. правила по указателям: сопроводительное письмо → шапка → первое лицо →
       контактное лицо → первая подпись коллективного обращения;
    3. `None`. Просто «первое ФИО в тексте» не берём: в приложениях чужих имён
       больше, чем своих, а по ФИО на прикладной стороне заводится заявитель.
    """
    if not text:
        return None

    hint = normalize_fio(llm_hint)
    if hint:
        stem = _surname_stem(hint).lower()
        if stem in text.lower() and not _mentioned_only_as_third_party(text, hint):
            return hint

    return _rule_candidate(text)

"""Поиск адреса электронной почты **заявителя** в тексте обращения.

Две разные задачи, решаются по-разному:

* **найти адреса** — регуляркой. Формат строгий, и опечатка сделала бы адрес
  нерабочим, поэтому строку адреса никогда не «печатает» модель: она берётся
  из текста дословно;
* **понять, чей адрес** — по контексту вокруг него.

Второе появилось после отладки на прикладной стороне: там по почте ищется
заявитель, и если подставить чужой адрес, обращение прилипнет к чужой карточке.
В обращениях регулярно встречаются адреса, заявителю не принадлежащие:
депутата, которому написали, приёмной органа власти, управляющей компании.
Поэтому адрес без признаков принадлежности заявителю **не возвращается вовсе** —
пустое поле лучше неверного.
"""

import re
from typing import Optional

# Локальная часть по практическому минимуму RFC 5322: точки, дефисы и +.
# Домен обязательно с TLD из букв — иначе в адреса попадают «1@2».
_EMAIL = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._%+\-]*[A-Za-z0-9])?"
    r"@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?"
    r"\.[A-Za-z]{2,}"
)

# Граждане нередко пишут «собаку» словом, чтобы обойти сборщиков адресов.
_OBFUSCATED = re.compile(
    r"(?P<local>[A-Za-z0-9][A-Za-z0-9._%+\-]*)"
    r"\s*[\(\[]\s*(?:at|dog|собака|соб)\s*[\)\]]\s*"
    r"(?P<domain>[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,})",
    re.IGNORECASE,
)

# Признаки того, что адрес принадлежит заявителю: прямое указание на почту для
# ответа, притяжательные формы, подпись в конце обращения.
_OWNED_BY_APPLICANT = re.compile(
    r"e-?mail"
    r"|мейл|майл|мыло"
    r"|(?:адрес\s+)?(?:электронн\w*\s+почт\w*|эл\.?\s*почт\w*)"
    r"|почт\w*\s+для\s+ответ\w*"
    # Голое «Почта:» — типовая подпись поля в блоке реквизитов заявителя.
    # Для чужих адресов оно безопасно: там в той же строке стоит организация
    # или должность, а признак чужого перевешивает.
    r"|почт\w*\s*[:\-]?\s*$|почт\w*\s*[:\-]"
    r"|мо(?:й|я|ей|его|ю)\s+(?:\w+\s+){0,2}(?:почт\w*|адрес\w*)"
    r"|обратн\w+\s+(?:адрес\w*|связ\w*)"
    r"|(?:прошу|просьба)[^.\n]{0,60}(?:ответ\w*|направ\w*|сообщ\w*)"
    r"|ответ\w*[^.\n]{0,30}(?:прошу|направ\w*|на\s)"
    r"|связаться\s+со\s+мной"
    r"|контактн\w+\s+(?:данн\w*|информац\w*)"
    r"|для\s+связи"
    r"|пишите\s+(?:мне|на)"
    r"|с\s+уважением"
    r"|подпись",
    re.IGNORECASE,
)

# Признаки чужого адреса: орган власти, должностное лицо, организация.
# Проверяются только в пределах СТРОКИ с адресом — иначе слово из соседнего
# абзаца отбрасывало бы верный адрес.
_OWNED_BY_OTHERS = re.compile(
    r"депутат\w*"
    r"|при[её]мн\w+"
    r"|администрац\w+|мэри\w+|правительств\w+"
    r"|министерств\w+|департамент\w*|ведомств\w+|комитет\w*"
    r"|управляющ\w+\s+компани\w+|\bук\b|тсж|снт"
    r"|банк\w*|застройщик\w*|подрядчик\w*|поставщик\w*"
    r"|организац\w+|учрежден\w+|предприяти\w+"
    r"|глав[аы]\s|руководител\w+|начальник\w*|директор\w*|секретар\w+"
    r"|пресс-служб\w+|канцеляри\w+|официальн\w+\s+сайт"
    r"|отправител\w+|исполнител\w+"
    r"|поступило\s+через",
    re.IGNORECASE,
)

# Служебные ящики: заявителю такие адреса не принадлежат никогда.
_SERVICE_LOCAL_PARTS = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "do_not_reply", "postmaster", "mailer-daemon", "mailerdaemon",
    "abuse", "webmaster", "info", "support", "help", "office", "priem",
})

# Сколько символов перед адресом просматривается на признак принадлежности.
# Захватывает и предыдущую строку: подпись «С уважением, Иванов И.И.» обычно
# стоит строкой выше самого адреса.
_LOOKBEHIND = 120

# После адреса признак тоже встречается: «ivanov@mail.ru — моя почта».
_LOOKAHEAD = 60


def _is_service(address: str) -> bool:
    return address.split("@", 1)[0] in _SERVICE_LOCAL_PARTS


def _candidates(text: str) -> list[tuple[str, int, int]]:
    """Адреса из текста: (адрес в нижнем регистре, начало, конец) по порядку."""
    found: list[tuple[str, int, int]] = []
    for match in _EMAIL.finditer(text):
        found.append((match.group(0).lower(), match.start(), match.end()))
    for match in _OBFUSCATED.finditer(text):
        address = f"{match.group('local')}@{match.group('domain')}".lower()
        found.append((address, match.start(), match.end()))

    found.sort(key=lambda item: item[1])

    seen: set[str] = set()
    unique: list[tuple[str, int, int]] = []
    for address, start, end in found:
        if _is_service(address) or address in seen:
            continue
        seen.add(address)
        unique.append((address, start, end))
    return unique


def extract_emails(text: Optional[str]) -> list[str]:
    """Все адреса из текста — в нижнем регистре, без повторов, в порядке текста.

    Без разбора принадлежности: служебные ящики отброшены, остальное как есть.
    Нужна, когда оператору показывают все варианты.
    """
    if not text:
        return []
    return [address for address, _s, _e in _candidates(text)]


def _line_around(text: str, start: int, end: int) -> str:
    """Строка, в которой стоит адрес."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]


def belongs_to_applicant(text: str, start: int, end: int) -> bool:
    """Есть ли рядом с адресом признак того, что он принадлежит заявителю.

    Чужие признаки ищутся только в строке с адресом, свои — в более широком
    окне: подпись «С уважением, …» обычно стоит строкой выше.
    """
    if _OWNED_BY_OTHERS.search(_line_around(text, start, end)):
        return False

    before = text[max(0, start - _LOOKBEHIND):start]
    after = text[end:end + _LOOKAHEAD]
    return bool(_OWNED_BY_APPLICANT.search(before) or _OWNED_BY_APPLICANT.search(after))


def _normalize(raw: Optional[str]) -> str:
    """Приводит ответ модели к виду, сравнимому с найденными адресами."""
    value = (raw or "").strip().lower()
    value = value.removeprefix("mailto:").strip("<>\"' .,;")
    return value


def extract_applicant_email(text: Optional[str], llm_hint: Optional[str] = None) -> Optional[str]:
    """Адрес заявителя или `None`.

    Порядок принятия решения:

    1. подсказка модели — но только если такой адрес **дословно есть в тексте**.
       Модель понимает контекст лучше правил («С уважением, Иванов, ivanov@…»),
       но печатать адрес ей не доверяем: сверяем с найденным списком;
    2. правила по контексту — первый адрес с признаком принадлежности заявителю
       и без признаков чужого;
    3. `None`. Слепой «первый адрес по тексту» убран намеренно: на прикладной
       стороне по почте ищется заявитель, и чужой адрес привязал бы обращение к
       чужой карточке.
    """
    if not text:
        return None

    candidates = _candidates(text)
    if not candidates:
        return None

    hint = _normalize(llm_hint)
    if hint:
        for address, _s, _e in candidates:
            if address == hint:
                return address

    for address, start, end in candidates:
        if belongs_to_applicant(text, start, end):
            return address

    return None

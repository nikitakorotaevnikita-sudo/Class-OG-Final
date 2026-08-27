"""Поиск адреса электронной почты заявителя в тексте обращения.

Детерминированно, регуляркой по тексту — не через LLM. Адрес имеет строгий
формат, и любая опечатка модели делает его нерабочим; при этом ответ по почте
уходит именно на извлечённый адрес, поэтому цена ошибки высокая.

Основная точка входа — `extract_applicant_email`. Когда в тексте несколько
адресов, выбирается тот, что стоит после явного указателя («e-mail:»,
«электронная почта») — в обращениях, пришедших через портал, в подвале часто
висит адрес самого органа власти.
"""

import re

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

# Указатели, после которых идёт адрес заявителя.
_MARKER = re.compile(
    r"(?:e-?mail|мейл|майл|мыло"
    r"|(?:адрес\s+)?(?:электронн\w*\s+почт\w*|эл\.?\s*почт\w*)"
    r"|почт\w*\s+для\s+ответ\w*)",
    re.IGNORECASE,
)

# Служебные ящики: заявителю такие адреса не принадлежат никогда.
_SERVICE_LOCAL_PARTS = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "do_not_reply", "postmaster", "mailer-daemon", "mailerdaemon",
    "abuse", "webmaster",
})

# Сколько символов до адреса просматривается на наличие указателя.
_MARKER_WINDOW = 60


def _is_service(address: str) -> bool:
    return address.split("@", 1)[0] in _SERVICE_LOCAL_PARTS


def extract_emails(text: str | None) -> list[str]:
    """Все адреса из текста — в нижнем регистре, без повторов, в порядке текста.

    Служебные ящики (noreply и подобные) отбрасываются. Замаскированные записи
    вида «ivanov(at)mail.ru» приводятся к обычному виду.
    """
    if not text:
        return []

    found: list[tuple[int, str]] = []
    for match in _EMAIL.finditer(text):
        found.append((match.start(), match.group(0).lower()))
    for match in _OBFUSCATED.finditer(text):
        address = f"{match.group('local')}@{match.group('domain')}".lower()
        found.append((match.start(), address))

    found.sort(key=lambda pair: pair[0])

    result: list[str] = []
    for _pos, address in found:
        if _is_service(address) or address in result:
            continue
        result.append(address)
    return result


def extract_applicant_email(text: str | None) -> str | None:
    """Адрес заявителя или None.

    Приоритет у адреса, перед которым стоит указатель («e-mail: …»): в
    пересланных обращениях в подвале обычно висит адрес органа власти, и без
    приоритизации в поле заявителя попадал бы он.
    """
    if not text:
        return None

    addresses = extract_emails(text)
    if not addresses:
        return None

    lowered = text.lower()
    for address in addresses:
        start = lowered.find(address)
        if start == -1:
            # Адрес был записан замаскированно — указатель искать не по чему.
            continue
        window = lowered[max(0, start - _MARKER_WINDOW):start]
        if _MARKER.search(window):
            return address

    return addresses[0]

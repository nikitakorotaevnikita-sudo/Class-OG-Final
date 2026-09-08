"""Почта заявителя: находим регуляркой, владельца определяем по контексту.

Правило появилось после отладки на прикладной стороне: там по почте ищется
заявитель, и чужой адрес (депутата, приёмной, УК) привязал бы обращение к чужой
карточке. Поэтому адрес без признаков принадлежности заявителю не возвращается.
"""

import sys

sys.path.insert(0, "src")

import pytest
from email_extractor import extract_applicant_email, extract_emails


# ── адрес заявителя распознаётся по признаку принадлежности ──────────────────

@pytest.mark.parametrize("text,expected", [
    ("Прошу ответить на ivanov@mail.ru", "ivanov@mail.ru"),
    ("E-mail: Petrov.P@YANDEX.RU", "petrov.p@yandex.ru"),
    ("мой адрес электронной почты sidorov-77@gmail.com", "sidorov-77@gmail.com"),
    ("Обратный адрес: a.b+tag@sub.domain.co.uk.", "a.b+tag@sub.domain.co.uk"),
    ("Контактные данные: ivanov(at)mail.ru", "ivanov@mail.ru"),
    ("почта для ответа ivanov [собака] rambler.ru", "ivanov@rambler.ru"),
    ("Прошу связаться со мной: petrov@mail.ru", "petrov@mail.ru"),
    ("petrov@mail.ru — моя почта", "petrov@mail.ru"),
    ("С уважением, Иванов И.И.\nivanov@mail.ru", "ivanov@mail.ru"),
])
def test_applicant_email_is_found(text, expected):
    assert extract_applicant_email(text) == expected


# ── чужие адреса не возвращаются ─────────────────────────────────────────────

def test_deputy_email_is_not_taken_as_applicants():
    """Случай из отладки: обращение написано депутату, его адрес — не наш."""
    text = "Обращаюсь к депутату Петрову П.П., deputat@duma72.ru, по вопросу дороги."
    assert extract_applicant_email(text) is None


def test_authority_footer_does_not_win_over_applicant():
    text = (
        "Обращение поступило через портал, отправитель priem@admin-tyumen.ru\n"
        "Прошу направить ответ на e-mail: zayavitel@mail.ru"
    )
    assert extract_applicant_email(text) == "zayavitel@mail.ru"


@pytest.mark.parametrize("text", [
    "Управляющая компания не отвечает, их почта uk-comfort@mail.ru",
    "Администрация города: adm@tyumen.ru",
    "Приёмная главы района: priemnaya-glavy@rayon.ru",
    "Банк отказал, писала на claims@bank.ru",
    "Ответ прошу направить депутату на mail@duma.ru",
])
def test_third_party_email_gives_none(text):
    assert extract_applicant_email(text) is None


def test_no_ownership_signal_gives_none():
    """Слепой «первый по тексту» убран: пустое поле лучше чужого адреса."""
    assert extract_applicant_email("Текст обращения. adm@tym.ru. Конец.") is None


@pytest.mark.parametrize("text", [
    "В обращении нет почты, только телефон 8-999-000-00-00",
    "",
    None,
    "что-то@localhost и 1@2",
])
def test_nothing_to_extract(text):
    assert extract_applicant_email(text) is None


# ── подсказка модели ─────────────────────────────────────────────────────────

def test_llm_hint_wins_when_address_is_in_the_text():
    """Модель понимает контекст лучше правил — но адрес должен быть в тексте."""
    text = "Прошу разобраться. adm-info@tyumen-city.ru, дом 5"
    assert extract_applicant_email(text) is None, "без подсказки признака нет"
    assert extract_applicant_email(text, llm_hint="adm-info@tyumen-city.ru") == \
        "adm-info@tyumen-city.ru"


@pytest.mark.parametrize("hint", [
    "ivanov@mail.ru ",
    "IVANOV@MAIL.RU",
    "mailto:ivanov@mail.ru",
    "<ivanov@mail.ru>",
])
def test_llm_hint_is_normalised(hint):
    text = "Контакты заявителя: ivanov@mail.ru"
    assert extract_applicant_email(text, llm_hint=hint) == "ivanov@mail.ru"


def test_hallucinated_hint_is_ignored():
    """Адреса, которого нет в тексте, в ответе быть не должно."""
    text = "Прошу ответить на ivanov@mail.ru"
    assert extract_applicant_email(text, llm_hint="ivanov@maiI.ru") == "ivanov@mail.ru"
    assert extract_applicant_email(text, llm_hint="выдумано@example.com") == "ivanov@mail.ru"


def test_hint_pointing_at_third_party_is_still_checked_against_text():
    """Подсказка сверяется с текстом, но чужой адрес модель выбрать может.

    Это осознанно: модель видит контекст целиком. Защита здесь одна — адрес
    обязан присутствовать в тексте дословно.
    """
    text = "Депутату Петрову deputat@duma72.ru. Мой адрес ivanov@mail.ru"
    assert extract_applicant_email(text, llm_hint="ivanov@mail.ru") == "ivanov@mail.ru"


# ── список всех адресов остаётся без разбора принадлежности ──────────────────

def test_extract_emails_returns_all_without_ownership_check():
    text = "Депутат deputat@duma72.ru, моя почта ivanov@mail.ru"
    assert extract_emails(text) == ["deputat@duma72.ru", "ivanov@mail.ru"]


def test_service_mailboxes_are_dropped_everywhere():
    text = "Отправлено с noreply@gosuslugi.ru, мой адрес ivanov@mail.ru"
    assert extract_emails(text) == ["ivanov@mail.ru"]
    assert extract_applicant_email(text) == "ivanov@mail.ru"


def test_addresses_deduplicated_in_text_order():
    text = "мой a@mail.ru, потом b@mail.ru, снова A@MAIL.RU"
    assert extract_emails(text) == ["a@mail.ru", "b@mail.ru"]


@pytest.mark.parametrize("text,expected", [
    ("Почта: ivanov@mail.ru.", "ivanov@mail.ru"),
    ("Почта: ivanov@mail.ru,", "ivanov@mail.ru"),
    ("(почта ivanov@mail.ru)", "ivanov@mail.ru"),
    ("«ivanov@mail.ru» — мой адрес", "ivanov@mail.ru"),
])
def test_trailing_punctuation_not_captured(text, expected):
    assert extract_applicant_email(text) == expected


# ── ОС аналитика: реквизиты заявителя, шапки сопроводительных, приложения ────

def test_address_next_to_the_applicants_name_is_taken():
    """Блок реквизитов: ФИО, телефон, почта — указателя «e-mail:» может не быть."""
    text = "Иванов Иван Иванович, 8-999-000-00-00, ivanov@mail.ru, дом 5"
    assert extract_applicant_email(text) == "ivanov@mail.ru"


def test_address_next_to_an_official_name_is_not_taken():
    """ФИО рядом — ещё не признак: у должностного лица тоже есть имя."""
    text = "Директор МУП «Тепло» Кузьмин Артём Сергеевич, kuzmin@teplo72.ru"
    assert extract_applicant_email(text) is None


@pytest.mark.parametrize("phrase", [
    "Ответ написать на ivanov@mail.ru",
    "Для обратной связи: ivanov@mail.ru",
    "Обратная связь — ivanov@mail.ru",
])
def test_reply_formulations_from_the_analyst(phrase):
    assert extract_applicant_email(phrase) == "ivanov@mail.ru"


def test_cover_letter_letterhead_is_skipped():
    """Адрес в шапке бланка сопроводительного письма принадлежит ведомству."""
    text = (
        "АДМИНИСТРАЦИЯ ГОРОДА N\n"
        "625000, ул. Первомайская, 20, priemnaya@admin-n.ru\n\n"
        "Направляем для рассмотрения обращение Иванова И.И.\n\n"
        "Прошу отремонтировать дорогу. Мой e-mail: ivanov@mail.ru\n"
    )
    assert extract_applicant_email(text) == "ivanov@mail.ru"


def test_only_a_letterhead_address_gives_none():
    text = (
        "АДМИНИСТРАЦИЯ ГОРОДА N\n"
        "625000, ул. Первомайская, 20, info-otdel@admin-n.ru\n\n"
        "Направляем для рассмотрения обращение Иванова И.И. по вопросу дороги.\n"
    )
    assert extract_applicant_email(text) is None


def test_attachment_address_is_not_the_applicants():
    """Приложения — протоколы, акты, служебные записки — чужая территория."""
    text = (
        "Прошу отремонтировать кровлю. Моя почта: ivanov@mail.ru\n\n"
        "Приложение: протокол общего собрания собственников.\n"
        "Управляющая организация ООО «Дом», почта для связи: uk-dom@mail.ru\n"
    )
    assert extract_applicant_email(text) == "ivanov@mail.ru"


def test_address_only_in_an_attachment_gives_none():
    text = (
        "Прошу отремонтировать кровлю дома.\n\n"
        "Приложение: протокол общего собрания собственников.\n"
        "Почта для связи: sobranie-dom5@mail.ru\n"
    )
    assert extract_applicant_email(text) is None


def test_several_own_addresses_return_the_first():
    text = "Мои почты: pervaya@mail.ru и vtoraya@mail.ru — пишите на любую."
    assert extract_applicant_email(text) == "pervaya@mail.ru"


def test_extract_emails_still_returns_everything():
    """Список всех адресов структуру документа не учитывает — он для оператора."""
    text = (
        "АДМИНИСТРАЦИЯ ГОРОДА N, priemnaya@admin-n.ru\n"
        "Направляем обращение.\n"
        "Моя почта ivanov@mail.ru\n"
        "Приложение: протокол. Почта УК: uk-dom@mail.ru\n"
    )
    assert extract_emails(text) == [
        "priemnaya@admin-n.ru", "ivanov@mail.ru", "uk-dom@mail.ru"]

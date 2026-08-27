import sys
sys.path.insert(0, "src")
import pytest
from email_extractor import extract_applicant_email, extract_emails


@pytest.mark.parametrize("text,expected", [
    ("Прошу ответить на ivanov@mail.ru", "ivanov@mail.ru"),
    ("E-mail: Petrov.P@YANDEX.RU", "petrov.p@yandex.ru"),
    ("мой адрес электронной почты sidorov-77@gmail.com, спасибо", "sidorov-77@gmail.com"),
    ("Адрес: ул. Ленина, 5. Эл. почта: a.b+tag@sub.domain.co.uk.", "a.b+tag@sub.domain.co.uk"),
    ("контакты: ivanov(at)mail.ru", "ivanov@mail.ru"),
    ("почта ivanov [собака] rambler.ru", "ivanov@rambler.ru"),
    ("В обращении нет почты, только телефон 8-999-000-00-00", None),
    ("", None),
    (None, None),
    # Домен без TLD и адрес из цифр — не адреса.
    ("что-то@localhost и 1@2", None),
])
def test_extract_applicant_email(text, expected):
    assert extract_applicant_email(text) == expected


def test_marker_wins_over_first_address():
    """В пересланных обращениях в подвале висит адрес органа власти."""
    text = (
        "Обращение поступило через портал, отправитель priem@admin-tyumen.ru\n"
        "Прошу направить ответ на e-mail: zayavitel@mail.ru"
    )
    assert extract_applicant_email(text) == "zayavitel@mail.ru"


def test_first_address_used_when_no_marker():
    text = "Пишите мне: first@mail.ru, либо second@mail.ru"
    assert extract_applicant_email(text) == "first@mail.ru"


def test_service_mailboxes_skipped():
    text = "Отправлено с noreply@gosuslugi.ru, ответ прошу на ivanov@mail.ru"
    assert extract_emails(text) == ["ivanov@mail.ru"]
    assert extract_applicant_email(text) == "ivanov@mail.ru"


def test_all_addresses_deduplicated_in_text_order():
    text = "a@mail.ru, потом b@mail.ru, снова A@MAIL.RU"
    assert extract_emails(text) == ["a@mail.ru", "b@mail.ru"]


def test_trailing_punctuation_not_captured():
    for text, expected in (
        ("Почта: ivanov@mail.ru.", "ivanov@mail.ru"),
        ("Почта: ivanov@mail.ru,", "ivanov@mail.ru"),
        ("(почта ivanov@mail.ru)", "ivanov@mail.ru"),
        ("«ivanov@mail.ru»", "ivanov@mail.ru"),
    ):
        assert extract_applicant_email(text) == expected

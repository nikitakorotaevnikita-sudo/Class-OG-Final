"""Меню разделов одинаково на всех страницах.

Новая страница распознавания сначала появилась только в собственном меню:
с главной, из бэк-офиса и с исторических данных попасть в неё было нельзя —
только по прямому адресу. Такое расхождение глазами не ловится, поэтому
проверяется тестом.
"""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "static"
PAGES = ["index.html", "historical.html", "backoffice.html", "vision.html"]

NAV_ITEM = re.compile(r'<a\s+href="([^"]+)"\s+class="nav-item[^"]*">([^<]+)</a>')


def nav_of(page: str) -> list[tuple[str, str]]:
    html = (STATIC / page).read_text(encoding="utf-8")
    return [(href, title.strip()) for href, title in NAV_ITEM.findall(html)]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_offers_the_same_sections(page):
    assert nav_of(page) == nav_of("index.html"), (
        f"меню на {page} отличается от главной — раздел окажется недоступен")


@pytest.mark.parametrize("page", PAGES)
def test_recognition_is_reachable_from_everywhere(page):
    assert any(href == "/vision" for href, _ in nav_of(page)), \
        f"со страницы {page} в распознавание не попасть"


@pytest.mark.parametrize("page", ["index.html", "historical.html", "vision.html"])
def test_current_section_is_marked_active(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert html.count('class="nav-item active"') == 1, \
        "ровно один пункт меню должен быть отмечен текущим"


def test_backoffice_marks_no_section():
    """Бэк-офис открывается кнопкой в шапке и разделом меню не является."""
    html = (STATIC / "backoffice.html").read_text(encoding="utf-8")
    assert 'class="nav-item active"' not in html

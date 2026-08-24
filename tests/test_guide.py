from types import SimpleNamespace

from keyboards.main import guide_index_kb, how_to_kb, main_menu
from services.guide import INDEX_TEXT, PAGES, page_text
from tests.test_keyboards import _pairs
from utils.callbacks import NAV_GUIDE, NAV_MAIN


def test_guide_pages_cover_menu_topics():
    assert set(PAGES) == {
        "write",
        "cig",
        "snus",
        "fool",
        "sleep",
        "caf",
        "alc",
        "act",
        "cm",
        "mk",
        "st",
        "hist",
        "set",
        "bal",
        "keys",
    }
    for key, body in PAGES.items():
        assert page_text(key) == body
        assert "<b>" in body
        assert len(body) < 3500
    assert page_text("missing") is None


def test_guide_index_buttons_match_pages():
    pairs = dict(_pairs(guide_index_kb()))
    for key in PAGES:
        assert f"g:{key}" in pairs.values()
    assert pairs["🏠 Меню"] == NAV_MAIN
    assert "Напоминаний нет" in INDEX_TEXT or "напоминаний нет" in INDEX_TEXT.lower()
    assert "дневник" in INDEX_TEXT.lower() or "привычек" in INDEX_TEXT.lower()


def test_main_menu_and_onboarding_open_guide():
    menu = dict(_pairs(main_menu(SimpleNamespace(), False)))
    assert menu["📖 Гайд"] == NAV_GUIDE
    onboarding = dict(_pairs(how_to_kb()))
    assert onboarding["📖 Подробный гайд"] == NAV_GUIDE
    assert onboarding["Понятно"] == "onb:ok"


def test_guide_explains_core_flows():
    blob = "\n".join(PAGES.values()).lower()
    for needle in (
        "сигарета",
        "снюс",
        "шайб",
        "сон",
        "кофеин",
        "алкоголь",
        "активность",
        "кастом",
        "метк",
        "статистик",
        "истори",
        "баланс",
        "csv",
    ):
        assert needle in blob
    assert "напомним" not in blob
    assert "напомню" not in blob

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
        "stp",
        "wgt",
        "ds",
        "cm",
        "mk",
        "st",
        "hist",
        "set",
        "bal",
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
    assert "Кнопки внизу" not in pairs
    assert "напоминание отметить подъём" in INDEX_TEXT.lower()
    assert "напоминание оценить день" in INDEX_TEXT.lower()
    assert "дневник" in INDEX_TEXT.lower() or "привычек" in INDEX_TEXT.lower()


def test_main_menu_and_onboarding_open_guide():
    from handlers.settings import TRACK_PROMPT
    from keyboards.main import track_metrics_kb

    menu = dict(_pairs(main_menu(SimpleNamespace(), False)))
    assert menu["📖 Гайд"] == NAV_GUIDE
    onboarding = dict(_pairs(how_to_kb()))
    assert onboarding["📖 Подробный гайд"] == NAV_GUIDE
    assert onboarding["Понятно"] == "onb:ok"
    assert TRACK_PROMPT == "Какие метрики вести:"
    track = dict(_pairs(track_metrics_kb(set())))
    assert track["☐ 🚬 Сигареты"] == "set:trk:cigarettes"


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
        "шаг",
        "вес",
        "самочувств",
        "стресс",
        "кастом",
        "метк",
        "статистик",
        "истори",
        "баланс",
        "csv",
    ):
        assert needle in blob
    assert "напомнит" in PAGES["sleep"].lower()
    assert "напомнить встать" in PAGES["set"].lower()
    assert "напомнить оценить" in PAGES["set"].lower()
    assert "напоминание оценить день" in PAGES["ds"].lower()
    assert "во сколько проснулись" in PAGES["sleep"].lower()
    assert "указать время" in PAGES["sleep"].lower()
    assert "когда заснули" in PAGES["sleep"].lower()
    assert "с телефоном и без" in PAGES["sleep"].lower()
    assert "отдельн" in PAGES["sleep"].lower() or "метрик" in PAGES["sleep"].lower()
    assert "в любой момент" in PAGES["sleep"].lower()
    assert "прошл" in PAGES["sleep"].lower()
    assert "вчера" in PAGES["sleep"].lower()
    assert "не пряч" in PAGES["sleep"].lower() or "все сразу" in PAGES["sleep"].lower()

from __future__ import annotations

from keyboards.main import (
    activity_duration_kb,
    ago_pick_kb,
    back_kb,
    calendar_kb,
    cancel_kb,
    drink_amount_kb,
    hours_kb,
    minutes_kb,
    now_or_time,
    score_kb,
    skip_comment_kb,
    sleep_menu,
    timezone_kb,
    when_kb,
)
from handlers.time_pick import time_pick_back_action
from states.diary import TimePickSG
from utils.callbacks import (
    ENTRY_ACT,
    ENTRY_ALC,
    ENTRY_CAF,
    NAV_BACK,
    NAV_HISTORY,
    NAV_MAIN,
    NAV_METRICS,
    NAV_SETTINGS,
)


def _pairs(markup) -> list[tuple[str, str]]:
    return [(btn.text, btn.callback_data) for row in markup.inline_keyboard for btn in row]


def test_nav_does_not_label_menu_as_back():
    pairs = _pairs(now_or_time("cig"))
    assert ("🏠 Меню", NAV_MAIN) in pairs
    assert all(text != "⬅️ Назад" for text, _ in pairs)
    assert all(text != "✖️ Отмена" for text, _ in pairs)


def test_now_or_time_has_relative_options():
    pairs = _pairs(now_or_time("cig"))
    assert ("5 мин назад", "cig:ago:5") in pairs
    assert ("⏱ Сколько назад", "cig:agoask") in pairs
    assert ("⌨️ Ввести текстом", "cig:txt") in pairs


def test_now_or_time_back_goes_to_previous_screen():
    pairs = _pairs(when_kb("caft"))
    assert ("⬅️ Назад", ENTRY_CAF) in pairs
    assert ("🏠 Меню", NAV_MAIN) in pairs


def test_ago_pick_has_custom_number():
    pairs = _pairs(ago_pick_kb("cig"))
    assert ("1 ч", "cig:ago:60") in pairs
    assert ("⌨️ Ввести число", "cig:agon") in pairs


def test_sleep_menu_has_relative_options():
    pairs = _pairs(sleep_menu())
    assert ("5 мин назад", "slp:ago:5") in pairs
    assert ("⌨️ Ввести текстом", "slp:txt") in pairs


def test_calendar_has_yesterday_and_daybefore():
    pairs = _pairs(calendar_kb(2026, 8))
    assert ("Сегодня", "cal:today") in pairs
    assert ("Вчера", "cal:yesterday") in pairs
    assert ("Позавчера", "cal:daybefore") in pairs


def test_cancel_without_target_is_menu():
    pairs = _pairs(cancel_kb())
    assert pairs == [("🏠 Меню", NAV_MAIN)]


def test_cancel_with_target_stays_in_flow():
    pairs = _pairs(cancel_kb(NAV_METRICS))
    assert ("✖️ Отмена", NAV_METRICS) in pairs
    assert ("🏠 Меню", NAV_MAIN) in pairs
    assert NAV_MAIN not in {data for text, data in pairs if text == "✖️ Отмена"}


def test_score_first_step_is_menu_not_cancel():
    pairs = _pairs(score_kb("md"))
    assert ("🏠 Меню", NAV_MAIN) in pairs
    assert all(text != "✖️ Отмена" for text, _ in pairs)
    assert all(text != "⬅️ Назад" for text, _ in pairs)


def test_timezone_registration_has_no_menu_cancel():
    pairs = _pairs(timezone_kb())
    assert all(data != NAV_MAIN for _, data in pairs)
    assert all(text != "✖️ Отмена" for text, _ in pairs)


def test_timezone_settings_back_is_settings():
    pairs = _pairs(timezone_kb(NAV_SETTINGS))
    assert ("⬅️ Назад", NAV_SETTINGS) in pairs
    assert ("🏠 Меню", NAV_MAIN) in pairs


def test_calendar_history_back():
    pairs = _pairs(calendar_kb(2026, 8, prefix="hcal", back=NAV_HISTORY))
    assert ("⬅️ Назад", NAV_HISTORY) in pairs
    assert ("🏠 Меню", NAV_MAIN) in pairs
    assert all(text != "✖️ Отмена" for text, _ in pairs)


def test_hours_and_minutes_back_inside_picker():
    assert ("⬅️ Назад", NAV_BACK) in _pairs(hours_kb())
    assert ("⬅️ Назад", NAV_BACK) in _pairs(minutes_kb())
    assert ("🏠 Меню", NAV_MAIN) in _pairs(hours_kb())


def test_skip_comment_back():
    pairs = _pairs(skip_comment_kb(ENTRY_CAF))
    assert ("⬅️ Назад", ENTRY_CAF) in pairs
    assert ("Пропустить", "wb:skip") in pairs


def test_back_kb_without_menu():
    pairs = _pairs(back_kb("tz:list", menu=False))
    assert pairs == [("⬅️ Назад", "tz:list")]


def test_time_pick_back_action_stack():
    assert time_pick_back_action(TimePickSG.minute.state, date_shortcuts=False) == "hours"
    assert time_pick_back_action(TimePickSG.manual.state, date_shortcuts=False) == "hours"
    assert time_pick_back_action(TimePickSG.hour.state, date_shortcuts=False) == "date"
    assert time_pick_back_action(TimePickSG.hour.state, date_shortcuts=True) == "exit"
    assert time_pick_back_action(TimePickSG.date.state, date_shortcuts=False) == "exit"
    assert time_pick_back_action(TimePickSG.date.state, date_shortcuts=True) == "hours"
    assert time_pick_back_action(TimePickSG.ago_pick.state, date_shortcuts=False) == "when"
    assert time_pick_back_action(TimePickSG.when_text.state, date_shortcuts=False) == "when"
    assert time_pick_back_action(TimePickSG.ago_minutes.state, date_shortcuts=False) == "ago_pick"


def test_alcohol_amount_presets_use_ml_callbacks():
    pairs = _pairs(drink_amount_kb("alc", "beer", ENTRY_ALC))
    assert ("0,5 л", "alc:q:500") in pairs
    assert ("330 мл", "alc:q:330") in pairs
    assert ("1 порция", "alc:q:pcs:1") in pairs


def test_activity_duration_presets():
    pairs = _pairs(activity_duration_kb(ENTRY_ACT))
    assert ("30 мин", "act:d:30") in pairs
    assert ("1,5 ч", "act:d:90") in pairs

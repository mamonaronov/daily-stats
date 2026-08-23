from __future__ import annotations

from types import SimpleNamespace

from keyboards.main import (
    activity_duration_kb,
    ago_pick_kb,
    back_kb,
    calendar_kb,
    cancel_kb,
    custom_metrics_kb,
    drink_amount_kb,
    hours_kb,
    main_menu,
    marker_card_kb,
    marker_name_kb,
    markers_root_kb,
    metric_duration_kb,
    metric_number_kb,
    metric_templates_kb,
    metric_time_kb,
    metric_types_kb,
    metric_units_kb,
    minutes_kb,
    now_or_time,
    score_kb,
    skip_comment_kb,
    sleep_row,
    spam_alert_kb,
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
    NAV_MARKERS,
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


def test_sleep_row_changes_with_phase():
    idle = [btn.callback_data for btn in sleep_row(None)]
    assert idle == ["slp:wake", "slp:phone", "slp:nophone"]
    with_phone = SimpleNamespace(
        phase=lambda: "with_phone",
    )
    assert [btn.callback_data for btn in sleep_row(with_phone)] == [
        "slp:wake",
        "slp:wakeup",
        "slp:away",
    ]


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


def test_main_menu_custom_metrics_button():
    pairs = _pairs(main_menu(SimpleNamespace(), False))
    assert ("📌 Кастом", "n:cm") in pairs
    assert ("🔖 Метки", NAV_MARKERS) in pairs
    assert all(text != "📌 Показатели" for text, _ in pairs)
    assert all("Настроение" not in text for text, _ in pairs)
    assert all("Самочувствие" not in text for text, _ in pairs)
    assert all("Заметка" not in text for text, _ in pairs)
    assert all("Оценить день" not in text for text, _ in pairs)


def test_custom_metrics_list_has_quick_add():
    metric = SimpleNamespace(id=3, name="Вода", enabled=1)
    pairs = _pairs(custom_metrics_kb([metric], True))
    assert ("Вода", "cm:o:3") in pairs
    assert ("➕", "cm:add:3") in pairs
    assert ("➕ Создать метрику", "cm:new") in pairs


def test_custom_metrics_disabled_has_no_quick_add():
    metric = SimpleNamespace(id=3, name="Вода", enabled=0)
    pairs = _pairs(custom_metrics_kb([metric], True))
    assert ("Вода (выкл)", "cm:o:3") in pairs
    assert ("➕", "cm:add:3") not in pairs


def test_metric_templates_and_types_explain_choice():
    templates = _pairs(metric_templates_kb())
    assert ("💧 Вода · мл", "cm:tpl:water") in templates
    assert ("✏️ Своя метрика", "cm:own") in templates
    types = dict(_pairs(metric_types_kb()))
    assert types["🔢 Число"] == "cm:t:number"
    assert types["📋 Выбор"] == "cm:t:choice"
    assert types["🕐 Время суток"] == "cm:t:time"


def test_metric_units_and_value_presets():
    units = dict(_pairs(metric_units_kb()))
    assert units["мл"] == "cm:u:ml"
    assert units["Без единицы"] == "cm:u:none"
    assert units["Другая единица"] == "cm:u:own"
    numbers = dict(_pairs(metric_number_kb("мл", "cm:o:1")))
    assert numbers["250 мл"] == "cm:q:250"
    assert numbers["1 л"] == "cm:q:1000"
    duration = dict(_pairs(metric_duration_kb("cm:o:1")))
    assert duration["30 мин"] == "cm:d:30"
    times = dict(_pairs(metric_time_kb("cm:o:1")))
    assert times["07:00"] == "cm:tm:0700"


def test_saved_entry_actions_use_undo():
    from keyboards.main import confirm_remove_kb, entry_actions, sleep_onset_kb

    pairs = _pairs(entry_actions("cig", 7, True, undo=True))
    assert ("🗑 Отменить", "un:cig:7") in pairs
    assert ("✏️ Изменить", "ed:cig:7") in pairs
    confirm = dict(_pairs(confirm_remove_kb("cig", 7, undo=True)))
    assert confirm["Отменить"] == "unok:cig:7"
    assert confirm["Оставить"] == "sv:cig:7"
    onset = dict(_pairs(sleep_onset_kb("wu", 3)))
    assert onset["🗑 Отменить"] == "un:wu:3"
    assert onset["Позже"] == "slp:later:wu:3"


def test_markers_root_and_card():
    marker = SimpleNamespace(
        id=4,
        name="Экзамен",
        occurred_at="2026-05-12T07:00:00+00:00",
        period_role="start",
    )
    period = SimpleNamespace(id=9, start_name="Экзамен", start_at="2026-05-12T07:00:00+00:00")
    pairs = _pairs(markers_root_kb([marker], [period], True, "UTC"))
    assert ("➕ Метка", "mk:new") in pairs
    assert ("▶️ Начало периода", "mk:start") in pairs
    assert ("⏹ Конец периода", "mk:end") in pairs
    assert ("🔗 Объединить", "mk:join") in pairs
    assert ("mk:o:4" in {cb for _, cb in pairs})
    assert ("mk:p:9" in {cb for _, cb in pairs})
    card = dict(_pairs(marker_card_kb(4, True, period_id=9)))
    assert card["✏️ Время"] == "ed:mk:4"
    assert card["🔓 Убрать период"] == "mk:u:9"
    assert card["📝 Название"] == "mk:nm:4"
    same = dict(_pairs(marker_name_kb("Экзамен")))
    assert same["Как у начала: Экзамен"] == "mk:samename"


def test_when_kb_marker_goes_back_to_markers():
    pairs = _pairs(when_kb("mkt"))
    assert ("⬅️ Назад", NAV_MARKERS) in pairs
    assert ("Сейчас", "mkt:now") in pairs


def test_spam_alert_kb_opens_user_card():
    pairs = _pairs(spam_alert_kb(42))
    assert ("👤 Карточка", "ad:u:42") in pairs
    assert ("🚫 Заблокировать", "ad:bn:42") in pairs

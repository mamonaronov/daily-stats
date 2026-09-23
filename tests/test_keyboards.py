from __future__ import annotations

from types import SimpleNamespace

from keyboards.main import (
    activity_duration_kb,
    ago_pick_kb,
    back_kb,
    balance_kb,
    calendar_kb,
    cancel_kb,
    custom_metrics_kb,
    daily_score_reminder_kb,
    drink_amount_kb,
    hours_kb,
    legal_consent_kb,
    legal_docs_kb,
    legal_page_kb,
    main_menu,
    marker_card_kb,
    marker_name_kb,
    markers_root_kb,
    metric_duration_kb,
    metric_number_kb,
    metric_time_kb,
    metric_types_kb,
    metric_units_kb,
    minutes_kb,
    now_or_time,
    score_kb,
    score_reminder_kb,
    settings_kb,
    skip_comment_kb,
    sleep_rows,
    spam_alert_kb,
    stats_metrics_kb,
    steps_day_kb,
    steps_value_kb,
    timezone_kb,
    track_metrics_kb,
    wake_kind_kb,
    wake_reminder_kb,
    wake_up_reminder_kb,
    when_kb,
)
from handlers.time_pick import time_pick_back_action
from services.ui_prefs import TRACKABLE_TYPES
from states.diary import TimePickSG
from utils.callbacks import (
    ENTRY_ACT,
    ENTRY_ALC,
    ENTRY_CAF,
    NAV_BACK,
    NAV_HISTORY,
    NAV_GUIDE,
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
    assert all(text != "Вчера" for text, _ in pairs)
    assert all(text != "Сегодня" for text, _ in pairs)


def test_now_or_time_back_goes_to_previous_screen():
    pairs = _pairs(when_kb("caft"))
    assert ("⬅️ Назад", ENTRY_CAF) in pairs
    assert ("🏠 Меню", NAV_MAIN) in pairs


def test_ago_pick_has_custom_number():
    pairs = _pairs(ago_pick_kb("cig"))
    assert ("1 ч", "cig:ago:60") in pairs
    assert ("⌨️ Ввести число", "cig:agon") in pairs


def _sleep_callbacks(rows) -> list[str]:
    return [btn.callback_data for row in rows for btn in row]


def _sleep_texts(rows) -> list[str]:
    return [btn.text for row in rows for btn in row]


def test_sleep_rows_keep_all_actions():
    core = ["slp:wake", "slp:wakeup", "slp:up", "slp:askonset", "slp:phone", "slp:nophone"]
    idle = sleep_rows(None)
    assert _sleep_callbacks(idle) == [*core, "slp:away"]
    assert _sleep_texts(idle) == [
        "Проснулся",
        "Проснулся и встал",
        "Встал",
        "Заснул?",
        "Лёг с телефоном",
        "Лёг без телефона",
        "Убрал телефон",
    ]
    with_phone = SimpleNamespace(phase=lambda: "with_phone")
    phone_rows = sleep_rows(with_phone)
    assert _sleep_callbacks(phone_rows) == [*core, "slp:away"]
    assert "Убрал телефон" in _sleep_texts(phone_rows)
    assert "Лёг с телефоном" in _sleep_texts(phone_rows)
    no_phone = SimpleNamespace(phase=lambda: "no_phone")
    assert _sleep_callbacks(sleep_rows(no_phone)) == [*core, "slp:away"]
    asleep = SimpleNamespace(phase=lambda: "asleep")
    assert _sleep_callbacks(sleep_rows(asleep)) == [*core, "slp:away"]
    awake = SimpleNamespace(phase=lambda: "awake", sleep_onset_at=None)
    assert _sleep_callbacks(sleep_rows(awake)) == [*core, "slp:away"]
    awake_done = SimpleNamespace(phase=lambda: "awake", sleep_onset_at="2026-08-16T21:00:00+00:00")
    assert _sleep_callbacks(sleep_rows(awake_done)) == [*core, "slp:away"]
    need_onset = SimpleNamespace(phase=lambda: "need_onset")
    assert _sleep_callbacks(sleep_rows(need_onset)) == [*core, "slp:away"]
    assert "Лёг без телефона" in _sleep_texts(sleep_rows(need_onset))


def test_calendar_has_yesterday_and_daybefore():
    pairs = _pairs(calendar_kb(2026, 8))
    assert ("Сегодня", "cal:today") in pairs
    assert ("Вчера", "cal:yesterday") in pairs
    assert ("Позавчера", "cal:daybefore") in pairs


def test_calendar_marks_days_with_open_scores():
    from datetime import date, timedelta

    today = date(2026, 8, 17)
    marks = {
        today: 2,
        today - timedelta(days=1): 1,
        date(2026, 8, 3): 6,
    }
    pairs = dict(_pairs(calendar_kb(2026, 8, prefix="dscal", open_scores=marks, today=today)))
    assert pairs["•17"] == "dscal:2026-08-17"
    assert pairs["•16"] == "dscal:2026-08-16"
    assert pairs["•3"] == "dscal:2026-08-03"
    assert pairs["1"] == "dscal:2026-08-01"
    assert pairs["Сегодня · ещё 2"] == "dscal:today"
    assert pairs["Вчера · ещё 1"] == "dscal:yesterday"
    assert pairs["Позавчера"] == "dscal:daybefore"


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


def test_legal_consent_kb_has_docs_and_accept():
    pairs = _pairs(legal_consent_kb())
    assert ("📄 Политика конфиденциальности", "lg:p:0:c") in pairs
    assert ("📜 Пользовательское соглашение", "lg:t:0:c") in pairs
    assert ("✅ Принимаю", "lg:ok") in pairs
    assert all(data != NAV_MAIN for _, data in pairs)


def test_legal_page_kb_paginates_and_returns():
    consent = dict(_pairs(legal_page_kb("p", 1, 3, "c")))
    assert consent["«"] == "lg:p:0:c"
    assert consent["2/3"] == "noop"
    assert consent["»"] == "lg:p:2:c"
    assert consent["⬅️ Назад"] == "lg:home"
    assert consent["✅ Принимаю"] == "lg:ok"
    settings = dict(_pairs(legal_page_kb("t", 0, 1, "s")))
    assert settings["⬅️ Назад"] == "lg:docs"
    assert settings["🏠 Меню"] == NAV_MAIN
    assert "✅ Принимаю" not in settings


def test_legal_docs_kb_lists_both_documents():
    pairs = _pairs(legal_docs_kb())
    assert ("📄 Политика конфиденциальности", "lg:p:0:s") in pairs
    assert ("📜 Пользовательское соглашение", "lg:t:0:s") in pairs
    assert ("⬅️ Назад", NAV_SETTINGS) in pairs
    assert ("🏠 Меню", NAV_MAIN) in pairs


def test_settings_kb_includes_legal_docs():
    user = SimpleNamespace(
        timezone="Europe/Moscow",
        default_sleep_time="23:00",
        wake_up_reminder_time=None,
        daily_score_reminder_time=None,
    )
    pairs = _pairs(settings_kb(user))
    assert ("📋 Метрики", "set:trk") in pairs
    assert ("☐ Прятать пустые края сна", "set:sedge") in pairs
    assert ("⏰ Напомнить встать: выкл", "set:wake") in pairs
    assert ("🙂 Напомнить оценить: выкл", "set:dsr") in pairs
    assert ("📄 Политика и соглашение", "lg:docs") in pairs
    assert all(data not in {"lg:p:0:s", "lg:t:0:s"} for _, data in pairs)
    assert ("🗑 Удалить аккаунт", "set:del") in pairs
    assert all(text != "📋 Кнопки меню" for text, _ in pairs)
    hidden = SimpleNamespace(
        timezone="Europe/Moscow",
        default_sleep_time="23:00",
        wake_up_reminder_time=None,
        daily_score_reminder_time=None,
        ui_prefs_json='{"tracked": [], "hide_sleep_empty_edges": true}',
    )
    assert ("☑ Прятать пустые края сна", "set:sedge") in _pairs(settings_kb(hidden))


def test_balance_kb_links_owner_and_drops_paid_button():
    markup = balance_kb("@owner")
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    urls = [btn.url for row in markup.inline_keyboard for btn in row if btn.url]
    assert "Я оплатил" not in texts
    assert "Написать @owner" in texts
    assert urls == ["https://t.me/owner"]
    assert ("🏠 Меню", NAV_MAIN) in _pairs(markup)
    empty = [btn.url for row in balance_kb("владелец сервиса").inline_keyboard for btn in row if btn.url]
    assert empty == []


def test_wake_reminder_kb_presets_and_off():
    pairs = dict(_pairs(wake_reminder_kb(None)))
    assert pairs["10:00"] == "set:wake:10:00"
    assert pairs["Другое время"] == "set:wake:custom"
    assert "Выключить" not in pairs
    enabled = dict(_pairs(wake_reminder_kb("10:00")))
    assert enabled["✓ 10:00"] == "set:wake:10:00"
    assert enabled["Выключить"] == "set:wake:off"


def test_score_reminder_kb_presets_and_off():
    pairs = dict(_pairs(score_reminder_kb(None)))
    assert pairs["21:00"] == "set:dsr:21:00"
    assert pairs["Другое время"] == "set:dsr:custom"
    assert "Выключить" not in pairs
    enabled = dict(_pairs(score_reminder_kb("21:00")))
    assert enabled["✓ 21:00"] == "set:dsr:21:00"
    assert enabled["Выключить"] == "set:dsr:off"


def test_daily_score_reminder_kb_opens_today():
    pairs = dict(_pairs(daily_score_reminder_kb()))
    assert pairs["🙂 Оценить сегодня"] == "ds:today"


def test_wake_up_reminder_kb_keeps_all_sleep_actions():
    idle = dict(_pairs(wake_up_reminder_kb(None)))
    assert idle["Проснулся"] == "slp:wake"
    assert idle["Проснулся и встал"] == "slp:wakeup"
    assert idle["Встал"] == "slp:up"
    assert idle["Лёг с телефоном"] == "slp:phone"
    awake = SimpleNamespace(phase=lambda: "awake")
    buttons = dict(_pairs(wake_up_reminder_kb(awake)))
    assert buttons["Встал"] == "slp:up"
    assert buttons["Лёг без телефона"] == "slp:nophone"


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
    assert ("Было раньше", "act:later") in pairs
    later = dict(_pairs(activity_duration_kb(ENTRY_ACT, later=True)))
    assert later["☑ Было раньше"] == "act:later"


def test_main_menu_splits_activity_metrics():
    pairs = dict(
        _pairs(main_menu(SimpleNamespace(), False, tracked={"walk", "workout"}, open_activities={"walk"}))
    )
    assert pairs["🚶 Ходьба · идёт"] == "act:o:walk"
    assert pairs["💪 Тренировка"] == "act:o:workout"
    assert "🏃 Активность" not in pairs
    assert "act:o:run" not in pairs.values()


def test_steps_day_and_value_keyboards():
    pairs = dict(_pairs(steps_day_kb(today_steps=8432)))
    assert pairs["Сегодня · 8 432"] == "stp:today"
    assert pairs["Вчера"] == "stp:yest"
    assert pairs["📅 Другая дата"] == "stp:date"
    values = dict(_pairs(steps_value_kb("e:stp")))
    assert values["10 000"] == "stp:q:10000"
    assert values["✖️ Отмена"] == "e:stp"


def test_daily_scores_day_and_value_keyboards():
    from keyboards.main import daily_scores_day_kb, daily_scores_value_kb, score_gaps_kb
    from services.daily_scores import spec_of

    pairs = dict(_pairs(daily_scores_day_kb(today_missing=3, yesterday_missing=1)))
    assert pairs["Сегодня · ещё 3"] == "ds:today"
    assert pairs["Вчера · ещё 1"] == "ds:yest"
    assert pairs["📅 Другая дата"] == "ds:date"
    done = dict(_pairs(daily_scores_day_kb()))
    assert done["Сегодня"] == "ds:today"
    assert done["Вчера"] == "ds:yest"
    specs = [spec_of("mood"), spec_of("energy")]
    markup = daily_scores_value_kb(specs, {"mood": 4})
    mood_row, energy_row = markup.inline_keyboard[:2]
    assert len(mood_row) == len(energy_row) == 7
    assert mood_row[-1].text == energy_row[-1].text == "✖️"
    pairs = _pairs(markup)
    assert ("😊", "noop") in pairs
    assert ("[🙂]", "ds:q:md:4") in pairs
    assert ("🤩", "ds:q:md:5") in pairs
    assert ("⚡", "noop") in pairs
    assert ("😢", "ds:q:md:1") in pairs
    assert ("😢", "ds:q:en:1") in pairs
    assert ("✖️", "ds:x:md") in pairs
    assert ("✖️", "noop") in pairs
    assert ("✖️", "ds:x:en") not in pairs
    from datetime import date

    gaps = score_gaps_kb([(date(2026, 9, 22), ["mood", "energy"])], date(2026, 9, 22))
    gap_pairs = _pairs(gaps)
    assert ("сегодня · 😊⚡", "ds:gap:2026-09-22:0") in gap_pairs
    assert ("😊 ✖️", "ds:sk:2026-09-22:md:0") in gap_pairs
    assert ("⚡ ✖️", "ds:sk:2026-09-22:en:0") in gap_pairs
    assert ("🏠 Меню", "n:m") in gap_pairs


def test_main_menu_custom_metrics_button():
    tracked = {"custom", "steps", "weight", "markers"}
    pairs = _pairs(main_menu(SimpleNamespace(), False, tracked=tracked))
    assert ("📌 Кастом", "n:cm") in pairs
    assert ("🚶 Шаги", "e:stp") in pairs
    assert ("⚖️ Вес", "e:wgt") in pairs
    assert ("🔖 Метки", NAV_MARKERS) in pairs
    assert ("📖 Гайд", NAV_GUIDE) in pairs
    assert all(text != "📌 Показатели" for text, _ in pairs)
    assert all("Настроение" not in text for text, _ in pairs)
    assert all("Самочувствие" not in text for text, _ in pairs)
    assert all("Заметка" not in text for text, _ in pairs)
    assert all("Оценить день" not in text for text, _ in pairs)
    assert all("Оценки дня" not in text for text, _ in pairs)
    scored = {t for t, _ in _pairs(main_menu(SimpleNamespace(), False, tracked={"mood", "energy"}))}
    assert "🙂 Оценки дня" in scored
    assert "Неоценено" not in scored
    assert "Всё оценено" not in scored
    open_pairs = dict(_pairs(main_menu(SimpleNamespace(), False, tracked={"mood"}, open_scores=2)))
    assert open_pairs["Неоценено · 2"] == "ds:gaps"
    done_pairs = dict(_pairs(main_menu(SimpleNamespace(), False, tracked={"mood"}, open_scores=0)))
    assert done_pairs["Всё оценено"] == "ds:gaps"
    empty = {t for t, _ in _pairs(main_menu(SimpleNamespace(), False))}
    assert "📌 Кастом" not in empty
    assert "🚶 Шаги" not in empty
    assert "🚬 Сигарета" not in empty
    assert "😴 Сон" not in empty
    assert "📊 Статистика" in empty
    assert "⚙️ Настройки" in empty


def test_custom_metrics_list_has_quick_add():
    metric = SimpleNamespace(id=3, name="Вода", enabled=1)
    pairs = _pairs(custom_metrics_kb([metric], True))
    assert ("Вода", "cm:o:3") in pairs
    assert ("➕", "cm:add:3") in pairs
    assert "➕ Создать метрику" not in {text for text, _ in pairs}
    assert "➕ Кастомная метрика" not in {text for text, _ in pairs}


def test_custom_metrics_disabled_has_no_quick_add():
    metric = SimpleNamespace(id=3, name="Вода", enabled=0)
    pairs = _pairs(custom_metrics_kb([metric], True))
    assert ("Вода (выкл)", "cm:o:3") in pairs
    assert ("➕", "cm:add:3") not in pairs


def test_metric_types_explain_choice():
    types = dict(_pairs(metric_types_kb()))
    assert types["🔢 Число"] == "cm:t:number"
    assert types["📋 Выбор"] == "cm:t:choice"
    assert types["🕐 Время суток"] == "cm:t:time"
    assert types["▶️ Интервал"] == "cm:t:period"
    assert "📆 Хорошее Решение" not in types
    assert types["✖️ Отмена"] == "set:trk"


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
    assert onset["Сейчас"] == "slo:now"
    assert onset["Сегодня"] == "slo:today"
    assert onset["Вчера"] == "slo:yesterday"
    assert onset["Позавчера"] == "slo:daybefore"
    assert onset["📅 Другая дата"] == "slo:date"
    assert onset["🕐 Указать время"] == "slo:time"
    assert onset["5 мин назад"] == "slo:ago:5"


def test_when_kb_sleep_wake_goes_back_to_wake_kind():
    pairs = dict(_pairs(when_kb("slw")))
    assert pairs["Сейчас"] == "slw:now"
    assert pairs["Сегодня"] == "slw:today"
    assert pairs["Вчера"] == "slw:yesterday"
    assert pairs["Позавчера"] == "slw:daybefore"
    assert pairs["📅 Другая дата"] == "slw:date"
    assert pairs["🕐 Указать время"] == "slw:time"
    assert pairs["⬅️ Назад"] == "slp:wk"


def test_wake_kind_kb_two_options():
    pairs = dict(_pairs(wake_kind_kb("slp:ql")))
    assert pairs["Сам"] == "slk:self"
    assert pairs["Из-за чего-то"] == "slk:other"
    assert pairs["⬅️ Назад"] == "slp:ql"


def test_when_kb_sleep_up_goes_back_to_sleep():
    from utils.callbacks import ENTRY_SLEEP

    pairs = dict(_pairs(when_kb("slu")))
    assert pairs["Сейчас"] == "slu:now"
    assert pairs["Сегодня"] == "slu:today"
    assert pairs["Вчера"] == "slu:yesterday"
    assert pairs["🕐 Указать время"] == "slu:time"
    assert pairs["⬅️ Назад"] == ENTRY_SLEEP


def test_when_kb_sleep_bed_asks_time():
    from utils.callbacks import ENTRY_SLEEP

    phone = dict(_pairs(when_kb("slb")))
    assert phone["Сейчас"] == "slb:now"
    assert phone["Сегодня"] == "slb:today"
    assert phone["Вчера"] == "slb:yesterday"
    assert phone["Позавчера"] == "slb:daybefore"
    assert phone["📅 Другая дата"] == "slb:date"
    assert phone["🕐 Указать время"] == "slb:time"
    assert phone["5 мин назад"] == "slb:ago:5"
    assert phone["⬅️ Назад"] == ENTRY_SLEEP
    nophone = dict(_pairs(when_kb("sln")))
    assert nophone["Сейчас"] == "sln:now"
    assert nophone["Вчера"] == "sln:yesterday"
    assert nophone["🕐 Указать время"] == "sln:time"
    assert nophone["⬅️ Назад"] == ENTRY_SLEEP
    away = dict(_pairs(when_kb("sla")))
    assert away["Сейчас"] == "sla:now"
    assert away["Позавчера"] == "sla:daybefore"
    assert away["🕐 Указать время"] == "sla:time"
    assert away["⬅️ Назад"] == ENTRY_SLEEP


def test_sleep_when_prefixes_map_to_purposes():
    from handlers.time_pick import WHEN_TO_PURPOSE

    assert WHEN_TO_PURPOSE["slw"] == "slp_wake"
    assert WHEN_TO_PURPOSE["slu"] == "slp_up"
    assert WHEN_TO_PURPOSE["slb"] == "slp_bed"
    assert WHEN_TO_PURPOSE["sln"] == "slp_bed"
    assert WHEN_TO_PURPOSE["sla"] == "slp_away"
    assert WHEN_TO_PURPOSE["slo"] == "slp_onset"
    assert WHEN_TO_PURPOSE["cms"] == "cm_start"
    assert WHEN_TO_PURPOSE["cme"] == "cm_end"
    assert WHEN_TO_PURPOSE["wgt"] == "wgt"
    assert WHEN_TO_PURPOSE["mkt"] == "mk"


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


def test_marker_pick_kb_wraps_selected():
    from keyboards.main import marker_pick_kb

    first = SimpleNamespace(
        id=4,
        name="Экзамен",
        occurred_at="2026-05-12T07:00:00+00:00",
        period_role=None,
    )
    second = SimpleNamespace(
        id=5,
        name="Встреча",
        occurred_at="2026-05-12T08:00:00+00:00",
        period_role=None,
    )
    pairs = dict(_pairs(marker_pick_kb([first, second], "mk:js", "UTC", selected_id=4)))
    selected = [text for text, data in pairs.items() if data == "mk:js:4"][0]
    other = [text for text, data in pairs.items() if data == "mk:js:5"][0]
    assert selected.startswith("[") and selected.endswith("]")
    assert not other.startswith("[")


def test_when_kb_marker_goes_back_to_markers():
    from keyboards.main import DATE_WHEN_PREFIXES, SLEEP_WHEN_PREFIXES

    pairs = dict(_pairs(when_kb("mkt")))
    assert pairs["⬅️ Назад"] == NAV_MARKERS
    assert pairs["Сейчас"] == "mkt:now"
    assert pairs["Сегодня"] == "mkt:today"
    assert pairs["Вчера"] == "mkt:yesterday"
    assert pairs["Позавчера"] == "mkt:daybefore"
    assert pairs["📅 Другая дата"] == "mkt:date"
    assert pairs["🕐 Указать время"] == "mkt:time"
    assert "mkt" in DATE_WHEN_PREFIXES
    assert "mkt" not in SLEEP_WHEN_PREFIXES


def test_spam_alert_kb_opens_user_card():
    pairs = _pairs(spam_alert_kb(42))
    assert ("👤 Карточка", "ad:u:42") in pairs
    assert ("🚫 Заблокировать", "ad:bn:42") in pairs


def test_admin_credit_kind_kb_income_or_gift():
    from keyboards.main import admin_credit_kind_kb

    pairs = _pairs(admin_credit_kind_kb(7))
    assert ("💵 Доход", "ad:cri:7") in pairs
    assert ("🎁 Подарок", "ad:crg:7") in pairs
    assert ("✖️ Отмена", "ad:u:7") in pairs
    assert all(data and len(data.encode()) <= 64 for _, data in pairs)


def test_admin_period_kb_has_all_time():
    from keyboards.main import admin_period_kb

    pairs = _pairs(admin_period_kb())
    assert ("Сегодня", "ads:today") in pairs
    assert ("Всё время", "ads:all") in pairs


def test_stats_period_kb_has_all_time():
    from keyboards.main import stats_period_kb

    pairs = _pairs(stats_period_kb())
    assert ("Всё время", "stp:all") in pairs
    assert ("30 дней", "stp:30") in pairs
    assert ("С даты", "stp:since") in pairs
    assert ("С метки", "stp:marker") in pairs
    assert ("📆 Период", "stp:range") in pairs
    assert all(data and len(data.encode()) <= 64 for _, data in pairs)


def test_stats_recent_spans_are_buttons():
    from keyboards.main import charts_done_kb, stats_metrics_kb, stats_period_kb

    recent = [
        ("С даты · 12 августа", "stre:0"),
        ("С метки · Отпуск", "stre:1"),
        ("Период · 1–15 августа", "stre:2"),
    ]
    period = dict(_pairs(stats_period_kb(recent)))
    assert period["С даты · 12 августа"] == "stre:0"
    assert period["Период · 1–15 августа"] == "stre:2"
    metrics = _pairs(stats_metrics_kb({"sleep"}, recent=recent))
    assert metrics[0] == ("С даты · 12 августа", "stre:0")
    assert ("📝 Текст", "stv:text") in metrics
    done = dict(_pairs(charts_done_kb(recent)))
    assert done["С метки · Отпуск"] == "stre:1"
    assert done["Другой период"] == "n:st"
    assert all(data and len(data.encode()) <= 64 for _, data in metrics)


def test_history_period_kb_has_day_counts():
    from keyboards.main import history_period_kb

    pairs = _pairs(history_period_kb())
    assert ("Сегодня", "hist:today") in pairs
    assert ("Вчера", "hist:yesterday") in pairs
    assert ("7 дней", "hist:7") in pairs
    assert ("14 дней", "hist:14") in pairs
    assert ("30 дней", "hist:30") in pairs
    assert ("Сколько дней", "hist:ndays") in pairs
    assert ("📅 Дата", "hist:date") in pairs
    assert ("📆 Период", "hist:range") in pairs
    assert ("Всё время", "hist:all") in pairs
    assert ("С даты", "hist:since") in pairs
    assert ("С метки", "hist:marker") in pairs
    assert all(data and len(data.encode()) <= 64 for _, data in pairs)


def test_since_marker_pick_kb_pages():
    from keyboards.main import since_marker_pick_kb
    from utils.callbacks import NAV_STATS

    items = [
        SimpleNamespace(
            id=n,
            occurred_at="2026-08-10T08:00:00+00:00",
            name=f"Метка {n}",
            period_role=None,
        )
        for n in range(1, 4)
    ]
    pairs = _pairs(
        since_marker_pick_kb(
            items,
            "UTC",
            pick_prefix="stmk",
            page_prefix="stmkp",
            page=1,
            pages=3,
            back=NAV_STATS,
        )
    )
    assert any(data == "stmk:1" for _, data in pairs)
    assert ("«", "stmkp:0") in pairs
    assert ("2/3", "noop") in pairs
    assert ("»", "stmkp:2") in pairs
    assert ("⬅️ Назад", NAV_STATS) in pairs
    assert all(data and len(data.encode()) <= 64 for _, data in pairs)

def test_score_kb_is_one_row():
    from utils.formatting import SCORE_EMOJI

    markup = score_kb("md")
    assert len(markup.inline_keyboard[0]) == 5
    assert [btn.callback_data for btn in markup.inline_keyboard[0]] == [f"md:{n}" for n in range(1, 6)]
    assert [btn.text for btn in markup.inline_keyboard[0]] == [SCORE_EMOJI[n] for n in range(1, 6)]


def test_main_menu_collapses_idle_sleep_and_hides_types():
    from utils.callbacks import ENTRY_SLEEP

    pairs = _pairs(main_menu(SimpleNamespace(), False, tracked={"sleep", "cigarettes"}))
    assert ("😴 Сон", ENTRY_SLEEP) in pairs
    assert "slp:wake" not in {cb for _, cb in pairs}
    selected = _pairs(
        main_menu(SimpleNamespace(), False, tracked={"cigarettes", "sleep"})
    )
    texts = {t for t, _ in selected}
    assert "☕ Кофеин" not in texts
    assert "🍺 Алкоголь" not in texts
    assert "🤌 Валять дурака" not in texts
    assert "🟢 Снюс" not in texts
    assert "🚶 Шаги" not in texts
    assert "⚖️ Вес" not in texts
    assert "🚬 Сигарета" in texts
    assert "😴 Сон" in texts


def test_main_menu_sleep_actions_use_two_rows():
    sleep = SimpleNamespace(phase=lambda: "need_onset")
    rows = [
        [btn.text for btn in row]
        for row in main_menu(
            SimpleNamespace(),
            False,
            sleep,
            tracked={"sleep", "sleep_phone", "sleep_nophone"},
        ).inline_keyboard
    ]
    assert ["Заснул?", "Проснулся"] not in rows
    assert ["Проснулся", "Проснулся и встал"] in rows
    assert ["Встал", "Заснул?"] in rows
    assert ["Лёг с телефоном", "Лёг без телефона"] in rows


def test_sleep_rows_hide_bed_when_metrics_off():
    idle = sleep_rows(None, tracked={"sleep"})
    assert _sleep_callbacks(idle) == ["slp:wake", "slp:wakeup", "slp:up", "slp:askonset"]
    assert "Лёг с телефоном" not in _sleep_texts(idle)
    phone_only = sleep_rows(None, tracked={"sleep", "sleep_phone"})
    assert _sleep_texts(phone_only) == [
        "Проснулся",
        "Проснулся и встал",
        "Встал",
        "Заснул?",
        "Лёг с телефоном",
        "Убрал телефон",
    ]
    open_phone = sleep_rows(SimpleNamespace(phase=lambda: "with_phone"), tracked={"sleep"})
    assert "slp:away" in _sleep_callbacks(open_phone)
    assert "Лёг с телефоном" not in _sleep_texts(open_phone)
    awake = sleep_rows(SimpleNamespace(phase=lambda: "awake"), tracked={"sleep", "sleep_phone", "sleep_nophone"})
    assert "slp:phone" in _sleep_callbacks(awake)
    assert "slp:nophone" in _sleep_callbacks(awake)
    assert "slp:up" in _sleep_callbacks(awake)


def test_main_menu_keeps_open_sleep_without_tracking():
    sleep = SimpleNamespace(phase=lambda: "need_onset")
    pairs = _pairs(main_menu(SimpleNamespace(), False, sleep))
    assert "slp:askonset" in {cb for _, cb in pairs}
    assert "🚬 Сигарета" not in {t for t, _ in pairs}


def test_main_menu_shows_pinned_metric():
    metric = SimpleNamespace(id=9, name="Вода")
    pairs = _pairs(main_menu(SimpleNamespace(), False, tracked={"custom"}, pinned=[metric]))
    assert ("Вода", "cm:o:9") in pairs
    assert ("➕", "cm:add:9") in pairs
    hidden_pins = _pairs(main_menu(SimpleNamespace(), False, pinned=[metric]))
    assert ("Вода", "cm:o:9") not in hidden_pins


def test_period_metric_uses_start_end_buttons():
    from keyboards.main import metric_card_kb, metric_delete_kb

    metric = SimpleNamespace(id=4, name="Ванная", enabled=1, data_type="period")
    pairs = _pairs(custom_metrics_kb([metric], True, open_ids={4}))
    assert ("Ванная · идёт", "cm:o:4") in pairs
    assert ("▶️", "cm:st:4") in pairs
    assert ("⏹", "cm:en:4") in pairs
    assert ("➕", "cm:add:4") not in pairs
    idle = dict(_pairs(metric_card_kb(4, True, True, data_type="period")))
    assert idle["▶️ Начал"] == "cm:st:4"
    assert idle["⏹ Закончил"] == "cm:en:4"
    assert idle["🗑 Удалить"] == "cm:del:4"
    locked = dict(_pairs(metric_card_kb(4, True, False, data_type="period")))
    assert "🗑 Удалить" not in locked
    confirm = dict(_pairs(metric_delete_kb(4)))
    assert confirm["Да, удалить"] == "cm:delok:4"
    assert confirm["Отмена"] == "cm:o:4"
    running = dict(_pairs(metric_card_kb(4, True, True, data_type="period", has_open=True)))
    assert "▶️ Начал" not in running
    assert running["⏹ Закончил"] == "cm:en:4"


def test_pledge_metric_uses_next_date_button():
    from datetime import date

    from keyboards.main import metric_card_kb, pledge_weekdays_kb

    metric = SimpleNamespace(id=8, name="Чтение", enabled=1, data_type="pledge")
    nxt = date(2026, 9, 21)
    pairs = _pairs(custom_metrics_kb([metric], True, pledge_next={8: nxt}))
    assert ("Чтение", "cm:o:8") in pairs
    assert ("21 сентября", "cm:pl:8") in pairs
    assert ("➕", "cm:add:8") not in pairs
    caught_up = _pairs(custom_metrics_kb([metric], True, pledge_next={}))
    assert ("21 сентября", "cm:pl:8") not in caught_up
    card = dict(
        _pairs(metric_card_kb(8, True, True, data_type="pledge", pledge_next=nxt, pledge_open=2, pledge_undo=date(2026, 9, 20)))
    )
    assert card["Закрыть 21 сентября"] == "cm:pn:8"
    assert card["Закрыть отставание · 2"] == "cm:pa:8"
    assert card["Снять 20 сентября"] == "cm:pu:8"
    days = dict(_pairs(pledge_weekdays_kb(127)))
    assert days["✅ Пн"] == "cm:wd:0"
    assert days["Готово"] == "cm:wd:ok"
    assert days["Каждый день"] == "cm:wd:all"


def test_main_menu_shows_good_decisions_button():
    from datetime import date

    metric = SimpleNamespace(id=9, name="Чтение", data_type="pledge")
    pairs = _pairs(
        main_menu(
            SimpleNamespace(),
            False,
            tracked={"pledges"},
            pledge_pinned=[metric],
            pledge_next={9: date(2026, 9, 22)},
        )
    )
    assert ("📆 Хорошие решения", "n:pl") in pairs
    assert ("Чтение", "cm:o:9") in pairs
    assert ("22 сентября", "cm:pq:9") in pairs
    hidden = _pairs(main_menu(SimpleNamespace(), False, tracked={"custom"}))
    assert ("📆 Хорошие решения", "n:pl") not in hidden


def test_main_menu_shows_pinned_period_metric():
    metric = SimpleNamespace(id=9, name="Ванная", data_type="period")
    pairs = _pairs(
        main_menu(
            SimpleNamespace(), False, tracked={"custom"}, pinned=[metric], open_metric_ids={9}
        )
    )
    assert ("Ванная · идёт", "cm:o:9") in pairs
    assert ("▶️", "cm:st:9") in pairs
    assert ("⏹", "cm:en:9") in pairs


def test_entry_actions_repeat_and_history_back():
    from keyboards.main import confirm_remove_kb, entry_actions

    cig = dict(_pairs(entry_actions("cig", 4, True, undo=True)))
    assert "Ещё одну" not in cig
    assert cig["🏠 Меню"] == NAV_MAIN
    caf = dict(_pairs(entry_actions("caf", 2, True, undo=True)))
    assert "Как тогда" not in caf
    fool = dict(_pairs(entry_actions("fool", 3, True, undo=True)))
    assert "Ещё одну" not in fool
    alc = dict(_pairs(entry_actions("alc", 1, True, undo=True)))
    assert "Как тогда" not in alc
    hist = dict(_pairs(entry_actions("cig", 4, True, from_history=True)))
    assert hist["⬅️ Назад"] == "h:back"
    assert hist["🗑 Удалить"] == "rm:cig:4"
    with_cursor = dict(
        _pairs(entry_actions("cig", 4, True, from_history=True, hist="2026-08-08:2026-08-15:0"))
    )
    assert with_cursor["⬅️ Назад"] == "h:back:2026-08-08:2026-08-15:0"
    assert with_cursor["🗑 Удалить"] == "rm:cig:4:2026-08-08:2026-08-15:0"
    confirm = dict(_pairs(confirm_remove_kb("cig", 4, hist="2026-08-08:2026-08-15:0")))
    assert confirm["Удалить"] == "rmok:cig:4:2026-08-08:2026-08-15:0"
    assert confirm["Отмена"] == "h:o:cig:4:2026-08-08:2026-08-15:0"
    steps = dict(_pairs(entry_actions("stp", 8, True, undo=True)))
    assert steps["✏️ Изменить"] == "stp:e:8"
    assert steps["🗑 Отменить"] == "un:stp:8"
    scores = dict(_pairs(entry_actions("dsc", 4, True, undo=True)))
    assert scores["✏️ Изменить"] == "ds:e:4"
    assert scores["🗑 Отменить"] == "un:dsc:4"


def test_history_day_kb_paginates_and_neighbors():
    from datetime import date
    from keyboards.main import history_day_kb

    today = date(2026, 8, 23)
    markup = history_day_kb(
        [("12:00 🚬 Сигарета", "h:o:cig:1")],
        page=1,
        pages=3,
        view_start=today,
        view_end=today,
        today=today,
    )
    pairs = dict(_pairs(markup))
    assert pairs["‹ вчера"] == "h:d:2026-08-22"
    assert pairs["сегодня"] == "noop"
    assert "завтра ›" not in pairs
    assert pairs["«"] == "h:p:0"
    assert pairs["2/3"] == "noop"
    assert pairs["»"] == "h:p:2"
    yesterday = history_day_kb(
        [("12:00 🚬 Сигарета", "h:o:cig:1")],
        page=0,
        pages=1,
        view_start=date(2026, 8, 22),
        view_end=date(2026, 8, 22),
        today=today,
    )
    ypairs = dict(_pairs(yesterday))
    assert ypairs["‹ 21.08"] == "h:d:2026-08-21"
    assert ypairs["22.08"] == "noop"
    assert ypairs["завтра ›"] == "h:d:2026-08-23"
    period = history_day_kb(
        [("😴 Сон 8–15 августа", "h:o:slp:1")],
        page=0,
        pages=1,
        view_start=date(2026, 8, 8),
        view_end=date(2026, 8, 15),
        today=today,
    )
    ppairs = dict(_pairs(period))
    assert not any("‹" in text or "›" in text for text in ppairs)


def test_track_metrics_kb_toggles_like_stats():
    pairs = dict(_pairs(track_metrics_kb({"cigarettes", "sleep"})))
    assert pairs["☑ 🚬 Сигареты"] == "set:trk:cigarettes"
    assert pairs["☑ 😴 Сон"] == "set:trk:sleep"
    assert pairs["☐ 🚶 Шаги"] == "set:trk:steps"
    assert pairs["☐ 📌 Кастом"] == "set:trk:custom"
    assert pairs["➕ Кастомная метрика"] == "cm:new"
    assert {cb for cb in pairs.values() if cb.startswith("set:trk:")} == {
        f"set:trk:{key}" for key in TRACKABLE_TYPES
    }
    empty = dict(_pairs(track_metrics_kb(set())))
    assert empty["➕ Кастомная метрика"] == "cm:new"
    assert all(
        text.startswith("☐ ")
        for text in empty
        if text not in {"⬅️ Назад", "🏠 Меню", "➕ Кастомная метрика"}
    )


def test_stats_metrics_kb_includes_custom():
    metric = SimpleNamespace(id=5, name="Вода")
    pairs = dict(_pairs(stats_metrics_kb({"cigarettes", "m5"}, [metric])))
    assert "☑ 🚬 Сигареты" in pairs
    assert pairs["☑ Вода"] == "stm:m5"
    all_pairs = dict(_pairs(stats_metrics_kb(set(), [])))
    assert "☐ 🚶 Шаги" in all_pairs
    assert all_pairs["☐ 🚶 Шаги"] == "stm:steps"
    assert all_pairs["☐ ⚖️ Вес"] == "stm:weight"
    assert all_pairs["☐ 💚 Самочувствие"] == "stm:wellbeing"
    assert all_pairs["☐ 🌟 Оценка дня"] == "stm:day_rating"
    assert all_pairs["☐ 😰 Стресс"] == "stm:stress"


def test_stats_metrics_kb_hides_metrics_without_data():
    metric = SimpleNamespace(id=5, name="Вода")
    quiet = SimpleNamespace(id=9, name="Пустая")
    pairs = dict(
        _pairs(
            stats_metrics_kb(
                {"sleep"},
                [metric, quiet],
                only={"sleep", "steps", "m5"},
            )
        )
    )
    assert pairs["☑ 😴 Сон"] == "stm:sleep"
    assert pairs["☐ 🚶 Шаги"] == "stm:steps"
    assert pairs["☐ Вода"] == "stm:m5"
    assert "stm:cigarettes" not in pairs.values()
    assert "stm:weight" not in pairs.values()
    assert "stm:m9" not in pairs.values()
    assert pairs["📝 Текст"] == "stv:text"


def test_followup_keyboards():
    from keyboards.main import charts_done_kb, how_to_kb
    from utils.callbacks import NAV_STATS

    how_to = dict(_pairs(how_to_kb()))
    assert how_to["Понятно"] == "onb:ok"
    assert how_to["📖 Подробный гайд"] == NAV_GUIDE
    done = dict(_pairs(charts_done_kb()))
    assert done["Другой период"] == NAV_STATS
    assert done["🏠 Меню"] == NAV_MAIN

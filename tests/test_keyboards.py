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
    legal_consent_kb,
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
    settings_kb,
    skip_comment_kb,
    sleep_rows,
    spam_alert_kb,
    stats_metrics_kb,
    steps_day_kb,
    steps_value_kb,
    timezone_kb,
    track_metrics_kb,
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


def test_sleep_row_changes_with_phase():
    idle = sleep_rows(None)
    assert [[btn.callback_data for btn in row] for row in idle] == [
        ["slp:wake"],
        ["slp:phone", "slp:nophone"],
    ]
    assert _sleep_texts(idle) == ["Проснулся", "Лёг с телефоном", "Лёг без телефона"]
    with_phone = SimpleNamespace(
        phase=lambda: "with_phone",
    )
    phone_rows = sleep_rows(with_phone)
    assert _sleep_callbacks(phone_rows) == [
        "slp:wake",
        "slp:wakeup",
        "slp:away",
    ]
    assert _sleep_texts(phone_rows) == ["Проснулся", "И встал", "Убрал телефон"]
    awake = SimpleNamespace(phase=lambda: "awake", sleep_onset_at=None)
    assert _sleep_callbacks(sleep_rows(awake)) == [
        "slp:askonset",
        "slp:up",
        "slp:phone",
        "slp:nophone",
    ]
    awake_done = SimpleNamespace(phase=lambda: "awake", sleep_onset_at="2026-08-16T21:00:00+00:00")
    assert _sleep_callbacks(sleep_rows(awake_done)) == [
        "slp:up",
        "slp:phone",
        "slp:nophone",
    ]
    need_onset = SimpleNamespace(phase=lambda: "need_onset")
    onset_rows = sleep_rows(need_onset)
    assert [[btn.callback_data for btn in row] for row in onset_rows] == [
        ["slp:askonset", "slp:wake"],
        ["slp:phone", "slp:nophone"],
    ]
    assert _sleep_texts(onset_rows) == [
        "Заснул?",
        "Проснулся",
        "Лёг с телефоном",
        "Лёг без телефона",
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
    assert settings["⬅️ Назад"] == NAV_SETTINGS
    assert settings["🏠 Меню"] == NAV_MAIN
    assert "✅ Принимаю" not in settings


def test_settings_kb_includes_legal_docs():
    user = SimpleNamespace(timezone="Europe/Moscow", default_sleep_time="23:00")
    pairs = _pairs(settings_kb(user))
    assert ("📋 Метрики", "set:trk") in pairs
    assert ("📄 Политика конфиденциальности", "lg:p:0:s") in pairs
    assert ("📜 Пользовательское соглашение", "lg:t:0:s") in pairs
    assert ("🗑 Удалить аккаунт", "set:del") in pairs
    assert all(text != "📋 Кнопки меню" for text, _ in pairs)


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


def test_steps_day_and_value_keyboards():
    pairs = dict(_pairs(steps_day_kb(today_steps=8432)))
    assert pairs["Сегодня · 8 432"] == "stp:today"
    assert pairs["Вчера"] == "stp:yest"
    assert pairs["📅 Другая дата"] == "stp:date"
    values = dict(_pairs(steps_value_kb("e:stp")))
    assert values["10 000"] == "stp:q:10000"
    assert values["✖️ Отмена"] == "e:stp"


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
    assert ("➕ Создать метрику", "cm:new") in pairs


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
    assert onset["🕐 Указать время"] == "slo:time"
    assert onset["5 мин назад"] == "slo:ago:5"


def test_when_kb_sleep_wake_goes_back_to_quality():
    pairs = dict(_pairs(when_kb("slw")))
    assert pairs["Сейчас"] == "slw:now"
    assert pairs["🕐 Указать время"] == "slw:time"
    assert pairs["⬅️ Назад"] == "slp:ql"


def test_when_kb_sleep_up_goes_back_to_sleep():
    from utils.callbacks import ENTRY_SLEEP

    pairs = dict(_pairs(when_kb("slu")))
    assert pairs["Сейчас"] == "slu:now"
    assert pairs["🕐 Указать время"] == "slu:time"
    assert pairs["⬅️ Назад"] == ENTRY_SLEEP


def test_sleep_when_prefixes_map_to_purposes():
    from handlers.time_pick import WHEN_TO_PURPOSE

    assert WHEN_TO_PURPOSE["slw"] == "slp_wake"
    assert WHEN_TO_PURPOSE["slu"] == "slp_up"
    assert WHEN_TO_PURPOSE["slo"] == "slp_onset"
    assert WHEN_TO_PURPOSE["cms"] == "cm_start"
    assert WHEN_TO_PURPOSE["cme"] == "cm_end"
    assert WHEN_TO_PURPOSE["wgt"] == "wgt"


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
        for row in main_menu(SimpleNamespace(), False, sleep, tracked={"sleep"}).inline_keyboard
    ]
    assert ["Заснул?", "Проснулся"] in rows
    assert ["Лёг с телефоном", "Лёг без телефона"] in rows


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
    from keyboards.main import metric_card_kb

    metric = SimpleNamespace(id=4, name="Ванная", enabled=1, data_type="period")
    pairs = _pairs(custom_metrics_kb([metric], True, open_ids={4}))
    assert ("Ванная · идёт", "cm:o:4") in pairs
    assert ("▶️", "cm:st:4") in pairs
    assert ("⏹", "cm:en:4") in pairs
    assert ("➕", "cm:add:4") not in pairs
    idle = dict(_pairs(metric_card_kb(4, True, True, data_type="period")))
    assert idle["▶️ Начал"] == "cm:st:4"
    assert idle["⏹ Закончил"] == "cm:en:4"
    running = dict(_pairs(metric_card_kb(4, True, True, data_type="period", has_open=True)))
    assert "▶️ Начал" not in running
    assert running["⏹ Закончил"] == "cm:en:4"


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
    from keyboards.main import entry_actions

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
    steps = dict(_pairs(entry_actions("stp", 8, True, undo=True)))
    assert steps["✏️ Изменить"] == "stp:e:8"
    assert steps["🗑 Отменить"] == "un:stp:8"


def test_history_day_kb_paginates_and_neighbors():
    from datetime import date
    from keyboards.main import history_day_kb

    today = date(2026, 8, 23)
    markup = history_day_kb(
        [("🚬 12:00", "h:o:cig:1")],
        page=1,
        pages=3,
        day=today,
        period_start=date(2026, 8, 22),
        period_end=date(2026, 8, 24),
        today=today,
    )
    pairs = dict(_pairs(markup))
    assert pairs["‹ вчера"] == "h:d:2026-08-22"
    assert pairs["сегодня"] == "noop"
    assert pairs["завтра ›"] == "h:d:2026-08-24"
    assert pairs["«"] == "h:p:0"
    assert pairs["2/3"] == "noop"
    assert pairs["»"] == "h:p:2"


def test_track_metrics_kb_toggles_like_stats():
    pairs = dict(_pairs(track_metrics_kb({"cigarettes", "sleep"})))
    assert pairs["☑ 🚬 Сигареты"] == "set:trk:cigarettes"
    assert pairs["☑ 😴 Сон"] == "set:trk:sleep"
    assert pairs["☐ 🚶 Шаги"] == "set:trk:steps"
    assert pairs["☐ 📌 Кастом"] == "set:trk:custom"
    assert {cb for cb in pairs.values() if cb.startswith("set:trk:")} == {
        f"set:trk:{key}" for key in TRACKABLE_TYPES
    }
    empty = dict(_pairs(track_metrics_kb(set())))
    assert all(text.startswith("☐ ") for text in empty if text not in {"⬅️ Назад", "🏠 Меню"})


def test_stats_metrics_kb_includes_custom():
    metric = SimpleNamespace(id=5, name="Вода")
    pairs = dict(_pairs(stats_metrics_kb({"cigarettes", "m5"}, [metric])))
    assert "☑ 🚬 Сигареты" in pairs
    assert pairs["☑ Вода"] == "stm:m5"
    all_pairs = dict(_pairs(stats_metrics_kb(set(), [])))
    assert "☐ 🚶 Шаги" in all_pairs
    assert all_pairs["☐ 🚶 Шаги"] == "stm:steps"
    assert all_pairs["☐ ⚖️ Вес"] == "stm:weight"


def test_followup_keyboards():
    from keyboards.main import charts_done_kb, how_to_kb
    from utils.callbacks import NAV_STATS

    how_to = dict(_pairs(how_to_kb()))
    assert how_to["Понятно"] == "onb:ok"
    assert how_to["📖 Подробный гайд"] == NAV_GUIDE
    done = dict(_pairs(charts_done_kb()))
    assert done["Другой период"] == NAV_STATS
    assert done["🏠 Меню"] == NAV_MAIN

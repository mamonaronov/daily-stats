"""Inline keyboards."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import SleepRecord, User
from services.metric_types import METRIC_TEMPLATES, METRIC_TYPES, UNIT_PRESETS
from utils.callbacks import (
    ENTRY_ACT,
    ENTRY_ALC,
    ENTRY_CAF,
    ENTRY_CIG,
    ENTRY_FOOL,
    ENTRY_SLEEP,
    ENTRY_SNUS,
    ENTRY_STP,
    ENTRY_WGT,
    NAV_ADMIN,
    NAV_BACK,
    NAV_BALANCE,
    NAV_GUIDE,
    NAV_HISTORY,
    NAV_MAIN,
    NAV_METRICS,
    NAV_MARKERS,
    NAV_SETTINGS,
    NAV_STATS,
)
from utils.formatting import SCORE_EMOJI, SCORE_LABELS, format_int_spaces, format_kg, truncate
from utils.time import COMMON_TIMEZONES, MONTHS_RU, WEEKDAYS_RU, format_date, format_dt, parse_iso


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def nav_row(back: str | None = None, *, menu: bool = True) -> list[InlineKeyboardButton]:
    row = []
    if back and back != NAV_MAIN:
        row.append(_btn("⬅️ Назад", back))
    if menu:
        row.append(_btn("🏠 Меню", NAV_MAIN))
    return row


def with_nav(builder: InlineKeyboardBuilder, back: str | None = None) -> InlineKeyboardMarkup:
    builder.row(*nav_row(back))
    return builder.as_markup()


def back_kb(back: str | None = None, *, menu: bool = True) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    row = nav_row(back, menu=menu)
    if row:
        b.row(*row)
    return b.as_markup()


def sleep_rows(sleep: SleepRecord | None) -> list[list[InlineKeyboardButton]]:
    phase = sleep.phase() if sleep else "idle"
    wake = _btn("Проснулся", "slp:wake")
    phone = _btn("Лёг с телефоном", "slp:phone")
    nophone = _btn("Лёг без телефона", "slp:nophone")
    bed = [phone, nophone]
    if phase == "with_phone":
        return [
            [wake, _btn("И встал", "slp:wakeup")],
            [_btn("Убрал телефон", "slp:away")],
        ]
    if phase == "no_phone":
        return [[wake, _btn("И встал", "slp:wakeup")]]
    if phase == "awake":
        top = [_btn("Встал", "slp:up")]
        if sleep is not None and sleep.sleep_onset_at is None:
            top.insert(0, _btn("Заснул?", "slp:askonset"))
        return [top, bed]
    if phase == "need_onset":
        return [[_btn("Заснул?", "slp:askonset"), wake], bed]
    return [[wake], bed]


def _add_sleep_rows(builder: InlineKeyboardBuilder, sleep: SleepRecord | None) -> None:
    for row in sleep_rows(sleep):
        builder.row(*row)


def sleep_actions_kb(sleep: SleepRecord | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _add_sleep_rows(b, sleep)
    return with_nav(b)


def main_menu(
    user: User,
    is_owner: bool,
    sleep: SleepRecord | None = None,
    *,
    hidden: set[str] | None = None,
    pinned: list | None = None,
    open_metric_ids: set[int] | None = None,
) -> InlineKeyboardMarkup:
    hidden = hidden or set()
    b = InlineKeyboardBuilder()
    cig = _btn("🚬 Сигарета", ENTRY_CIG)
    if "snus" in hidden:
        b.row(cig)
    else:
        b.row(cig, _btn("🟢 Снюс", ENTRY_SNUS))
    if "fooling" not in hidden:
        b.row(_btn("🤌 Валять дурака", ENTRY_FOOL))
    phase = sleep.phase() if sleep else "idle"
    if phase == "idle":
        b.row(_btn("😴 Сон", ENTRY_SLEEP))
    else:
        _add_sleep_rows(b, sleep)
    drinks: list[InlineKeyboardButton] = []
    if "caffeine" not in hidden:
        drinks.append(_btn("☕ Кофеин", ENTRY_CAF))
    if "alcohol" not in hidden:
        drinks.append(_btn("🍺 Алкоголь", ENTRY_ALC))
    if drinks:
        b.row(*drinks)
    extras: list[InlineKeyboardButton] = []
    if "activity" not in hidden:
        extras.append(_btn("🏃 Активность", ENTRY_ACT))
    if "steps" not in hidden:
        extras.append(_btn("🚶 Шаги", ENTRY_STP))
    if "weight" not in hidden:
        extras.append(_btn("⚖️ Вес", ENTRY_WGT))
    if "custom" not in hidden:
        extras.append(_btn("📌 Кастом", NAV_METRICS))
    for i in range(0, len(extras), 2):
        b.row(*extras[i : i + 2])
    if "markers" not in hidden:
        b.row(_btn("🔖 Метки", NAV_MARKERS))
    for metric in (pinned or [])[:3]:
        b.row(*_metric_quick_row(metric, open_metric_ids))
    b.row(_btn("📊 Статистика", NAV_STATS), _btn("📅 История", NAV_HISTORY))
    b.row(_btn("⚙️ Настройки", NAV_SETTINGS), _btn("💰 Баланс", NAV_BALANCE))
    b.row(_btn("📖 Гайд", NAV_GUIDE))
    if is_owner:
        b.row(_btn("🛠 Админ-панель", NAV_ADMIN))
    return b.as_markup()


def cancel_kb(back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if back and back != NAV_MAIN:
        b.row(_btn("✖️ Отмена", back), _btn("🏠 Меню", NAV_MAIN))
    else:
        b.row(_btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


_WHEN_TITLES = {
    "cig": "🚬 Сигарета",
    "fool": "🤌 Валять дурака",
    "caft": "Когда это было?",
    "alct": "Когда это было?",
    "actt": "Когда была активность?",
    "wgt": "Когда взвесились?",
    "slw": "Когда проснулись?",
    "slu": "Когда встали?",
    "slo": "Когда заснули?",
    "cmt": "Когда зафиксировать?",
    "cms": "Когда начали?",
    "cme": "Когда закончили?",
    "mkt": "Когда поставить метку?",
}

_WHEN_BACK = {
    "cig": None,
    "fool": None,
    "caft": ENTRY_CAF,
    "alct": ENTRY_ALC,
    "actt": ENTRY_ACT,
    "wgt": ENTRY_WGT,
    "slw": "slp:ql",
    "slu": ENTRY_SLEEP,
    "mkt": NAV_MARKERS,
}


def when_title(prefix: str) -> str:
    return _WHEN_TITLES.get(prefix, "Когда это было?")


def when_kb(prefix: str, *, metric_id: int | None = None) -> InlineKeyboardMarkup:
    back = _WHEN_BACK.get(prefix)
    if prefix in {"cmt", "cms", "cme"} and metric_id is not None:
        back = f"cm:o:{metric_id}"
    return now_or_time(prefix, back)


def _relative_when_rows(builder: InlineKeyboardBuilder, prefix: str) -> None:
    builder.row(_btn("5 мин назад", f"{prefix}:ago:5"), _btn("10 мин назад", f"{prefix}:ago:10"))
    builder.row(_btn("15 мин назад", f"{prefix}:ago:15"), _btn("30 мин назад", f"{prefix}:ago:30"))
    builder.row(_btn("⏱ Сколько назад", f"{prefix}:agoask"), _btn("⌨️ Ввести текстом", f"{prefix}:txt"))


def now_or_time(prefix: str, back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сейчас", f"{prefix}:now"), _btn("🕐 Указать время", f"{prefix}:time"))
    _relative_when_rows(b, prefix)
    return with_nav(b, back)


def ago_pick_kb(prefix: str, back: str | None = NAV_BACK) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        _btn("1 мин", f"{prefix}:ago:1"),
        _btn("2 мин", f"{prefix}:ago:2"),
        _btn("3 мин", f"{prefix}:ago:3"),
        _btn("5 мин", f"{prefix}:ago:5"),
    )
    b.row(
        _btn("10 мин", f"{prefix}:ago:10"),
        _btn("15 мин", f"{prefix}:ago:15"),
        _btn("20 мин", f"{prefix}:ago:20"),
        _btn("30 мин", f"{prefix}:ago:30"),
    )
    b.row(
        _btn("45 мин", f"{prefix}:ago:45"),
        _btn("1 ч", f"{prefix}:ago:60"),
        _btn("1.5 ч", f"{prefix}:ago:90"),
        _btn("2 ч", f"{prefix}:ago:120"),
    )
    b.row(_btn("⌨️ Ввести число", f"{prefix}:agon"))
    return with_nav(b, back)


def sleep_onset_kb(undo_kind: str | None = None, undo_id: int | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    later = f"slp:later:{undo_kind}:{undo_id}" if undo_kind and undo_id is not None else "slp:later"
    b.row(_btn("Сейчас", "slo:now"), _btn("🕐 Указать время", "slo:time"))
    _relative_when_rows(b, "slo")
    b.row(_btn("Позже", later))
    if undo_kind and undo_id is not None:
        b.row(_btn("🗑 Отменить", f"un:{undo_kind}:{undo_id}"))
    return with_nav(b)


def snus_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🛒 Купил сейчас", "sns:buy"), _btn("🕐 Купил ранее", "sns:tbuy"))
    b.row(_btn("✅ Закончилась сейчас", "sns:end"), _btn("🕐 Закончилась ранее", "sns:tend"))
    return with_nav(b)


def score_kb(prefix: str, back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(*[_btn(SCORE_EMOJI[score], f"{prefix}:{score}") for score in range(1, 6)])
    b.row(*nav_row(back))
    return b.as_markup()


def caffeine_types() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("☕ Кофе", "caf:t:coffee"), _btn("⚡ Энергетик", "caf:t:energy"))
    b.row(_btn("🍵 Чай", "caf:t:tea"), _btn("Другое", "caf:t:other"))
    return with_nav(b)


def alcohol_types() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🍺 Пиво", "alc:t:beer"), _btn("🍷 Вино", "alc:t:wine"))
    b.row(_btn("🥃 Крепкий", "alc:t:spirits"), _btn("🍹 Коктейль", "alc:t:cocktail"))
    b.row(_btn("Другое", "alc:t:other"))
    return with_nav(b)


ALCOHOL_AMOUNT_PRESETS = {
    "beer": ((330, "330 мл"), (500, "0,5 л"), (1000, "1 л"), (1500, "1,5 л")),
    "wine": ((100, "100 мл"), (150, "150 мл"), (200, "200 мл"), (750, "0,75 л")),
    "spirits": ((40, "40 мл"), (50, "50 мл"), (100, "100 мл"), (500, "0,5 л")),
    "cocktail": ((150, "150 мл"), (250, "250 мл"), (350, "350 мл"), (500, "0,5 л")),
    "other": ((50, "50 мл"), (200, "200 мл"), (330, "330 мл"), (500, "0,5 л")),
}

CAFFEINE_AMOUNT_PRESETS = {
    "coffee": ((200, "200 мл"), (250, "250 мл"), (350, "350 мл"), (400, "400 мл")),
    "energy": ((250, "250 мл"), (330, "330 мл"), (450, "450 мл"), (500, "0,5 л")),
    "tea": ((200, "200 мл"), (250, "250 мл"), (400, "400 мл"), (500, "0,5 л")),
    "other": ((200, "200 мл"), (250, "250 мл"), (330, "330 мл"), (500, "0,5 л")),
}


def drink_amount_kb(kind: str, drink_type: str, back: str) -> InlineKeyboardMarkup:
    presets = ALCOHOL_AMOUNT_PRESETS if kind == "alc" else CAFFEINE_AMOUNT_PRESETS
    prefix = "alc:q" if kind == "alc" else "caf:q"
    b = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for milliliters, label in presets.get(drink_type, presets["other"]):
        row.append(_btn(label, f"{prefix}:{milliliters}"))
        if len(row) == 2:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    if kind == "alc":
        b.row(_btn("1 порция", f"{prefix}:pcs:1"))
    elif drink_type in {"coffee", "tea"}:
        b.row(_btn("1 чашка", f"{prefix}:pcs:1"))
    return with_nav(b, back)


def drink_recent_kb(
    kind: str,
    drink_type: str,
    recents: list[tuple[float, str]],
    back: str,
) -> InlineKeyboardMarkup:
    prefix = "caf:q" if kind == "caf" else "alc:q"
    b = InlineKeyboardBuilder()
    for amount, unit in recents:
        if unit in {"шт", "pcs"}:
            b.row(_btn(f"{amount:g} шт", f"{prefix}:pcs:{int(amount)}"))
        else:
            token = str(int(amount)) if amount == int(amount) else f"{amount:g}"
            b.row(_btn(f"{amount:g} {unit}", f"{prefix}:{token}"))
    other = "caf:x:" if kind == "caf" else "alc:x:"
    b.row(_btn("Другое", f"{other}{drink_type}"))
    return with_nav(b, back)


def activity_duration_kb(back: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("15 мин", "act:d:15"), _btn("30 мин", "act:d:30"), _btn("45 мин", "act:d:45"))
    b.row(_btn("1 ч", "act:d:60"), _btn("1,5 ч", "act:d:90"), _btn("2 ч", "act:d:120"))
    return with_nav(b, back)


def activity_types() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🚶 Ходьба", "act:t:walk"), _btn("🏃 Бег", "act:t:run"))
    b.row(_btn("💪 Тренировка", "act:t:workout"), _btn("🚴 Велосипед", "act:t:bike"))
    b.row(_btn("Другое", "act:t:other"))
    return with_nav(b)


_STEPS_PRESETS = (3000, 5000, 8000, 10000, 12000, 15000)


def steps_day_kb(*, today_steps: int | None = None, yesterday_steps: int | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    today_label = "Сегодня" if today_steps is None else f"Сегодня · {format_int_spaces(today_steps)}"
    yesterday_label = "Вчера" if yesterday_steps is None else f"Вчера · {format_int_spaces(yesterday_steps)}"
    b.row(_btn(today_label, "stp:today"), _btn(yesterday_label, "stp:yest"))
    b.row(_btn("📅 Другая дата", "stp:date"))
    return with_nav(b)


def steps_value_kb(back: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for amount in _STEPS_PRESETS:
        row.append(_btn(format_int_spaces(amount), f"stp:q:{amount}"))
        if len(row) == 2:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    b.row(_btn("✖️ Отмена", back), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def weight_value_kb(recent: list[float] | None = None, back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for kg in recent or []:
        token = f"{kg:g}"
        b.row(_btn(format_kg(kg), f"wgt:q:{token}"))
    if back and back != NAV_MAIN:
        b.row(_btn("✖️ Отмена", back), _btn("🏠 Меню", NAV_MAIN))
    else:
        b.row(*nav_row())
    return b.as_markup()


def timezone_kb(back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for tz, label in COMMON_TIMEZONES:
        b.row(_btn(label, f"tz:{tz}"))
    b.row(_btn("Другой (IANA)", "tz:custom"))
    if back:
        b.row(*nav_row(back))
    return b.as_markup()


def calendar_kb(year: int, month: int, prefix: str = "cal", back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(f"{MONTHS_RU[month].capitalize()} {year}", "noop"))
    b.row(*[_btn(d, "noop") for d in WEEKDAYS_RU])
    first = date(year, month, 1)
    start_pad = first.weekday()
    days_in_month = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    row: list[InlineKeyboardButton] = [_btn(" ", "noop") for _ in range(start_pad)]
    for day in range(1, days_in_month + 1):
        current = date(year, month, day)
        row.append(_btn(str(day), f"{prefix}:{current.isoformat()}"))
        if len(row) == 7:
            b.row(*row)
            row = []
    if row:
        while len(row) < 7:
            row.append(_btn(" ", "noop"))
        b.row(*row)
    prev_month = date(year, month, 1) - timedelta(days=1)
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    b.row(
        _btn("«", f"{prefix}m:{prev_month.year:04d}-{prev_month.month:02d}"),
        _btn("»", f"{prefix}m:{next_month.year:04d}-{next_month.month:02d}"),
    )
    b.row(
        _btn("Сегодня", f"{prefix}:today"),
        _btn("Вчера", f"{prefix}:yesterday"),
        _btn("Позавчера", f"{prefix}:daybefore"),
    )
    b.row(*nav_row(back))
    return b.as_markup()


def hours_kb(prefix: str = "hr", *, date_shortcuts: bool = False, back: str | None = NAV_BACK) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for hour in range(0, 24, 4):
        b.row(*[_btn(f"{h:02d}", f"{prefix}:{h}") for h in range(hour, hour + 4)])
    if date_shortcuts:
        b.row(_btn("Сегодня", "hdt:today"), _btn("Вчера", "hdt:yesterday"), _btn("Позавчера", "hdt:daybefore"))
        b.row(_btn("📅 Дата", "hdt:calendar"))
    b.row(_btn("⌨️ Ввести текстом", f"{prefix}:manual"))
    b.row(*nav_row(back))
    return b.as_markup()


def minutes_kb(prefix: str = "mn", back: str | None = NAV_BACK) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for start in range(0, 60, 15):
        b.row(*[_btn(f"{m:02d}", f"{prefix}:{m}") for m in range(start, start + 15, 5)])
    b.row(*nav_row(back))
    return b.as_markup()


def history_period_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", "hist:today"), _btn("Вчера", "hist:yesterday"))
    b.row(_btn("📅 Дата", "hist:date"), _btn("📆 Период", "hist:range"))
    return with_nav(b)


def history_day_kb(
    rows: list[tuple[str, str]],
    *,
    page: int,
    pages: int,
    day: date,
    period_start: date,
    period_end: date,
    today: date,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, data in rows:
        b.row(_btn(label[:40], data))
    nav: list[InlineKeyboardButton] = []
    prev_day = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    if prev_day >= period_start:
        left = "‹ вчера" if prev_day == today - timedelta(days=1) else f"‹ {prev_day.strftime('%d.%m')}"
        nav.append(_btn(left, f"h:d:{prev_day.isoformat()}"))
    mid = "сегодня" if day == today else day.strftime("%d.%m")
    nav.append(_btn(mid, "noop"))
    if next_day <= period_end:
        right = "завтра ›" if next_day == today + timedelta(days=1) else f"{next_day.strftime('%d.%m')} ›"
        nav.append(_btn(right, f"h:d:{next_day.isoformat()}"))
    if nav:
        b.row(*nav)
    if pages > 1:
        prow: list[InlineKeyboardButton] = []
        if page > 0:
            prow.append(_btn("«", f"h:p:{page - 1}"))
        prow.append(_btn(f"{page + 1}/{pages}", "noop"))
        if page + 1 < pages:
            prow.append(_btn("»", f"h:p:{page + 1}"))
        b.row(*prow)
    return with_nav(b, NAV_HISTORY)


def stats_period_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", "stp:today"), _btn("Вчера", "stp:yesterday"))
    b.row(_btn("7 дней", "stp:7"), _btn("14 дней", "stp:14"))
    b.row(_btn("30 дней", "stp:30"), _btn("📆 Период", "stp:range"))
    return with_nav(b)


def stats_metrics_kb(selected: set[str], custom: list | None = None) -> InlineKeyboardMarkup:
    options = [
        ("cigarettes", "🚬 Сигареты"),
        ("fooling", "🤌 Валять дурака"),
        ("snus", "🟢 Снюс"),
        ("sleep", "😴 Сон"),
        ("caffeine", "☕ Кофеин"),
        ("alcohol", "🍺 Алкоголь"),
        ("activity", "🏃 Активность"),
        ("steps", "🚶 Шаги"),
        ("weight", "⚖️ Вес"),
    ]
    b = InlineKeyboardBuilder()
    for key, label in options:
        mark = "☑" if key in selected else "☐"
        b.row(_btn(f"{mark} {label}", f"stm:{key}"))
    for metric in custom or []:
        key = f"m{metric.id}"
        mark = "☑" if key in selected else "☐"
        b.row(_btn(f"{mark} {metric.name[:24]}", f"stm:{key}"))
    b.row(_btn("📝 Текст", "stv:text"), _btn("📈 График", "stv:chart"))
    return with_nav(b, NAV_STATS)


def settings_kb(user: User) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(f"🌍 Часовой пояс: {user.timezone}", "set:tz"))
    b.row(_btn(f"🌙 Сон по умолчанию: {user.default_sleep_time}", "set:sleep"))
    b.row(_btn("📋 Кнопки меню", "set:vis"))
    b.row(_btn("📤 Выгрузить CSV", "set:exp"))
    b.row(_btn("📞 Связаться с владельцем", "set:contact"))
    b.row(_btn("📄 Политика конфиденциальности", "lg:p:0:s"))
    b.row(_btn("📜 Пользовательское соглашение", "lg:t:0:s"))
    b.row(_btn("🗑 Удалить аккаунт", "set:del"))
    return with_nav(b)


def menu_types_kb(hidden: set[str]) -> InlineKeyboardMarkup:
    from services.ui_prefs import HIDEABLE_LABELS, HIDEABLE_TYPES

    b = InlineKeyboardBuilder()
    for key in HIDEABLE_TYPES:
        mark = "☐" if key in hidden else "☑"
        b.row(_btn(f"{mark} {HIDEABLE_LABELS[key]}", f"set:vis:{key}"))
    return with_nav(b, NAV_SETTINGS)


def export_period_kb(back: str = NAV_SETTINGS, *, prefix: str = "exp") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", f"{prefix}:today"), _btn("7 дней", f"{prefix}:7"))
    b.row(_btn("30 дней", f"{prefix}:30"))
    return with_nav(b, back)


def how_to_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("📖 Подробный гайд", NAV_GUIDE))
    b.row(_btn("Понятно", "onb:ok"))
    return b.as_markup()


def guide_index_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Как записать событие", "g:write"))
    b.row(_btn("🚬 Сигарета", "g:cig"), _btn("🟢 Снюс", "g:snus"))
    b.row(_btn("🤌 Валять дурака", "g:fool"), _btn("😴 Сон", "g:sleep"))
    b.row(_btn("☕ Кофеин", "g:caf"), _btn("🍺 Алкоголь", "g:alc"))
    b.row(_btn("🏃 Активность", "g:act"), _btn("🚶 Шаги", "g:stp"))
    b.row(_btn("⚖️ Вес", "g:wgt"), _btn("📌 Кастом", "g:cm"))
    b.row(_btn("🔖 Метки", "g:mk"))
    b.row(_btn("📊 Статистика", "g:st"), _btn("📅 История", "g:hist"))
    b.row(_btn("⚙️ Настройки", "g:set"), _btn("💰 Баланс", "g:bal"))
    return with_nav(b)


def guide_page_kb() -> InlineKeyboardMarkup:
    return back_kb(NAV_GUIDE)


def charts_done_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Другой период", NAV_STATS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def paid_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Пропустить сумму", "bal:paid:0"))
    return with_nav(b, NAV_BALANCE)


def balance_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Я оплатил", "bal:paid"))
    return with_nav(b)


def legal_consent_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("📄 Политика конфиденциальности", "lg:p:0:c"))
    b.row(_btn("📜 Пользовательское соглашение", "lg:t:0:c"))
    b.row(_btn("✅ Принимаю", "lg:ok"))
    return b.as_markup()


def legal_page_kb(doc_token: str, page: int, pages: int, origin: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("«", f"lg:{doc_token}:{page - 1}:{origin}"))
    if pages > 1:
        nav.append(_btn(f"{page + 1}/{pages}", "noop"))
    if page + 1 < pages:
        nav.append(_btn("»", f"lg:{doc_token}:{page + 1}:{origin}"))
    if nav:
        b.row(*nav)
    if origin == "c":
        b.row(_btn("⬅️ Назад", "lg:home"))
        b.row(_btn("✅ Принимаю", "lg:ok"))
    else:
        b.row(*nav_row(NAV_SETTINGS))
    return b.as_markup()


def confirm_delete_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Да, удалить аккаунт", "set:del:yes"))
    b.row(_btn("Отмена", NAV_SETTINGS))
    return b.as_markup()


def entry_actions(
    kind: str,
    item_id: int,
    writable: bool,
    *,
    undo: bool = False,
    from_history: bool = False,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable:
        delete_label = "🗑 Отменить" if undo else "🗑 Удалить"
        delete_cb = f"un:{kind}:{item_id}" if undo else f"rm:{kind}:{item_id}"
        if kind == "stp":
            b.row(_btn("✏️ Изменить", f"stp:e:{item_id}"), _btn(delete_label, delete_cb))
        else:
            b.row(_btn("✏️ Изменить", f"ed:{kind}:{item_id}"), _btn(delete_label, delete_cb))
        if kind == "act":
            b.row(_btn("💬 Коммент", f"act:cmt:{item_id}"))
    hist = _btn("⬅️ Назад", "h:back") if from_history else _btn("📅 История", NAV_HISTORY)
    b.row(hist, _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def confirm_remove_kb(kind: str, item_id: int, *, undo: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if undo:
        b.row(_btn("Отменить", f"unok:{kind}:{item_id}"), _btn("Оставить", f"sv:{kind}:{item_id}"))
    else:
        b.row(_btn("Удалить", f"rmok:{kind}:{item_id}"), _btn("Отмена", f"h:o:{kind}:{item_id}"))
    return b.as_markup()


def skip_comment_kb(back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Пропустить", "wb:skip"))
    b.row(*nav_row(back))
    return b.as_markup()


def _metric_quick_row(metric, open_ids: set[int] | None = None) -> list[InlineKeyboardButton]:
    name = (metric.name or "Метрика")[:20]
    if getattr(metric, "data_type", None) == "period":
        if open_ids and metric.id in open_ids:
            name = f"{name} · идёт"[:28]
        return [
            _btn(name, f"cm:o:{metric.id}"),
            _btn("▶️", f"cm:st:{metric.id}"),
            _btn("⏹", f"cm:en:{metric.id}"),
        ]
    return [_btn(name, f"cm:o:{metric.id}"), _btn("➕", f"cm:add:{metric.id}")]


def custom_metrics_kb(
    metrics, writable: bool, *, open_ids: set[int] | None = None
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for metric in metrics:
        flag = "" if metric.enabled else " (выкл)"
        if writable and metric.enabled and getattr(metric, "data_type", None) == "period":
            b.row(*_metric_quick_row(metric, open_ids))
            continue
        name_btn = _btn(f"{metric.name}{flag}", f"cm:o:{metric.id}")
        if writable and metric.enabled:
            b.row(name_btn, _btn("➕", f"cm:add:{metric.id}"))
        else:
            b.row(name_btn)
    if writable:
        b.row(_btn("➕ Создать метрику", "cm:new"))
    return with_nav(b)


def metric_templates_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for item in METRIC_TEMPLATES:
        b.row(_btn(item.button, f"cm:tpl:{item.key}"))
    b.row(_btn("✏️ Своя метрика", "cm:own"))
    b.row(_btn("✖️ Отмена", NAV_METRICS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def metric_types_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, spec in METRIC_TYPES.items():
        b.row(_btn(spec.button_label, f"cm:t:{key}"))
    b.row(_btn("✖️ Отмена", NAV_METRICS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def metric_units_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for key, label in UNIT_PRESETS:
        row.append(_btn(label, f"cm:u:{key}"))
        if len(row) == 3:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    b.row(_btn("Без единицы", "cm:u:none"), _btn("Другая единица", "cm:u:own"))
    b.row(_btn("⬅️ Назад", "cm:types"), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


METRIC_NUMBER_PRESETS: dict[str, tuple[tuple[float, str], ...]] = {
    "мл": ((100, "100 мл"), (250, "250 мл"), (330, "330 мл"), (500, "500 мл"), (1000, "1 л")),
    "л": ((0.25, "0,25 л"), (0.33, "0,33 л"), (0.5, "0,5 л"), (1, "1 л"), (1.5, "1,5 л")),
    "шт": ((1, "1"), (2, "2"), (3, "3"), (5, "5"), (10, "10")),
    "кг": ((0.5, "0,5"), (1, "1"), (2, "2"), (5, "5"), (10, "10")),
    "шаги": ((1000, "1 000"), (3000, "3 000"), (5000, "5 000"), (8000, "8 000"), (10000, "10 000")),
    "стр": ((5, "5"), (10, "10"), (20, "20"), (50, "50")),
    "км": ((1, "1 км"), (3, "3 км"), (5, "5 км"), (10, "10 км")),
    "%": ((25, "25%"), (50, "50%"), (75, "75%"), (100, "100%")),
    "₽": ((50, "50 ₽"), (100, "100 ₽"), (200, "200 ₽"), (500, "500 ₽")),
    "мин": ((5, "5 мин"), (10, "10 мин"), (15, "15 мин"), (30, "30 мин"), (60, "1 ч")),
}

_DEFAULT_NUMBER_PRESETS = ((1, "1"), (2, "2"), (3, "3"), (5, "5"), (10, "10"), (20, "20"))

_METRIC_TIMES = ("06:00", "07:00", "08:00", "09:00", "12:00", "18:00", "21:00", "22:00", "23:00")


def _metric_cancel_row(back: str) -> list[InlineKeyboardButton]:
    return [_btn("✖️ Отмена", back), _btn("🏠 Меню", NAV_MAIN)]


def metric_number_kb(unit: str | None, back: str) -> InlineKeyboardMarkup:
    presets = METRIC_NUMBER_PRESETS.get((unit or "").strip().lower(), _DEFAULT_NUMBER_PRESETS)
    b = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for amount, label in presets:
        token = str(int(amount)) if abs(amount - round(amount)) < 1e-9 else f"{amount:g}"
        row.append(_btn(label, f"cm:q:{token}"))
        if len(row) == 2:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    b.row(*_metric_cancel_row(back))
    return b.as_markup()


def metric_duration_kb(back: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("15 мин", "cm:d:15"), _btn("30 мин", "cm:d:30"), _btn("45 мин", "cm:d:45"))
    b.row(_btn("1 ч", "cm:d:60"), _btn("1,5 ч", "cm:d:90"), _btn("2 ч", "cm:d:120"))
    b.row(*_metric_cancel_row(back))
    return b.as_markup()


def metric_time_kb(back: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for stamp in _METRIC_TIMES:
        row.append(_btn(stamp, f"cm:tm:{stamp.replace(':', '')}"))
        if len(row) == 3:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    b.row(*_metric_cancel_row(back))
    return b.as_markup()


def metric_card_kb(
    metric_id: int,
    enabled: bool,
    writable: bool,
    *,
    pinned: bool = False,
    can_pin: bool = True,
    data_type: str | None = None,
    has_open: bool = False,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable:
        if data_type == "period":
            if has_open:
                b.row(_btn("⏹ Закончил", f"cm:en:{metric_id}"))
            else:
                b.row(
                    _btn("▶️ Начал", f"cm:st:{metric_id}"),
                    _btn("⏹ Закончил", f"cm:en:{metric_id}"),
                )
        else:
            b.row(_btn("➕ Записать значение", f"cm:add:{metric_id}"))
        label = "Выключить" if enabled else "Включить"
        b.row(_btn(label, f"cm:tog:{metric_id}"))
        if pinned:
            b.row(_btn("📍 Убрать с главной", f"cm:pin:{metric_id}"))
        elif can_pin:
            b.row(_btn("📌 На главную", f"cm:pin:{metric_id}"))
    b.row(_btn("⬅️ К метрикам", NAV_METRICS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def bool_kb(back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("✅ Да", "cm:v:1"), _btn("✖️ Нет", "cm:v:0"))
    if back:
        b.row(_btn("✖️ Отмена", back), _btn("🏠 Меню", NAV_MAIN))
    else:
        b.row(*nav_row())
    return b.as_markup()


def choices_kb(choices: list[str], back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, choice in enumerate(choices):
        b.row(_btn(choice, f"cm:ch:{idx}"))
    if back:
        b.row(_btn("✖️ Отмена", back), _btn("🏠 Меню", NAV_MAIN))
    else:
        b.row(*nav_row())
    return b.as_markup()


def admin_root_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("👥 Пользователи", "ad:users"), _btn("🔎 Поиск", "ad:search"))
    b.row(_btn("💰 Балансы", "ad:bal"), _btn("📋 Операции", "ad:ops"))
    b.row(_btn("📊 Статистика сервиса", "ad:stats"), _btn("🖴 Аптайм", "ad:vpn"))
    b.row(_btn("⚙️ Настройки", "ad:cfg"), _btn("🗄 База данных", "ad:dbe"))
    b.row(_btn("📦 Бэкапы", "ad:bk"), _btn("📢 Рассылка", "ad:bc"))
    return with_nav(b)


def admin_broadcast_kb(counts: dict[str, int] | None = None) -> InlineKeyboardMarkup:
    counts = counts or {}

    def label(text: str, key: str) -> str:
        if key not in counts:
            return text
        return f"{text} ({counts[key]})"

    b = InlineKeyboardBuilder()
    b.row(_btn(label("👥 Все активные", "all"), "ad:bc:all"))
    b.row(_btn(label("✅ С доступом", "paid"), "ad:bc:paid"))
    b.row(_btn(label("💸 Без оплаты", "unpaid"), "ad:bc:unpaid"))
    b.row(*nav_row(NAV_ADMIN))
    return b.as_markup()


def spam_alert_kb(telegram_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("👤 Карточка", f"ad:u:{telegram_id}"), _btn("🚫 Заблокировать", f"ad:bn:{telegram_id}"))
    return b.as_markup()


def admin_user_kb(telegram_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("➕ Пополнить", f"ad:cr:{telegram_id}"), _btn("➖ Списать", f"ad:db:{telegram_id}"))
    b.row(_btn("🎯 Установить баланс", f"ad:st:{telegram_id}"), _btn("💸 Стоимость/день", f"ad:pr:{telegram_id}"))
    b.row(_btn("📋 Операции", f"ad:op:{telegram_id}"), _btn("📊 Статистика", f"ad:us:{telegram_id}"))
    b.row(_btn("📤 CSV", f"ad:exp:{telegram_id}"))
    b.row(_btn("🚫 Заблокировать", f"ad:bn:{telegram_id}"), _btn("✅ Разблокировать", f"ad:un:{telegram_id}"))
    b.row(_btn("🔎 Поиск", "ad:search"), _btn("🛠 Админка", NAV_ADMIN))
    b.row(_btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def admin_credit_kind_kb(telegram_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        _btn("💵 Доход", f"ad:cri:{telegram_id}"),
        _btn("🎁 Подарок", f"ad:crg:{telegram_id}"),
    )
    b.row(_btn("✖️ Отмена", f"ad:u:{telegram_id}"))
    return b.as_markup()


def users_page_kb(offset: int, has_next: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    row = []
    if offset > 0:
        row.append(_btn("«", f"ad:up:{max(0, offset - 10)}"))
    if has_next:
        row.append(_btn("»", f"ad:up:{offset + 10}"))
    if row:
        b.row(*row)
    b.row(_btn("🛠 Админка", NAV_ADMIN), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def admin_period_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", "ads:today"), _btn("7 дней", "ads:7"), _btn("30 дней", "ads:30"))
    b.row(_btn("Всё время", "ads:all"))
    b.row(_btn("🛠 Админка", NAV_ADMIN))
    return b.as_markup()


def admin_restore_confirm_kb(*, disk: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    confirm = "ad:bkrok" if disk else "ad:rstok"
    b.row(_btn("🔄 Восстановить и перезапустить", confirm))
    b.row(_btn("✖️ Отмена", "ad:bk"))
    return b.as_markup()


def admin_backups_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("📤 Сделать бэкап сейчас", "ad:bknow"))
    b.row(_btn("🔄 Восстановить из файла", "ad:rst"))
    b.row(_btn("🗄 Копии на диске", "ad:bkl"))
    b.row(_btn("🛠 Админка", NAV_ADMIN))
    return b.as_markup()


def admin_disk_backups_kb(total: int, offset: int, page: int = 5) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    end = min(offset + page, total)
    for index in range(offset, end):
        b.row(
            _btn("📄 Отправить", f"ad:bks:{index}"),
            _btn("🔄 Восстановить", f"ad:bkr:{index}"),
        )
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(_btn("«", f"ad:bkl:{max(0, offset - page)}"))
    if end < total:
        nav.append(_btn("»", f"ad:bkl:{offset + page}"))
    if nav:
        b.row(*nav)
    b.row(_btn("📦 Бэкапы", "ad:bk"), _btn("🛠 Админка", NAV_ADMIN))
    return b.as_markup()


def admin_db_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("📋 Таблицы", "ad:tbls"), _btn("📄 Схема", "ad:dsch"))
    b.row(_btn("⌨️ SQL-запрос", "ad:sql"), _btn("🩺 Целостность", "ad:dint"))
    b.row(_btn("🧹 Очистить базу", "ad:clr"), _btn("📦 Бэкапы", "ad:bk"))
    b.row(_btn("🛠 Админка", NAV_ADMIN))
    return b.as_markup()


def admin_tables_kb(tables: list[tuple[str, int]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for name, count in tables:
        b.row(_btn(f"{name} · {count}", f"ad:tp:{name}:0"))
    b.row(_btn("🗄 База", "ad:dbe"), _btn("🛠 Админка", NAV_ADMIN))
    return b.as_markup()


def admin_table_kb(name: str, offset: int, total: int, page: int = 10) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(_btn("«", f"ad:tp:{name}:{max(0, offset - page)}"))
    if offset + page < total:
        nav.append(_btn("»", f"ad:tp:{name}:{offset + page}"))
    if nav:
        b.row(*nav)
    b.row(_btn("📄 Скачать CSV", f"ad:tf:{name}"))
    b.row(_btn("📋 Таблицы", "ad:tbls"), _btn("🗄 База", "ad:dbe"))
    return b.as_markup()


def admin_sql_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("⌨️ Ещё запрос", "ad:sql"))
    b.row(_btn("🗄 База", "ad:dbe"), _btn("🛠 Админка", NAV_ADMIN))
    return b.as_markup()


_VPN_SPAN_LABELS = {
    "5m": "5 мин",
    "30m": "30 мин",
    "1h": "час",
    "6h": "6 часов",
    "12h": "12 часов",
    "24h": "сутки",
    "7d": "неделю",
    "30d": "месяц",
    "all": "всё время",
}


def admin_vpn_kb(period: str = "24h", view: str = "n", *, rounded: bool = False) -> InlineKeyboardMarkup:
    if view not in {"n", "s", "a"}:
        view = "n"
    if view != "a":
        rounded = False
    token = "a:r" if view == "a" and rounded else view
    b = InlineKeyboardBuilder()
    rows = (
        (("5m", "5 мин"), ("30m", "30 мин"), ("1h", "1 ч")),
        (("6h", "6 ч"), ("12h", "12 ч"), ("24h", "сутки")),
        (("7d", "неделя"), ("30d", "месяц"), ("all", "всё время")),
    )
    for labels in rows:
        b.row(*[_btn(("• " if key == period else "") + label, f"adv:{key}:{token}") for key, label in labels])
    b.row(
        _btn(("• " if view == "n" else "") + "Ноды", f"adv:{period}:n"),
        _btn(("• " if view == "s" else "") + "Подписки", f"adv:{period}:s"),
        _btn(("• " if view == "a" else "") + "Доступность", f"adv:{period}:a" + (":r" if rounded else "")),
    )
    span = _VPN_SPAN_LABELS.get(period, "сутки")
    if view == "a":
        b.row(_btn(("• " if rounded else "") + "Округление", f"adv:{period}:a" + ("" if rounded else ":r")))
    b.row(_btn(f"📄 Логи за {span}", f"advl:{period}"))
    if view == "a":
        b.row(_btn(f"📈 Доступность за {span}", f"advc:{period}:a" + (":r" if rounded else "")))
    else:
        b.row(_btn(f"📈 Картинки за {span}", f"advc:{period}"))
    b.row(_btn("🛠 Админка", NAV_ADMIN))
    return b.as_markup()


def _marker_btn_label(marker, tz: str) -> str:
    prefix = ""
    if getattr(marker, "period_role", None) == "start":
        prefix = "▶️ "
    elif getattr(marker, "period_role", None) == "end":
        prefix = "⏹ "
    stamp = format_dt(parse_iso(marker.occurred_at), tz)
    return truncate(f"{prefix}{stamp} {marker.name}", 40)


def markers_root_kb(markers, open_periods, writable: bool, tz: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable:
        b.row(_btn("➕ Метка", "mk:new"), _btn("▶️ Начало периода", "mk:start"))
        b.row(_btn("⏹ Конец периода", "mk:end"), _btn("🔗 Объединить", "mk:join"))
    for period in open_periods:
        start_at = format_dt(parse_iso(period.start_at), tz) if period.start_at else ""
        label = truncate(f"▶️ {period.start_name or 'Период'} · {start_at}", 40)
        b.row(_btn(label, f"mk:p:{period.id}"))
    for marker in markers:
        b.row(_btn(_marker_btn_label(marker, tz), f"mk:o:{marker.id}"))
    return with_nav(b)


def marker_name_kb(same_as: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if same_as:
        b.row(_btn(f"Как у начала: {same_as[:24]}", "mk:samename"))
    b.row(*nav_row(NAV_MARKERS))
    return b.as_markup()


def marker_pick_kb(items, prefix: str, tz: str, *, selected_id: int | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for item in items:
        mark = "• " if selected_id is not None and item.id == selected_id else ""
        b.row(_btn(truncate(f"{mark}{_marker_btn_label(item, tz)}", 40), f"{prefix}:{item.id}"))
    return with_nav(b, NAV_MARKERS)


def period_pick_kb(periods) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for period in periods:
        b.row(_btn((period.start_name or "Период")[:40], f"mk:pe:{period.id}"))
    return with_nav(b, NAV_MARKERS)


def marker_card_kb(
    marker_id: int,
    writable: bool,
    *,
    period_id: int | None = None,
    undo: bool = False,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable:
        delete_label = "🗑 Отменить" if undo else "🗑 Удалить"
        delete_cb = f"un:mk:{marker_id}" if undo else f"rm:mk:{marker_id}"
        b.row(_btn("✏️ Время", f"ed:mk:{marker_id}"), _btn(delete_label, delete_cb))
        b.row(_btn("📝 Название", f"mk:nm:{marker_id}"), _btn("💬 Комментарий", f"mk:cm:{marker_id}"))
        if period_id is not None:
            b.row(_btn("🔓 Убрать период", f"mk:u:{period_id}"))
    b.row(_btn("🔖 К меткам", NAV_MARKERS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def period_card_kb(
    period_id: int,
    writable: bool,
    *,
    open_period: bool,
    start_marker_id: int,
    end_marker_id: int | None = None,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable and open_period:
        b.row(_btn("⏹ Поставить конец", f"mk:pe:{period_id}"))
    b.row(_btn("Начало", f"mk:o:{start_marker_id}"))
    if end_marker_id is not None:
        b.row(_btn("Конец", f"mk:o:{end_marker_id}"))
    if writable:
        b.row(_btn("🔓 Убрать период", f"mk:u:{period_id}"))
    b.row(_btn("🔖 К меткам", NAV_MARKERS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def confirm_unlink_kb(period_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Убрать период", f"mk:uok:{period_id}"), _btn("Отмена", f"mk:p:{period_id}"))
    return b.as_markup()

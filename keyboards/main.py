"""Inline keyboards."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import User
from services.metric_types import METRIC_TYPES
from utils.callbacks import (
    ENTRY_ACT,
    ENTRY_ALC,
    ENTRY_CAF,
    ENTRY_CIG,
    ENTRY_FOOL,
    ENTRY_MOOD,
    ENTRY_NOTE,
    ENTRY_SLEEP,
    ENTRY_SNUS,
    ENTRY_WB,
    NAV_ADMIN,
    NAV_BACK,
    NAV_BALANCE,
    NAV_DAY,
    NAV_HISTORY,
    NAV_MAIN,
    NAV_METRICS,
    NAV_SETTINGS,
    NAV_STATS,
)
from utils.formatting import SCORE_EMOJI, SCORE_LABELS
from utils.time import COMMON_TIMEZONES, MONTHS_RU, WEEKDAYS_RU, format_date


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


def main_menu(user: User, is_owner: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🚬 Сигарета", ENTRY_CIG), _btn("🟢 Снюс", ENTRY_SNUS))
    b.row(_btn("🤌 Валять дурака", ENTRY_FOOL))
    b.row(_btn("😴 Сон", ENTRY_SLEEP), _btn("🙂 Настроение", ENTRY_MOOD))
    b.row(_btn("❤️ Самочувствие", ENTRY_WB), _btn("☕ Кофеин", ENTRY_CAF))
    b.row(_btn("🍺 Алкоголь", ENTRY_ALC), _btn("🏃 Активность", ENTRY_ACT))
    b.row(_btn("📝 Заметка", ENTRY_NOTE), _btn("🌙 Оценить день", NAV_DAY))
    b.row(_btn("📌 Показатели", NAV_METRICS), _btn("📊 Статистика", NAV_STATS))
    b.row(_btn("📅 История", NAV_HISTORY), _btn("⚙️ Настройки", NAV_SETTINGS))
    b.row(_btn("💰 Баланс", NAV_BALANCE))
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
    "mdt": "Когда оценить настроение?",
    "wbt": "Когда оценить самочувствие?",
    "nt": "Когда добавить заметку?",
    "slw": "Когда проснулись? Можно указать время задним числом.",
    "cmt": "Когда зафиксировать?",
}

_WHEN_BACK = {
    "cig": None,
    "fool": None,
    "caft": ENTRY_CAF,
    "alct": ENTRY_ALC,
    "actt": ENTRY_ACT,
    "mdt": ENTRY_MOOD,
    "wbt": ENTRY_WB,
    "nt": ENTRY_NOTE,
    "slw": "slp:wake",
}


def when_title(prefix: str) -> str:
    return _WHEN_TITLES.get(prefix, "Когда это было?")


def when_kb(prefix: str, *, metric_id: int | None = None) -> InlineKeyboardMarkup:
    back = _WHEN_BACK.get(prefix)
    if prefix == "cmt" and metric_id is not None:
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


def sleep_kind_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🌙 Отход ко сну", "slp:tbed"), _btn("☀️ Пробуждение", "slp:twake"))
    return with_nav(b, ENTRY_SLEEP)


def sleep_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🌙 Лёг спать", "slp:bed"), _btn("☀️ Проснулся", "slp:wake"))
    b.row(_btn("Сейчас", "slp:now"), _btn("🕐 Указать время", "slp:time"))
    _relative_when_rows(b, "slp")
    return with_nav(b)


def snus_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🛒 Купил сейчас", "sns:buy"), _btn("🕐 Купил ранее", "sns:tbuy"))
    b.row(_btn("✅ Закончилась сейчас", "sns:end"), _btn("🕐 Закончилась ранее", "sns:tend"))
    return with_nav(b)


def score_kb(prefix: str, back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for score in range(1, 6):
        b.row(_btn(f"{SCORE_EMOJI[score]} {SCORE_LABELS[score].capitalize()}", f"{prefix}:{score}"))
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


def activity_types() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🚶 Ходьба", "act:t:walk"), _btn("🏃 Бег", "act:t:run"))
    b.row(_btn("💪 Тренировка", "act:t:workout"), _btn("🚴 Велосипед", "act:t:bike"))
    b.row(_btn("Другое", "act:t:other"))
    return with_nav(b)


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


def stats_period_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", "stp:today"), _btn("Вчера", "stp:yesterday"))
    b.row(_btn("7 дней", "stp:7"), _btn("14 дней", "stp:14"))
    b.row(_btn("30 дней", "stp:30"), _btn("📆 Период", "stp:range"))
    return with_nav(b)


def stats_metrics_kb(selected: set[str]) -> InlineKeyboardMarkup:
    options = [
        ("cigarettes", "🚬 Сигареты"),
        ("fooling", "🤌 Валять дурака"),
        ("snus", "🟢 Снюс"),
        ("sleep", "😴 Сон"),
        ("mood", "🙂 Настроение"),
        ("wellbeing", "❤️ Самочувствие"),
        ("caffeine", "☕ Кофеин"),
        ("alcohol", "🍺 Алкоголь"),
        ("activity", "🏃 Активность"),
    ]
    b = InlineKeyboardBuilder()
    for key, label in options:
        mark = "☑" if key in selected else "☐"
        b.row(_btn(f"{mark} {label}", f"stm:{key}"))
    b.row(_btn("📝 Текст", "stv:text"), _btn("📈 График", "stv:chart"))
    return with_nav(b, NAV_STATS)


def settings_kb(user: User) -> InlineKeyboardMarkup:
    rem = "Вкл" if user.reminders_enabled else "Выкл"
    b = InlineKeyboardBuilder()
    b.row(_btn(f"🌍 Часовой пояс: {user.timezone}", "set:tz"))
    b.row(_btn(f"🔔 Напоминания: {rem}", "set:rem"))
    b.row(_btn(f"🌙 Сон по умолчанию: {user.default_sleep_time}", "set:sleep"))
    b.row(_btn("📞 Связаться с владельцем", "set:contact"))
    b.row(_btn("🗑 Удалить аккаунт", "set:del"))
    return with_nav(b)


def confirm_delete_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Да, удалить аккаунт", "set:del:yes"))
    b.row(_btn("Отмена", NAV_SETTINGS))
    return b.as_markup()


def entry_actions(kind: str, item_id: int, writable: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable:
        b.row(_btn("✏️ Изменить", f"ed:{kind}:{item_id}"), _btn("🗑 Удалить", f"rm:{kind}:{item_id}"))
    b.row(_btn("📅 История", NAV_HISTORY), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def confirm_remove_kb(kind: str, item_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Удалить", f"rmok:{kind}:{item_id}"), _btn("Отмена", f"h:o:{kind}:{item_id}"))
    return b.as_markup()


def skip_comment_kb(back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Пропустить", "wb:skip"))
    b.row(*nav_row(back))
    return b.as_markup()


def custom_metrics_kb(metrics, writable: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for metric in metrics:
        flag = "" if metric.enabled else " (выкл)"
        b.row(_btn(f"{metric.name}{flag}", f"cm:o:{metric.id}"))
    if writable:
        b.row(_btn("➕ Создать показатель", "cm:new"))
    return with_nav(b)


def metric_types_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, spec in METRIC_TYPES.items():
        b.row(_btn(spec.label, f"cm:t:{key}"))
    b.row(_btn("✖️ Отмена", NAV_METRICS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def metric_card_kb(metric_id: int, enabled: bool, writable: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable:
        b.row(_btn("➕ Добавить значение", f"cm:add:{metric_id}"))
        label = "Выключить" if enabled else "Включить"
        b.row(_btn(label, f"cm:tog:{metric_id}"))
    b.row(_btn("⬅️ К показателям", NAV_METRICS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def bool_kb(back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Да", "cm:v:1"), _btn("Нет", "cm:v:0"))
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


def admin_user_kb(telegram_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("➕ Пополнить", f"ad:cr:{telegram_id}"), _btn("➖ Списать", f"ad:db:{telegram_id}"))
    b.row(_btn("🎯 Установить баланс", f"ad:st:{telegram_id}"), _btn("💸 Стоимость/день", f"ad:pr:{telegram_id}"))
    b.row(_btn("📋 Операции", f"ad:op:{telegram_id}"), _btn("📊 Статистика", f"ad:us:{telegram_id}"))
    b.row(_btn("🚫 Заблокировать", f"ad:bn:{telegram_id}"), _btn("✅ Разблокировать", f"ad:un:{telegram_id}"))
    b.row(_btn("🔎 Поиск", "ad:search"), _btn("🛠 Админка", NAV_ADMIN))
    b.row(_btn("🏠 Меню", NAV_MAIN))
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
    "1h": "час",
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
        (("5m", "5 мин"), ("1h", "1 ч"), ("24h", "сутки")),
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

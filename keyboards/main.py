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
    ENTRY_MOOD,
    ENTRY_NOTE,
    ENTRY_SLEEP,
    ENTRY_SNUS,
    ENTRY_WB,
    NAV_ADMIN,
    NAV_BALANCE,
    NAV_CANCEL,
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


def nav_row(back: str | None = None) -> list[InlineKeyboardButton]:
    row = []
    if back:
        row.append(_btn("⬅️ Назад", back))
    row.append(_btn("🏠 Меню", NAV_MAIN))
    return row


def with_nav(builder: InlineKeyboardBuilder, back: str | None = None) -> InlineKeyboardMarkup:
    builder.row(*nav_row(back))
    return builder.as_markup()


def main_menu(user: User, is_owner: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🚬 Сигарета", ENTRY_CIG), _btn("🟢 Снюс", ENTRY_SNUS))
    b.row(_btn("😴 Сон", ENTRY_SLEEP), _btn("🙂 Настроение", ENTRY_MOOD))
    b.row(_btn("❤️ Самочувствие", ENTRY_WB), _btn("☕ Кофеин", ENTRY_CAF))
    b.row(_btn("🍺 Алкоголь", ENTRY_ALC), _btn("🏃 Активность", ENTRY_ACT))
    b.row(_btn("📝 Заметка", ENTRY_NOTE), _btn("🌙 Оценить день", NAV_DAY))
    b.row(_btn("📌 Показатели", NAV_METRICS), _btn("📊 Статистика", NAV_STATS))
    b.row(_btn("📅 История", NAV_HISTORY), _btn("⚙️ Настройки", NAV_SETTINGS))
    b.row(_btn("💰 Баланс", NAV_BALANCE))
    if is_owner:
        b.row(_btn("👑 Админ-панель", NAV_ADMIN))
    return b.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("✖️ Отмена", NAV_CANCEL), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def now_or_time(prefix: str, back: str = NAV_MAIN) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сейчас", f"{prefix}:now"), _btn("🕐 Указать время", f"{prefix}:time"))
    return with_nav(b, back)


def sleep_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🌙 Лёг спать", "slp:bed"), _btn("☀️ Проснулся", "slp:wake"))
    b.row(_btn("Сейчас", "slp:now"), _btn("🕐 Указать время", "slp:time"))
    return with_nav(b, NAV_MAIN)


def snus_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🛒 Купил сейчас", "sns:buy"), _btn("🕐 Купил ранее", "sns:tbuy"))
    b.row(_btn("✅ Закончилась сейчас", "sns:end"), _btn("🕐 Закончилась ранее", "sns:tend"))
    return with_nav(b, NAV_MAIN)


def score_kb(prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for score in range(1, 6):
        b.row(_btn(f"{SCORE_EMOJI[score]} {SCORE_LABELS[score].capitalize()}", f"{prefix}:{score}"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def caffeine_types() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("☕ Кофе", "caf:t:coffee"), _btn("⚡ Энергетик", "caf:t:energy"))
    b.row(_btn("🍵 Чай", "caf:t:tea"), _btn("Другое", "caf:t:other"))
    return with_nav(b, NAV_MAIN)


def alcohol_types() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🍺 Пиво", "alc:t:beer"), _btn("🍷 Вино", "alc:t:wine"))
    b.row(_btn("🥃 Крепкий", "alc:t:spirits"), _btn("🍹 Коктейль", "alc:t:cocktail"))
    b.row(_btn("Другое", "alc:t:other"))
    return with_nav(b, NAV_MAIN)


def activity_types() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🚶 Ходьба", "act:t:walk"), _btn("🏃 Бег", "act:t:run"))
    b.row(_btn("💪 Тренировка", "act:t:workout"), _btn("🚴 Велосипед", "act:t:bike"))
    b.row(_btn("Другое", "act:t:other"))
    return with_nav(b, NAV_MAIN)


def timezone_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for tz, label in COMMON_TIMEZONES:
        b.row(_btn(label, f"tz:{tz}"))
    b.row(_btn("Другой (IANA)", "tz:custom"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def calendar_kb(year: int, month: int, prefix: str = "cal") -> InlineKeyboardMarkup:
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
    b.row(_btn("Сегодня", f"{prefix}:today"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def hours_kb(prefix: str = "hr", *, date_shortcuts: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for hour in range(0, 24, 4):
        b.row(*[_btn(f"{h:02d}", f"{prefix}:{h}") for h in range(hour, hour + 4)])
    if date_shortcuts:
        b.row(_btn("Сегодня", "hdt:today"), _btn("Вчера", "hdt:yesterday"), _btn("📅 Дата", "hdt:calendar"))
    b.row(_btn("⌨️ Ввести вручную", f"{prefix}:manual"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def minutes_kb(prefix: str = "mn") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for start in range(0, 60, 15):
        b.row(*[_btn(f"{m:02d}", f"{prefix}:{m}") for m in range(start, start + 15, 5)])
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def history_period_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", "hist:today"), _btn("Вчера", "hist:yesterday"))
    b.row(_btn("📅 Дата", "hist:date"), _btn("📆 Период", "hist:range"))
    return with_nav(b, NAV_MAIN)


def stats_period_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", "stp:today"), _btn("Вчера", "stp:yesterday"))
    b.row(_btn("7 дней", "stp:7"), _btn("14 дней", "stp:14"))
    b.row(_btn("30 дней", "stp:30"), _btn("📆 Период", "stp:range"))
    return with_nav(b, NAV_MAIN)


def stats_metrics_kb(selected: set[str]) -> InlineKeyboardMarkup:
    options = [
        ("cigarettes", "🚬 Сигареты"),
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
    return with_nav(b, NAV_MAIN)


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
    b.row(_btn("Удалить", f"rmok:{kind}:{item_id}"), _btn("Отмена", NAV_HISTORY))
    return b.as_markup()


def skip_comment_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Пропустить", "wb:skip"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def custom_metrics_kb(metrics, writable: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for metric in metrics:
        flag = "" if metric.enabled else " (выкл)"
        b.row(_btn(f"{metric.name}{flag}", f"cm:o:{metric.id}"))
    if writable:
        b.row(_btn("➕ Создать показатель", "cm:new"))
    return with_nav(b, NAV_MAIN)


def metric_types_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, spec in METRIC_TYPES.items():
        b.row(_btn(spec.label, f"cm:t:{key}"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def metric_card_kb(metric_id: int, enabled: bool, writable: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if writable:
        b.row(_btn("➕ Добавить значение", f"cm:add:{metric_id}"))
        label = "Выключить" if enabled else "Включить"
        b.row(_btn(label, f"cm:tog:{metric_id}"))
    b.row(_btn("⬅️ К показателям", NAV_METRICS), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def bool_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Да", "cm:v:1"), _btn("Нет", "cm:v:0"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def choices_kb(choices: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, choice in enumerate(choices):
        b.row(_btn(choice, f"cm:ch:{idx}"))
    b.row(_btn("✖️ Отмена", NAV_CANCEL))
    return b.as_markup()


def admin_root_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("👥 Пользователи", "ad:users"), _btn("🔎 Поиск", "ad:search"))
    b.row(_btn("💰 Балансы", "ad:bal"), _btn("📋 Операции", "ad:ops"))
    b.row(_btn("📊 Статистика сервиса", "ad:stats"), _btn("🛡 VPN", "ad:vpn"))
    b.row(_btn("⚙️ Настройки", "ad:cfg"))
    return with_nav(b, NAV_MAIN)


def admin_user_kb(telegram_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("➕ Пополнить", f"ad:cr:{telegram_id}"), _btn("➖ Списать", f"ad:db:{telegram_id}"))
    b.row(_btn("🎯 Установить баланс", f"ad:st:{telegram_id}"), _btn("💸 Стоимость/день", f"ad:pr:{telegram_id}"))
    b.row(_btn("📋 Операции", f"ad:op:{telegram_id}"), _btn("📊 Статистика", f"ad:us:{telegram_id}"))
    b.row(_btn("🚫 Заблокировать", f"ad:bn:{telegram_id}"), _btn("✅ Разблокировать", f"ad:un:{telegram_id}"))
    b.row(_btn("🔎 Поиск", "ad:search"), _btn("👑 Админка", NAV_ADMIN))
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
    b.row(_btn("👑 Админка", NAV_ADMIN), _btn("🏠 Меню", NAV_MAIN))
    return b.as_markup()


def admin_period_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("Сегодня", "ads:today"), _btn("7 дней", "ads:7"), _btn("30 дней", "ads:30"))
    b.row(_btn("👑 Админка", NAV_ADMIN))
    return b.as_markup()


def admin_vpn_kb(period: str = "24h") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    labels = (("1h", "1 ч"), ("24h", "сутки"), ("7d", "неделя"), ("30d", "месяц"))
    b.row(*[_btn(("• " if key == period else "") + label, f"adv:{key}") for key, label in labels])
    b.row(_btn("👑 Админка", NAV_ADMIN))
    return b.as_markup()

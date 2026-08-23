"""Shared handler helpers."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import Config
from database.models import SleepRecord, User
from database.queries import Repo
from keyboards.main import back_kb, calendar_kb, hours_kb, main_menu, timezone_kb, when_kb
from services.users import access_message, can_write, write_block_message
from utils.formatting import balance_runway, money
from utils.telegram import safe_edit

TZ_PROMPT = "Выберите часовой пояс. Он нужен для статистики, границ дня и напоминаний."
TZ_RESTORE_PROMPT = (
    "Аккаунт был удалён. Данные сохранены.\n\n"
    "Выберите часовой пояс, чтобы восстановить доступ."
)
BANNED_TEXT = "Доступ ограничен. Напишите владельцу сервиса."


def menu_text(user: User, config: Config) -> str:
    write_ok = "доступны" if can_write(user) else "временно недоступны"
    return (
        f"📓 <b>Дневник</b>\n\n"
        f"Привет, {user.display_name}!\n"
        f"💰 Баланс: {money(user.balance)} · {money(user.daily_price)}/день · {balance_runway(user)}\n"
        f"Новые записи: {write_ok}\n\n"
        f"Выберите действие:"
    )


def start_payload(
    user: User | None,
    config: Config,
    is_owner: bool,
    sleep: SleepRecord | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Text and keyboard of /start — same payload used for lifecycle pings."""
    if user and user.is_banned:
        return BANNED_TEXT, None
    if user and user.is_active:
        return menu_text(user, config), main_menu(user, is_owner, sleep)
    prompt = TZ_RESTORE_PROMPT if user and user.is_deleted else TZ_PROMPT
    return prompt, timezone_kb()


async def show_main(
    target: CallbackQuery | Message,
    user: User,
    config: Config,
    is_owner: bool,
    state: FSMContext | None = None,
    repo: Repo | None = None,
) -> None:
    if state:
        await state.clear()
    sleep = await repo.latest_sleep(user.telegram_id) if repo is not None else None
    text, markup = start_payload(user, config, is_owner, sleep)
    if isinstance(target, CallbackQuery):
        await target.answer()
        await safe_edit(target.message, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


async def require_active(event: CallbackQuery | Message, user: User | None) -> User | None:
    if user is None:
        text = "Сначала нажмите /start для регистрации."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None
    blocked = access_message(user)
    if blocked:
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit(event.message, blocked, back_kb())
        else:
            await event.answer(blocked)
        return None
    return user


async def require_writable(event: CallbackQuery | Message, user: User | None) -> User | None:
    user = await require_active(event, user)
    if user is None:
        return None
    blocked = write_block_message(user)
    if blocked:
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit(event.message, blocked, main_menu(user, False))
        else:
            await event.answer(blocked)
        return None
    return user


async def ask_when_after_amount(event: CallbackQuery | Message, state: FSMContext) -> None:
    data = await state.get_data()
    prefix = "caft" if data.get("amount_kind") == "caf" else "alct"
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, "Когда это было?", when_kb(prefix))
        return
    await event.answer("Когда это было?", reply_markup=when_kb(prefix))


async def start_time_pick(
    cb: CallbackQuery,
    state: FSMContext,
    purpose: str,
    extra: dict | None = None,
    *,
    skip_date: bool = False,
) -> None:
    from datetime import date

    from states.diary import TimePickSG
    from utils.callbacks import NAV_BACK
    from utils.time import user_today

    extra = dict(extra or {})
    if "time_exit" not in extra:
        if purpose.startswith("edit:"):
            _, kind, raw_id = purpose.split(":", 2)
            extra["time_exit"] = f"hist:{kind}:{raw_id}"
        else:
            extra["time_exit"] = {
                "cig": "when:cig",
                "fool": "when:fool",
                "slp_onset": "slp_onset",
                "slp_bed": "sleep",
                "slp_wake": "when:slw",
                "snus_buy": "snus",
                "snus_end": "snus",
                "caf": "when:caft",
                "alc": "when:alct",
                "act": "when:actt",
                "cm": "when:cmt",
                "mk": "when:mkt",
            }.get(purpose, "when:cig")
    user_tz = extra.get("tz")
    today = user_today(user_tz) if user_tz else date.today()
    payload = {"time_purpose": purpose, "picked_date": today.isoformat(), **extra}
    if skip_date:
        payload["time_date_shortcuts"] = True
        await state.set_state(TimePickSG.hour)
        await state.update_data(**payload)
        await cb.answer()
        await safe_edit(
            cb.message,
            f"Дата: {today.isoformat()} (сегодня)\nВыберите час — можно уже прошедший:",
            hours_kb(date_shortcuts=True, back=NAV_BACK),
        )
        return
    await state.set_state(TimePickSG.date)
    await state.update_data(**payload)
    await cb.answer()
    await safe_edit(cb.message, "Выберите дату:", calendar_kb(today.year, today.month, back=NAV_BACK))

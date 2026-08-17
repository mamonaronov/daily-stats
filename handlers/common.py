"""Shared handler helpers."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from keyboards.main import cancel_kb, main_menu
from services.users import access_message, can_write, write_block_message
from utils.formatting import money, paid_days
from utils.telegram import safe_edit


def menu_text(user: User, config: Config) -> str:
    days = paid_days(user.balance, user.daily_price)
    write_ok = "доступны" if can_write(user) else "временно недоступны"
    return (
        f"📓 <b>Дневник</b>\n\n"
        f"Привет, {user.display_name}!\n"
        f"💰 Баланс: {money(user.balance)} · {money(user.daily_price)}/день · ~{days} дн.\n"
        f"Новые записи: {write_ok}\n\n"
        f"Выберите действие:"
    )


async def show_main(
    target: CallbackQuery | Message,
    user: User,
    config: Config,
    is_owner: bool,
    state: FSMContext | None = None,
) -> None:
    if state:
        await state.clear()
    text = menu_text(user, config)
    markup = main_menu(user, is_owner)
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
            await safe_edit(event.message, blocked, cancel_kb())
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


async def start_time_pick(cb: CallbackQuery, state: FSMContext, purpose: str, extra: dict | None = None) -> None:
    from datetime import date

    from keyboards.main import calendar_kb
    from states.diary import TimePickSG
    from utils.time import user_today

    payload = {"time_purpose": purpose, **(extra or {})}
    await state.set_state(TimePickSG.date)
    await state.update_data(**payload)
    user_tz = extra.get("tz") if extra else None
    today = user_today(user_tz) if user_tz else date.today()
    await cb.answer()
    await safe_edit(cb.message, "Выберите дату:", calendar_kb(today.year, today.month))

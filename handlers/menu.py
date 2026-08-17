"""Main navigation callbacks."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, show_main
from keyboards.main import now_or_time
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
    NAV_BALANCE,
    NAV_CANCEL,
    NAV_MAIN,
)
from utils.formatting import money, paid_days
from utils.telegram import safe_edit

router = Router(name="menu")


@router.callback_query(F.data == NAV_MAIN)
async def go_main(cb: CallbackQuery, state: FSMContext, db_user: User | None, config: Config, is_owner: bool) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == NAV_CANCEL)
async def cancel(cb: CallbackQuery, state: FSMContext, db_user: User | None, config: Config, is_owner: bool) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        await state.clear()
        return
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data == NAV_BALANCE)
async def show_balance(cb: CallbackQuery, db_user: User | None, repo: Repo) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    user = await repo.get_user(user.telegram_id) or user
    text = (
        f"💰 <b>Баланс</b>\n\n"
        f"Сейчас: {money(user.balance)}\n"
        f"Стоимость: {money(user.daily_price)} / день\n"
        f"Оплаченных дней: ~{paid_days(user.balance, user.daily_price)}\n"
        f"Оплачено до: {user.paid_until_date or '—'}\n\n"
        f"Пополнение выполняется владельцем сервиса вручную после оплаты вне бота."
    )
    from keyboards.main import with_nav
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    await cb.answer()
    await safe_edit(cb.message, text, with_nav(InlineKeyboardBuilder()))


@router.callback_query(F.data == ENTRY_CIG)
async def cig_entry(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "🚬 Сигарета", now_or_time("cig"))


@router.callback_query(F.data == ENTRY_SNUS)
async def snus_entry(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    from handlers.common import require_writable
    from handlers.snus import show_snus_menu

    user = await require_writable(cb, db_user)
    if user is None:
        return
    await show_snus_menu(cb, repo, user)


@router.callback_query(F.data == ENTRY_SLEEP)
async def sleep_entry(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import sleep_menu

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "😴 Сон", sleep_menu())


@router.callback_query(F.data == ENTRY_MOOD)
async def mood_entry(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import score_kb

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "🙂 Как настроение?", score_kb("md"))


@router.callback_query(F.data == ENTRY_WB)
async def wb_entry(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import score_kb

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "❤️ Как самочувствие?", score_kb("wb"))


@router.callback_query(F.data == ENTRY_CAF)
async def caf_entry(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import caffeine_types

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "☕ Что выпили?", caffeine_types())


@router.callback_query(F.data == ENTRY_ALC)
async def alc_entry(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import alcohol_types

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "🍺 Что выпили?", alcohol_types())


@router.callback_query(F.data == ENTRY_ACT)
async def act_entry(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import activity_types

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "🏃 Какая активность?", activity_types())


@router.callback_query(F.data == ENTRY_NOTE)
async def note_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import cancel_kb
    from states.diary import NoteSG

    if await require_writable(cb, db_user) is None:
        return
    await state.set_state(NoteSG.text)
    await cb.answer()
    await safe_edit(cb.message, "📝 Напишите текст заметки", cancel_kb())

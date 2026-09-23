"""Main navigation callbacks."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, require_writable, show_main
from keyboards.main import balance_kb, now_or_time, sleep_actions_kb
from services.legal import legal_contact
from services.ui_prefs import prefs_of
from utils.callbacks import (
    ENTRY_ALC,
    ENTRY_CAF,
    ENTRY_CIG,
    ENTRY_DS,
    ENTRY_FOOL,
    ENTRY_SLEEP,
    ENTRY_SNUS,
    ENTRY_STP,
    ENTRY_WGT,
    NAV_BALANCE,
    NAV_CANCEL,
    NAV_MAIN,
)
from utils.formatting import balance_coverage_block, money
from utils.telegram import safe_edit

router = Router(name="menu")


@router.callback_query(F.data == NAV_MAIN)
async def go_main(
    cb: CallbackQuery,
    state: FSMContext,
    db_user: User | None,
    config: Config,
    is_owner: bool,
    repo: Repo,
) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data == NAV_CANCEL)
async def cancel(
    cb: CallbackQuery,
    state: FSMContext,
    db_user: User | None,
    config: Config,
    is_owner: bool,
    repo: Repo,
) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        await state.clear()
        return
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data == NAV_BALANCE)
async def show_balance(cb: CallbackQuery, db_user: User | None, repo: Repo, config: Config) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    user = await repo.get_user(user.telegram_id) or user
    contact = html.escape(legal_contact(config.owner_contact))
    text = (
        f"💰 <b>Баланс</b>\n\n"
        f"Сейчас: {money(user.balance)}\n"
        f"Стоимость: {money(user.daily_price)} / день\n"
        f"{balance_coverage_block(user)}\n\n"
        f"Чтобы пополнить, напишите владельцу: {contact}\n"
        f"Оплата проходит вне бота. После перевода баланс зачислят вручную."
    )
    await cb.answer()
    await safe_edit(cb.message, text, balance_kb(config.owner_contact))


@router.callback_query(F.data == ENTRY_CIG)
async def cig_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable

    if await require_writable(cb, db_user) is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, "🚬 Сигарета", now_or_time("cig"))


@router.callback_query(F.data == ENTRY_FOOL)
async def fool_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable

    if await require_writable(cb, db_user) is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, "🤌 Валять дурака", now_or_time("fool"))


@router.callback_query(F.data == ENTRY_SNUS)
async def snus_entry(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    from handlers.common import require_writable
    from handlers.snus import show_snus_menu

    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.clear()
    await show_snus_menu(cb, repo, user)


@router.callback_query(F.data == ENTRY_SLEEP)
async def sleep_entry(
    cb: CallbackQuery,
    state: FSMContext,
    db_user: User | None,
    config: Config,
    is_owner: bool,
    repo: Repo,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    sleep = await repo.latest_sleep(user.telegram_id)
    await cb.answer()
    await safe_edit(cb.message, "😴 Сон", sleep_actions_kb(sleep, prefs_of(user).tracked))


@router.callback_query(F.data == ENTRY_CAF)
async def caf_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import caffeine_types

    if await require_writable(cb, db_user) is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, "☕ Что выпили?", caffeine_types())


@router.callback_query(F.data == ENTRY_ALC)
async def alc_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import alcohol_types

    if await require_writable(cb, db_user) is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, "🍺 Что выпили?", alcohol_types())


@router.callback_query(F.data == ENTRY_STP)
async def steps_entry(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    from handlers.common import require_writable
    from handlers.steps import show_steps_menu

    user = await require_writable(cb, db_user)
    if user is None:
        return
    await show_steps_menu(cb, repo, user, state)


@router.callback_query(F.data == ENTRY_WGT)
async def weight_entry(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    from handlers.common import require_writable
    from handlers.weight import show_weight_prompt

    user = await require_writable(cb, db_user)
    if user is None:
        return
    await show_weight_prompt(cb, state, repo, user)


@router.callback_query(F.data == ENTRY_DS)
async def daily_scores_entry(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    from handlers.common import require_writable
    from handlers.daily_scores import show_daily_scores_menu

    user = await require_writable(cb, db_user)
    if user is None:
        return
    await show_daily_scores_menu(cb, repo, user, state)

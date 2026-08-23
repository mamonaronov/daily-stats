"""Main navigation callbacks."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, require_writable, show_main
from keyboards.main import balance_kb, now_or_time, paid_kb, sleep_actions_kb
from utils.callbacks import (
    ENTRY_ACT,
    ENTRY_ALC,
    ENTRY_CAF,
    ENTRY_CIG,
    ENTRY_FOOL,
    ENTRY_SLEEP,
    ENTRY_SNUS,
    NAV_BALANCE,
    NAV_CANCEL,
    NAV_MAIN,
)
from states.diary import PaidSG
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
async def show_balance(cb: CallbackQuery, db_user: User | None, repo: Repo) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    user = await repo.get_user(user.telegram_id) or user
    text = (
        f"💰 <b>Баланс</b>\n\n"
        f"Сейчас: {money(user.balance)}\n"
        f"Стоимость: {money(user.daily_price)} / день\n"
        f"{balance_coverage_block(user)}\n\n"
        f"Пополнение выполняется владельцем сервиса вручную после оплаты вне бота."
    )
    await cb.answer()
    await safe_edit(cb.message, text, balance_kb())


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
    await safe_edit(cb.message, "😴 Сон", sleep_actions_kb(sleep))


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


@router.callback_query(F.data == ENTRY_ACT)
async def act_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable
    from keyboards.main import activity_types

    if await require_writable(cb, db_user) is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, "🏃 Какая активность?", activity_types())


@router.callback_query(F.data == "bal:paid")
async def paid_start(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await state.set_state(PaidSG.amount)
    await cb.answer()
    await safe_edit(cb.message, "Сумма перевода? Можно пропустить.", paid_kb())


@router.callback_query(F.data == "bal:paid:0")
async def paid_skip(
    cb: CallbackQuery,
    state: FSMContext,
    db_user: User | None,
    config: Config,
    bot: Bot,
    repo: Repo,
) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.clear()
    from services.paid import report_payment

    await report_payment(bot, config, user, None)
    user = await repo.get_user(user.telegram_id) or user
    await cb.answer("Сообщили владельцу")
    await safe_edit(
        cb.message,
        (
            f"💰 <b>Баланс</b>\n\n"
            f"Сейчас: {money(user.balance)}\n"
            f"Владелец получил заявку. Зачисление вручную, не сразу."
        ),
        balance_kb(),
    )


@router.message(PaidSG.amount)
async def paid_amount(
    message: Message,
    state: FSMContext,
    db_user: User | None,
    config: Config,
    bot: Bot,
    repo: Repo,
) -> None:
    user = await require_active(message, db_user)
    if user is None:
        return
    raw = (message.text or "").strip()
    await state.clear()
    from services.paid import report_payment

    await report_payment(bot, config, user, raw or None)
    user = await repo.get_user(user.telegram_id) or user
    await message.answer(
        "Сообщили владельцу. Зачисление вручную, баланс пока тот же.",
        reply_markup=balance_kb(),
    )

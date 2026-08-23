"""Caffeine logging."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import ask_when_after_amount, require_writable, show_main, start_time_pick
from keyboards.main import back_kb, drink_amount_kb, when_kb
from services import entries
from states.diary import AmountSG
from utils.callbacks import ENTRY_ALC, ENTRY_CAF
from utils.quantity import parse_drink_amount
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="caffeine")

CAF_AMOUNT_PROMPT = (
    "Сколько выпили?\n\n"
    "Нажмите объём или напишите, например: 250, 250 мл, 0.5л или 1 чашка.\n"
    "Число от 10 — миллилитры."
)
ALC_AMOUNT_HINT = "Введите объём, например 500, 500 мл, 0.5л или 1 порция."
CAF_AMOUNT_HINT = "Введите объём, например 250, 250 мл, 0.5л или 1."


@router.callback_query(F.data.startswith("caf:t:"))
async def caf_type(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    drink = cb.data.split(":")[2]
    await state.set_state(AmountSG.value)
    await state.update_data(drink_type=drink, amount_kind="caf")
    await cb.answer()
    await safe_edit(cb.message, CAF_AMOUNT_PROMPT, drink_amount_kb("caf", drink, ENTRY_CAF))


@router.callback_query(F.data.startswith("caf:q:"), AmountSG.value)
async def caf_amount_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    parts = cb.data.split(":")
    if parts[2] == "pcs":
        await state.update_data(amount=float(parts[3]), unit="шт")
    else:
        await state.update_data(amount=float(parts[2]), unit="мл")
    await ask_when_after_amount(cb, state)


@router.message(AmountSG.value)
async def amount_value(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    kind = data.get("amount_kind")
    back = ENTRY_CAF if kind == "caf" else ENTRY_ALC
    hint = CAF_AMOUNT_HINT if kind == "caf" else ALC_AMOUNT_HINT
    small = "count" if kind == "caf" else "liters"
    try:
        qty = parse_drink_amount(message.text or "", small_integer=small)
    except ValueError:
        await message.answer(hint, reply_markup=back_kb(back))
        return
    await state.update_data(amount=qty.amount, unit=qty.unit)
    prefix = "caft" if kind == "caf" else "alct"
    await message.answer("Когда это было?", reply_markup=when_kb(prefix))


@router.callback_query(F.data == "caft:now")
async def caf_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    _, error = await entries.add_caffeine(
        repo, user, data["drink_type"], data.get("amount"), data.get("unit"), user_now(user.timezone)
    )
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Сохранено")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data == "caft:time")
async def caf_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(
        cb,
        state,
        "caf",
        {
            "tz": user.timezone,
            "drink_type": data["drink_type"],
            "amount": data.get("amount"),
            "unit": data.get("unit"),
        },
    )

"""Alcohol logging."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import ask_when_after_amount, require_writable, show_main, start_time_pick
from keyboards.main import drink_amount_kb
from services import entries
from states.diary import AmountSG
from utils.callbacks import ENTRY_ALC
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="alcohol")

ALC_AMOUNT_PROMPT = (
    "Сколько выпили?\n\n"
    "Нажмите объём или напишите, например: 500, 500 мл, 0.5л, 0,33 л.\n"
    "Число от 10 — миллилитры, меньше 10 — литры. Можно 1 порция."
)


@router.callback_query(F.data.startswith("alc:t:"))
async def alc_type(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    drink = cb.data.split(":")[2]
    await state.set_state(AmountSG.value)
    await state.update_data(drink_type=drink, amount_kind="alc")
    await cb.answer()
    await safe_edit(cb.message, ALC_AMOUNT_PROMPT, drink_amount_kb("alc", drink, ENTRY_ALC))


@router.callback_query(F.data.startswith("alc:q:"), AmountSG.value)
async def alc_amount_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    parts = cb.data.split(":")
    if parts[2] == "pcs":
        await state.update_data(amount=float(parts[3]), unit="шт")
    else:
        await state.update_data(amount=float(parts[2]), unit="мл")
    await ask_when_after_amount(cb, state)


@router.callback_query(F.data == "alct:now")
async def alc_now(
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
    _, error = await entries.add_alcohol(
        repo, user, data["drink_type"], data.get("amount"), data.get("unit"), user_now(user.timezone)
    )
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Сохранено")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "alct:time")
async def alc_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(
        cb,
        state,
        "alc",
        {
            "tz": user.timezone,
            "drink_type": data["drink_type"],
            "amount": data.get("amount"),
            "unit": data.get("unit"),
        },
    )

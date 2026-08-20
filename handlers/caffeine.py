"""Caffeine logging."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main, start_time_pick
from keyboards.main import back_kb, when_kb
from services import entries
from states.diary import AmountSG
from utils.callbacks import ENTRY_ALC, ENTRY_CAF
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="caffeine")


@router.callback_query(F.data.startswith("caf:t:"))
async def caf_type(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    drink = cb.data.split(":")[2]
    await state.set_state(AmountSG.value)
    await state.update_data(drink_type=drink, amount_kind="caf")
    await cb.answer()
    await safe_edit(cb.message, "Количество (например 1 или 250). Единица — шт/мл.", back_kb(ENTRY_CAF))


@router.message(AmountSG.value)
async def amount_value(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    raw = (message.text or "").replace(",", ".").strip()
    data = await state.get_data()
    back = ENTRY_CAF if data.get("amount_kind") == "caf" else ENTRY_ALC
    try:
        amount = float(raw.split()[0])
    except (ValueError, IndexError):
        await message.answer("Введите число, например 1 или 200", reply_markup=back_kb(back))
        return
    unit = "шт"
    parts = raw.split()
    if len(parts) > 1:
        unit = parts[1][:12]
    await state.update_data(amount=amount, unit=unit)
    kind = data.get("amount_kind")
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
    await show_main(cb, user, config, is_owner, state)


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

"""Weight weigh-ins: timestamped measurements, any time of day."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import weight_value_kb, when_kb
from services import entries
from services.metric_types import parse_weight_kg
from states.diary import WeightSG
from utils.callbacks import ENTRY_WGT, NAV_MAIN
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="weight")

WEIGHT_PROMPT = "⚖️ Сколько кг?\nНапишите число, например 72,4."
WEIGHT_ERROR = "Введите вес в кг, например 72,4 (от 1 до 500)."


async def show_weight_prompt(event: CallbackQuery | Message, state: FSMContext, repo: Repo, user: User) -> None:
    recent = await repo.recent_weights(user.telegram_id)
    await state.set_state(WeightSG.value)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, WEIGHT_PROMPT, weight_value_kb(recent, NAV_MAIN))
        return
    await event.answer(WEIGHT_PROMPT, reply_markup=weight_value_kb(recent, NAV_MAIN))


async def _ask_when(event: CallbackQuery | Message, state: FSMContext, kilograms: float) -> None:
    await state.update_data(kilograms=kilograms)
    await state.set_state(None)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, "Когда взвесились?", when_kb("wgt"))
        return
    await event.answer("Когда взвесились?", reply_markup=when_kb("wgt"))


@router.callback_query(F.data.startswith("wgt:q:"), WeightSG.value)
async def weight_preset(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    try:
        kilograms = parse_weight_kg(cb.data.split(":", 2)[2])
    except ValueError:
        await cb.answer("Некорректное число", show_alert=True)
        return
    await _ask_when(cb, state, kilograms)


@router.message(WeightSG.value)
async def weight_typed(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    try:
        kilograms = parse_weight_kg(message.text or "")
    except ValueError:
        await message.answer(WEIGHT_ERROR, reply_markup=weight_value_kb(None, NAV_MAIN))
        return
    await _ask_when(message, state, kilograms)


@router.callback_query(F.data == "wgt:now")
async def weight_now(
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
    kilograms = data.get("kilograms")
    if kilograms is None:
        await cb.answer("Сначала укажите вес", show_alert=True)
        return
    item_id, error = await entries.add_weight(repo, user, float(kilograms), user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "wgt", item_id, state)


@router.callback_query(F.data == "wgt:time")
async def weight_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    kilograms = data.get("kilograms")
    if kilograms is None:
        await cb.answer("Сначала укажите вес", show_alert=True)
        return
    await start_time_pick(
        cb,
        state,
        "wgt",
        {"tz": user.timezone, "kilograms": kilograms},
    )

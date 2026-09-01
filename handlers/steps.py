"""Daily step count: one value per local day, upsert anytime."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from database.queries import Repo
from handlers.common import require_writable
from handlers.history import show_saved_entry
from keyboards.main import calendar_kb, steps_day_kb, steps_value_kb
from services import entries
from services.metric_types import parse_steps
from states.diary import StepsSG
from utils.callbacks import ENTRY_STP
from utils.formatting import format_int_spaces
from utils.telegram import safe_edit
from utils.time import format_date_long, parse_calendar_token, user_today

router = Router(name="steps")

STEPS_ERROR = "Введите целое число шагов, например 8000 или 8 000 (до 200 000)."


def _day_prompt(day: date, today: date, current: int | None) -> str:
    if day == today:
        label = f"сегодня ({format_date_long(day)})"
        extra = "День ещё идёт — можно записать сейчас и потом поменять."
    elif day == today - timedelta(days=1):
        label = f"вчера ({format_date_long(day)})"
        extra = "Можно поменять значение в любой момент."
    else:
        label = format_date_long(day)
        extra = "Можно поменять значение в любой момент."
    lines = [f"🚶 Шаги за {label}"]
    if current is not None:
        lines.append(f"Сейчас записано: {format_int_spaces(current)}. Новое значение заменит его.")
    else:
        lines.append(extra)
    lines.append("")
    lines.append("Напишите число или нажмите кнопку.")
    return "\n".join(lines)


async def _ask_value(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    day: date,
) -> None:
    rec = await repo.get_steps_by_day(user.telegram_id, day.isoformat())
    await state.set_state(StepsSG.value)
    await state.update_data(steps_day=day.isoformat())
    text = _day_prompt(day, user_today(user.timezone), rec.steps if rec else None)
    markup = steps_value_kb(ENTRY_STP)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, text, markup)
        return
    await event.answer(text, reply_markup=markup)


async def _save_steps(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    count: int,
) -> None:
    data = await state.get_data()
    raw_day = data.get("steps_day")
    if not raw_day:
        if isinstance(event, CallbackQuery):
            await event.answer("Сначала выберите день", show_alert=True)
        else:
            await event.answer("Сначала выберите день.")
        return
    day = date.fromisoformat(raw_day)
    item_id, error, updated = await entries.upsert_steps(repo, user, day, count)
    if error:
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    heading = "✅ Обновлено" if updated else "✅ Записано"
    toast = "Обновлено" if updated else "Записано"
    await show_saved_entry(event, repo, user, "stp", item_id, state, toast=toast, heading=heading)


async def show_steps_menu(cb: CallbackQuery, repo: Repo, user: User, state: FSMContext) -> None:
    await state.clear()
    today = user_today(user.timezone)
    yesterday = today - timedelta(days=1)
    today_rec = await repo.get_steps_by_day(user.telegram_id, today.isoformat())
    yest_rec = await repo.get_steps_by_day(user.telegram_id, yesterday.isoformat())
    await cb.answer()
    await safe_edit(
        cb.message,
        "🚶 Шаги за какой день?",
        steps_day_kb(
            today_steps=today_rec.steps if today_rec else None,
            yesterday_steps=yest_rec.steps if yest_rec else None,
        ),
    )


@router.callback_query(F.data == "stp:today")
async def steps_today(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _ask_value(cb, state, repo, user, user_today(user.timezone))


@router.callback_query(F.data == "stp:yest")
async def steps_yesterday(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _ask_value(cb, state, repo, user, user_today(user.timezone) - timedelta(days=1))


@router.callback_query(F.data == "stp:date")
async def steps_pick_date(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    today = user_today(user.timezone)
    await state.set_state(StepsSG.pick_date)
    await cb.answer()
    await safe_edit(cb.message, "Дата шагов:", calendar_kb(today.year, today.month, prefix="stpcal", back=ENTRY_STP))


@router.callback_query(F.data.startswith("stpcalm:"))
async def steps_month(cb: CallbackQuery, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    await cb.answer()
    await safe_edit(cb.message, "Дата шагов:", calendar_kb(year, month, prefix="stpcal", back=ENTRY_STP))


@router.callback_query(F.data.startswith("stpcal:"), StepsSG.pick_date)
async def steps_got_date(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    try:
        day = parse_calendar_token(token, user_today(user.timezone))
    except ValueError:
        await cb.answer()
        return
    await _ask_value(cb, state, repo, user, day)


@router.callback_query(F.data.startswith("stp:e:"))
async def steps_edit(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    rec = await repo.get_steps(int(cb.data.split(":")[2]), user.telegram_id)
    if rec is None:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    await _ask_value(cb, state, repo, user, date.fromisoformat(rec.day))


@router.callback_query(F.data.startswith("stp:q:"), StepsSG.value)
async def steps_preset(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    try:
        count = parse_steps(cb.data.split(":", 2)[2])
    except ValueError:
        await cb.answer("Некорректное число", show_alert=True)
        return
    await _save_steps(cb, state, repo, user, count)


@router.message(StepsSG.value)
async def steps_typed(message: Message, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    try:
        count = parse_steps(message.text or "")
    except ValueError:
        await message.answer(STEPS_ERROR, reply_markup=steps_value_kb(ENTRY_STP))
        return
    await _save_steps(message, state, repo, user, count)

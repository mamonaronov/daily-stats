"""Physical activity logging."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import activity_duration_kb, skip_comment_kb, when_kb
from services import entries
from states.diary import ActivitySG
from utils.callbacks import ENTRY_ACT
from utils.telegram import safe_edit
from utils.time import parse_minutes_ago, user_now

router = Router(name="activity")


def _activity_duration(raw: str) -> int:
    duration = parse_minutes_ago(raw)
    if duration <= 0 or duration > 24 * 60:
        raise ValueError("duration")
    return duration


@router.callback_query(F.data.startswith("act:t:"))
async def act_type(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.set_state(ActivitySG.duration)
    await state.update_data(activity_type=cb.data.split(":")[2])
    await cb.answer()
    await safe_edit(
        cb.message,
        "Длительность: нажмите или напишите, например 35, 1 час, 1ч 20м.",
        activity_duration_kb(ENTRY_ACT),
    )


@router.callback_query(F.data.startswith("act:d:"), ActivitySG.duration)
async def act_duration_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.update_data(duration=int(cb.data.split(":")[2]))
    await state.set_state(ActivitySG.comment)
    await cb.answer()
    await safe_edit(cb.message, "Комментарий? Можно пропустить.", skip_comment_kb(ENTRY_ACT))


@router.message(ActivitySG.duration)
async def act_duration(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    try:
        duration = _activity_duration(message.text or "")
    except ValueError:
        await message.answer(
            "Введите длительность, например 35, 90 мин, 1 час или 1ч 20м.",
            reply_markup=activity_duration_kb(ENTRY_ACT),
        )
        return
    await state.update_data(duration=duration)
    await state.set_state(ActivitySG.comment)
    await message.answer("Комментарий? Можно пропустить.", reply_markup=skip_comment_kb(ENTRY_ACT))


@router.callback_query(F.data == "wb:skip", ActivitySG.comment)
async def act_skip(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.update_data(comment=None)
    await cb.answer()
    await safe_edit(cb.message, "Когда была активность?", when_kb("actt"))


@router.message(ActivitySG.comment)
async def act_comment(message: Message, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(message, db_user) is None:
        return
    await state.update_data(comment=(message.text or "").strip() or None)
    await message.answer("Когда была активность?", reply_markup=when_kb("actt"))


@router.callback_query(F.data == "actt:now")
async def act_now(
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
    item_id, error = await entries.add_activity(
        repo,
        user,
        data["activity_type"],
        data.get("duration"),
        data.get("comment"),
        user_now(user.timezone),
    )
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "act", item_id, state)


@router.callback_query(F.data == "actt:time")
async def act_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(
        cb,
        state,
        "act",
        {
            "tz": user.timezone,
            "activity_type": data["activity_type"],
            "duration": data.get("duration"),
            "comment": data.get("comment"),
        },
    )

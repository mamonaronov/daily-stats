"""Mood logging."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main, start_time_pick
from keyboards.main import now_or_time
from services import entries
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="mood")


@router.callback_query(F.data.startswith("md:"))
async def mood_score(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    score = int(cb.data.split(":")[1])
    await state.update_data(score=score)
    await cb.answer()
    await safe_edit(cb.message, "Когда оценить настроение?", now_or_time("mdt"))


@router.callback_query(F.data == "mdt:now")
async def mood_now(
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
    _, error = await entries.add_mood(repo, user, int(data["score"]), user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Сохранено")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "mdt:time")
async def mood_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(cb, state, "mood", {"tz": user.timezone, "score": data["score"]})

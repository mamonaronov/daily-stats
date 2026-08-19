"""Fooling-around logging (instant timestamp, like cigarettes)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main, start_time_pick
from services import entries
from utils.time import user_now

router = Router(name="fooling")


@router.callback_query(F.data == "fool:now")
async def fool_now(
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
    _, error = await entries.add_fooling(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Записано")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "fool:time")
async def fool_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(cb, state, "fool", {"tz": user.timezone})

"""Cigarette logging."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, start_time_pick
from handlers.history import show_saved_entry
from services import entries
from utils.time import user_now

router = Router(name="cigarettes")


@router.callback_query(F.data == "cig:now")
async def cig_now(
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
    item_id, error = await entries.add_cigarette(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "cig", item_id, state)


@router.callback_query(F.data.startswith("more:cig:"))
async def cig_more(
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
    item_id, error = await entries.add_cigarette(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "cig", item_id, state)


@router.callback_query(F.data == "cig:time")
async def cig_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(cb, state, "cig", {"tz": user.timezone})

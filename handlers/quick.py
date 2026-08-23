"""Persistent reply keyboard: cigarette now, snus, sleep, more."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, require_writable, show_main
from handlers.history import show_saved_entry
from keyboards.main import REPLY_CIG, REPLY_MORE, REPLY_SLEEP, REPLY_SNUS, sleep_actions_kb
from services import entries
from utils.time import user_now

router = Router(name="quick")


@router.message(F.text == REPLY_CIG)
async def quick_cig(
    message: Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    item_id, error = await entries.add_cigarette(repo, user, user_now(user.timezone))
    if error:
        await message.answer(error)
        return
    await show_saved_entry(message, repo, user, "cig", item_id, state)


@router.message(F.text == REPLY_SNUS)
async def quick_snus(message: Message, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    from handlers.snus import show_snus_menu

    user = await require_writable(message, db_user)
    if user is None:
        return
    await state.clear()
    await show_snus_menu(message, repo, user)


@router.message(F.text == REPLY_SLEEP)
async def quick_sleep(message: Message, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    sleep = await repo.latest_sleep(user.telegram_id)
    await message.answer("😴 Сон", reply_markup=sleep_actions_kb(sleep))


@router.message(F.text == REPLY_MORE)
async def quick_more(
    message: Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    user = await require_active(message, db_user)
    if user is None:
        return
    await show_main(message, user, config, is_owner, state, repo)

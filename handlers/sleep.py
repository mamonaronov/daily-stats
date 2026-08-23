"""Sleep logging from the main menu row."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main, start_time_pick
from keyboards.main import score_kb, sleep_onset_kb
from services import entries
from states.diary import SleepSG
from utils.callbacks import NAV_MAIN
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="sleep")


async def _prompt_onset(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(cb.message, "Когда заснули?", sleep_onset_kb())


@router.callback_query(F.data == "slp:phone")
async def sleep_phone_in(
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
    _, error = await entries.add_sleep_phone_in(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Спокойной ночи")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data.in_({"slp:nophone", "slp:away"}))
async def sleep_phone_away(
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
    _, error = await entries.add_sleep_phone_away(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Телефон убран")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data == "slp:wake")
async def sleep_wake_quality(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.set_state(SleepSG.quality)
    await state.update_data(sleep_action="wake")
    await cb.answer()
    await safe_edit(cb.message, "Как спалось?", score_kb("slq", back=NAV_MAIN))


@router.callback_query(F.data == "slp:wakeup")
async def sleep_wake_up_quality(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.set_state(SleepSG.quality)
    await state.update_data(sleep_action="wake_up")
    await cb.answer()
    await safe_edit(cb.message, "Как спалось?", score_kb("slq", back=NAV_MAIN))


@router.callback_query(F.data.startswith("slq:"), SleepSG.quality)
async def sleep_quality(
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
    quality = int(cb.data.split(":")[1])
    data = await state.get_data()
    when = user_now(user.timezone)
    action = data.get("sleep_action")
    if action == "wake_up":
        _, error = await entries.add_sleep_wake_and_up(repo, user, when, quality)
        if error:
            await cb.answer(error, show_alert=True)
            return
        await cb.answer("Доброе утро")
        await _prompt_onset(cb, state)
        return
    _, error = await entries.add_sleep_wake(repo, user, when, quality)
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Доброе утро")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data == "slp:up")
async def sleep_up(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, error = await entries.add_sleep_up(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Встали")
    await _prompt_onset(cb, state)


@router.callback_query(F.data == "slp:later")
async def sleep_onset_later(
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
    await cb.answer("Можно указать позже")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data == "slp:onset")
async def sleep_onset_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(
        cb,
        state,
        "slp_onset",
        {"tz": user.timezone, "time_exit": "slp_onset"},
        skip_date=True,
    )

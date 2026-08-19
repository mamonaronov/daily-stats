"""Sleep logging."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main, start_time_pick
from keyboards.main import now_or_time, score_kb
from services import entries
from services.reminders import refresh_user_reminder
from states.diary import SleepSG
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="sleep")


async def _save_wake(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    config: Config,
    user: User,
    is_owner: bool,
    when: datetime,
    quality: int | None,
) -> None:
    _, error = await entries.add_sleep_wake(repo, user, when, quality)
    await refresh_user_reminder(repo, user, config)
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Доброе утро")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "slp:bed")
async def sleep_bed_now(
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
    _, error = await entries.add_sleep_bed(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await refresh_user_reminder(repo, user, config)
    await cb.answer("Спокойной ночи")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "slp:wake")
async def sleep_wake_quality(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.set_state(SleepSG.quality)
    await state.update_data(sleep_action="wake")
    await cb.answer()
    await safe_edit(cb.message, "Как спалось?", score_kb("slq"))


@router.callback_query(F.data.startswith("slq:"), SleepSG.quality)
async def sleep_quality(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    quality = int(cb.data.split(":")[1])
    data = await state.get_data()
    if data.get("sleep_action") == "wake_time":
        await start_time_pick(
            cb,
            state,
            "slp_wake",
            {"tz": user.timezone, "quality": quality},
            skip_date=True,
        )
        return
    await state.set_state(SleepSG.when)
    await state.update_data(quality=quality)
    await cb.answer()
    await safe_edit(cb.message, "Когда проснулись? Можно указать время задним числом.", now_or_time("slw"))


@router.callback_query(F.data == "slw:now", SleepSG.when)
async def sleep_wake_now(
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
    await _save_wake(cb, state, repo, config, user, is_owner, user_now(user.timezone), data.get("quality"))


@router.callback_query(F.data == "slw:time", SleepSG.when)
async def sleep_wake_pick_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(
        cb,
        state,
        "slp_wake",
        {"tz": user.timezone, "quality": data.get("quality")},
        skip_date=True,
    )


@router.callback_query(F.data == "slp:time")
async def sleep_choose_kind(cb: CallbackQuery, db_user: User | None) -> None:
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from keyboards.main import _btn, with_nav

    if await require_writable(cb, db_user) is None:
        return
    b = InlineKeyboardBuilder()
    b.row(_btn("🌙 Отход ко сну", "slp:tbed"), _btn("☀️ Пробуждение", "slp:twake"))
    await cb.answer()
    await safe_edit(cb.message, "Что указать вручную?", with_nav(b))


@router.callback_query(F.data == "slp:tbed")
async def sleep_time_bed(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(cb, state, "slp_bed", {"tz": user.timezone})


@router.callback_query(F.data == "slp:twake")
async def sleep_time_wake(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.set_state(SleepSG.quality)
    await state.update_data(sleep_action="wake_time")
    await cb.answer()
    await safe_edit(cb.message, "Качество сна?", score_kb("slq"))


@router.callback_query(F.data == "slp:now")
async def sleep_now_hint(cb: CallbackQuery, db_user: User | None) -> None:
    from keyboards.main import sleep_menu

    if await require_writable(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "Выберите: лёг спать или проснулся.", sleep_menu())

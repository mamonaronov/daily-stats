"""Sleep logging from the main menu row."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import score_kb, sleep_onset_kb, when_kb, when_title
from services import entries
from states.diary import SleepSG
from utils.callbacks import NAV_MAIN
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="sleep")


def _onset_extra(user: User, data: dict) -> dict:
    extra = {"tz": user.timezone, "time_exit": "slp_onset"}
    if data.get("onset_undo_kind") is not None and data.get("onset_undo_id") is not None:
        extra["onset_undo_kind"] = data["onset_undo_kind"]
        extra["onset_undo_id"] = data["onset_undo_id"]
    return extra


async def _prompt_onset(
    event: CallbackQuery | Message,
    state: FSMContext,
    undo_kind: str,
    undo_id: int,
) -> None:
    await state.clear()
    await state.update_data(onset_undo_kind=undo_kind, onset_undo_id=undo_id)
    text = "Когда заснули?"
    markup = sleep_onset_kb(undo_kind, undo_id)
    if isinstance(event, CallbackQuery):
        await safe_edit(event.message, text, markup)
        return
    await event.answer(text, reply_markup=markup)


async def _ask_quality(cb: CallbackQuery, state: FSMContext, action: str) -> None:
    await state.set_state(SleepSG.quality)
    await state.update_data(sleep_action=action)
    await cb.answer()
    await safe_edit(cb.message, "Как спалось?", score_kb("slq", back=NAV_MAIN))


async def _ask_wake_when(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SleepSG.when)
    await cb.answer()
    await safe_edit(cb.message, when_title("slw"), when_kb("slw"))


async def _fail(event: CallbackQuery | Message, error: str) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer(error, show_alert=True)
        return
    await event.answer(error)


async def _maybe_prompt_onset(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    item_id: int | None,
    undo_kind: str,
    *,
    toast: str,
) -> None:
    rec = await repo.get_sleep(item_id, user.telegram_id) if item_id else None
    if rec is not None and rec.sleep_onset_at is None:
        if isinstance(event, CallbackQuery):
            await event.answer(toast)
        await _prompt_onset(event, state, undo_kind, rec.id)
        return
    await show_saved_entry(event, repo, user, undo_kind, item_id, state, toast=toast)


async def complete_sleep_wake(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    when: datetime,
) -> None:
    data = await state.get_data()
    quality = data.get("sleep_quality")
    if quality is None:
        await _fail(event, "Сначала оцените, как спалось.")
        return
    action = data.get("sleep_action") or "wake"
    if action == "wake_up":
        item_id, error = await entries.add_sleep_wake_and_up(repo, user, when, int(quality))
        undo_kind = "wu"
    else:
        item_id, error = await entries.add_sleep_wake(repo, user, when, int(quality))
        undo_kind = "sw"
    if error:
        await _fail(event, error)
        return
    await _maybe_prompt_onset(event, state, repo, user, item_id, undo_kind, toast="Доброе утро")


async def complete_sleep_up(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    when: datetime,
) -> None:
    item_id, error = await entries.add_sleep_up(repo, user, when)
    if error:
        await _fail(event, error)
        return
    await _maybe_prompt_onset(event, state, repo, user, item_id, "su", toast="Встали")


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
    item_id, error = await entries.add_sleep_phone_in(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "sp", item_id, state, toast="Спокойной ночи")


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
    item_id, error = await entries.add_sleep_phone_away(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "sa", item_id, state, toast="Телефон убран")


@router.callback_query(F.data == "slp:wake")
async def sleep_wake_quality(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.clear()
    await _ask_quality(cb, state, "wake")


@router.callback_query(F.data == "slp:wakeup")
async def sleep_wake_up_quality(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.clear()
    await _ask_quality(cb, state, "wake_up")


@router.callback_query(F.data == "slp:ql")
async def sleep_quality_back(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await _ask_quality(cb, state, data.get("sleep_action") or "wake")


@router.callback_query(F.data.startswith("slq:"), SleepSG.quality)
async def sleep_quality(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.update_data(sleep_quality=int(cb.data.split(":")[1]))
    await _ask_wake_when(cb, state)


@router.callback_query(F.data == "slw:now")
async def sleep_wake_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await complete_sleep_wake(cb, state, repo, user, user_now(user.timezone))


@router.callback_query(F.data == "slw:time")
async def sleep_wake_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(
        cb,
        state,
        "slp_wake",
        {
            "tz": user.timezone,
            "sleep_action": data.get("sleep_action"),
            "sleep_quality": data.get("sleep_quality"),
            "time_exit": "when:slw",
        },
        skip_date=True,
    )


@router.callback_query(F.data == "slp:up")
async def sleep_up_when(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.clear()
    await state.set_state(SleepSG.when)
    await cb.answer()
    await safe_edit(cb.message, when_title("slu"), when_kb("slu"))


@router.callback_query(F.data == "slu:now")
async def sleep_up_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await complete_sleep_up(cb, state, repo, user, user_now(user.timezone))


@router.callback_query(F.data == "slu:time")
async def sleep_up_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(
        cb,
        state,
        "slp_up",
        {"tz": user.timezone, "time_exit": "when:slu"},
        skip_date=True,
    )


@router.callback_query(F.data == "slp:askonset")
async def sleep_ask_onset(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    data = await state.get_data()
    await cb.answer()
    await safe_edit(
        cb.message,
        "Когда заснули?",
        sleep_onset_kb(data.get("onset_undo_kind"), data.get("onset_undo_id")),
    )


@router.callback_query(F.data.startswith("slp:later"))
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
    parts = cb.data.split(":")
    if len(parts) >= 4:
        await show_saved_entry(
            cb,
            repo,
            user,
            parts[2],
            int(parts[3]),
            state,
            toast="Можно указать позже",
        )
        return
    from handlers.common import show_main

    await cb.answer("Можно указать позже")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data.in_({"slp:onset", "slo:time"}))
async def sleep_onset_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(cb, state, "slp_onset", _onset_extra(user, await state.get_data()), skip_date=True)


@router.callback_query(F.data == "slo:now")
async def sleep_onset_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    item_id, error = await entries.add_sleep_onset(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "so", item_id, state, toast="Заснули")

"""Sleep logging from the main menu row."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import SleepRecord, User
from database.queries import Repo
from handlers.common import require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import score_kb, sleep_onset_kb, when_kb, when_title
from services import entries
from states.diary import SleepSG
from utils.callbacks import NAV_MAIN
from utils.telegram import safe_edit
from utils.time import format_dt, parse_iso, user_now

router = Router(name="sleep")


def bed_times_hint(user: User, rec: SleepRecord | None) -> str:
    if rec is None:
        return ""
    lines: list[str] = []
    tz = user.timezone
    if rec.phone_in_bed_at:
        lines.append(f"Лёг с телефоном: {format_dt(parse_iso(rec.phone_in_bed_at), tz)}")
    if rec.phone_away_at:
        label = "Без телефона" if rec.phone_in_bed_at else "Лёг без телефона"
        lines.append(f"{label}: {format_dt(parse_iso(rec.phone_away_at), tz)}")
    elif rec.bedtime and not rec.phone_in_bed_at:
        lines.append(f"Лёг спать: {format_dt(parse_iso(rec.bedtime), tz)}")
    return "\n".join(lines)


def onset_prompt_text(user: User, rec: SleepRecord | None) -> str:
    hint = bed_times_hint(user, rec)
    if not hint:
        return "Когда заснули?"
    return f"Когда заснули?\n\n{hint}"


def _onset_extra(user: User, data: dict) -> dict:
    extra = {"tz": user.timezone, "time_exit": "slp_onset"}
    if data.get("onset_undo_kind") is not None and data.get("onset_undo_id") is not None:
        extra["onset_undo_kind"] = data["onset_undo_kind"]
        extra["onset_undo_id"] = data["onset_undo_id"]
    if data.get("time_hint"):
        extra["time_hint"] = data["time_hint"]
    if data.get("onset_prompt"):
        extra["onset_prompt"] = data["onset_prompt"]
    return extra


async def _onset_record(repo: Repo, user: User, data: dict) -> SleepRecord | None:
    undo_id = data.get("onset_undo_id")
    if undo_id is not None:
        rec = await repo.get_sleep(int(undo_id), user.telegram_id)
        if rec is not None:
            return rec
    return await repo.latest_sleep(user.telegram_id)


async def _store_onset_prompt(
    state: FSMContext,
    user: User,
    rec: SleepRecord | None,
    *,
    undo_kind: str | None,
    undo_id: int | None,
) -> str:
    text = onset_prompt_text(user, rec)
    await state.update_data(
        onset_undo_kind=undo_kind,
        onset_undo_id=undo_id,
        onset_prompt=text,
        time_hint=bed_times_hint(user, rec),
    )
    return text


async def _prompt_onset(
    event: CallbackQuery | Message,
    state: FSMContext,
    undo_kind: str,
    undo_id: int,
    user: User,
    rec: SleepRecord | None,
) -> None:
    await state.clear()
    text = await _store_onset_prompt(state, user, rec, undo_kind=undo_kind, undo_id=undo_id)
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
        await _prompt_onset(event, state, undo_kind, rec.id, user, rec)
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


def _bed_prefix(action: str | None) -> str:
    return "sln" if action == "nophone" else "slb"


async def _ask_bed_when(cb: CallbackQuery, state: FSMContext, action: str) -> None:
    await state.set_state(SleepSG.when)
    await state.update_data(sleep_action=action)
    prefix = _bed_prefix(action)
    await cb.answer()
    await safe_edit(cb.message, when_title(prefix), when_kb(prefix))


async def complete_sleep_bed(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    when: datetime,
) -> None:
    data = await state.get_data()
    action = data.get("sleep_action")
    if action is None and data.get("when_prefix") == "sln":
        action = "nophone"
    if action == "nophone":
        item_id, error = await entries.add_sleep_phone_away(repo, user, when)
        kind, toast = "sa", "Спокойной ночи"
    else:
        item_id, error = await entries.add_sleep_phone_in(repo, user, when)
        kind, toast = "sp", "Спокойной ночи"
    if error:
        await _fail(event, error)
        return
    rec = await repo.get_sleep(item_id, user.telegram_id) if item_id else None
    if rec is not None and rec.wake_time is not None:
        await _maybe_prompt_onset(event, state, repo, user, item_id, kind, toast=toast)
        return
    await show_saved_entry(event, repo, user, kind, item_id, state, toast=toast)


@router.callback_query(F.data == "slp:phone")
async def sleep_phone_when(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.clear()
    await _ask_bed_when(cb, state, "phone")


@router.callback_query(F.data == "slp:nophone")
async def sleep_nophone_when(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.clear()
    await _ask_bed_when(cb, state, "nophone")


@router.callback_query(F.data == "slp:away")
async def sleep_phone_away(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    item_id, error = await entries.add_sleep_phone_away(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "sa", item_id, state, toast="Телефон убран")


@router.callback_query(F.data.in_({"slb:now", "sln:now"}))
async def sleep_bed_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    prefix = cb.data.split(":", 1)[0]
    await state.update_data(when_prefix=prefix, sleep_action="nophone" if prefix == "sln" else "phone")
    await complete_sleep_bed(cb, state, repo, user, user_now(user.timezone))


@router.callback_query(F.data.in_({"slb:time", "sln:time"}))
async def sleep_bed_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    prefix = cb.data.split(":", 1)[0]
    data = await state.get_data()
    action = data.get("sleep_action") or ("nophone" if prefix == "sln" else "phone")
    await start_time_pick(
        cb,
        state,
        "slp_bed",
        {
            "tz": user.timezone,
            "sleep_action": action,
            "when_prefix": prefix,
            "time_exit": f"when:{prefix}",
        },
        skip_date=True,
    )


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
async def sleep_ask_onset(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    undo_kind = data.get("onset_undo_kind")
    undo_id = data.get("onset_undo_id")
    rec = await _onset_record(repo, user, data)
    text = await _store_onset_prompt(state, user, rec, undo_kind=undo_kind, undo_id=undo_id)
    await cb.answer()
    await safe_edit(cb.message, text, sleep_onset_kb(undo_kind, undo_id))


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
async def sleep_onset_pick(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    extra = _onset_extra(user, await state.get_data())
    rec = await _onset_record(repo, user, extra)
    extra["time_hint"] = bed_times_hint(user, rec)
    extra["onset_prompt"] = onset_prompt_text(user, rec)
    await start_time_pick(cb, state, "slp_onset", extra, skip_date=True)


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

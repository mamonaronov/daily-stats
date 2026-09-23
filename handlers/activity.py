"""Physical activity logging: intervals for walk and run, durations for the rest."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from database.queries import Repo
from handlers.common import require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import (
    activity_duration_kb,
    activity_interval_kb,
    activity_types,
    skip_comment_kb,
    when_kb,
    when_title,
)
from services import entries
from services.activities import ACTIVITIES, activity_screen_text, spec_of
from services.ui_prefs import prefs_of
from states.diary import ActivitySG
from utils.callbacks import ENTRY_ACT, NAV_MAIN
from utils.telegram import safe_edit
from utils.time import parse_iso, parse_minutes_ago, to_iso, user_now

router = Router(name="activity")

UNAVAILABLE = "Сначала выберите активность."


def _activity_duration(raw: str) -> int:
    duration = parse_minutes_ago(raw)
    if duration <= 0 or duration > 24 * 60:
        raise ValueError("duration")
    return duration


def activity_when_screen(prefix: str, data: dict):
    if prefix not in {"acs", "ace", "actt"}:
        return None
    text = data.get("act_prompt") or when_title(prefix)
    key = data.get("activity_type")
    if prefix == "actt" and key:
        back = f"act:bd:{key}"
    elif key:
        back = f"act:o:{key}"
    else:
        back = NAV_MAIN
    return text, when_kb(prefix, back=back)


async def _show_screen(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    key: str,
    *,
    later: bool = False,
) -> None:
    spec = spec_of(key)
    if spec is None:
        if isinstance(event, CallbackQuery):
            await event.answer("Неизвестная активность", show_alert=True)
        else:
            await event.answer("Неизвестная активность.")
        return
    await state.clear()
    if spec.interval:
        open_rec = await repo.get_open_activity(user.telegram_id, spec.key)
        text = activity_screen_text(spec, open_rec, user.timezone)
        markup = activity_interval_kb(spec.key, open_session=open_rec is not None)
    else:
        await state.set_state(ActivitySG.duration)
        await state.update_data(activity_type=spec.key, defer_time=later, comment=None)
        text = activity_screen_text(spec, None, user.timezone, later=later)
        markup = activity_duration_kb(NAV_MAIN, later=later)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, text, markup)
        return
    await event.answer(text, reply_markup=markup)


async def _begin_interval(
    cb: CallbackQuery,
    state: FSMContext,
    user: User,
    key: str,
    action: str,
) -> None:
    spec = spec_of(key)
    if spec is None or not spec.interval:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    if action == "end":
        prompt = f"Когда закончили «{spec.label}»?"
        prefix = "ace"
    elif action == "complete":
        prompt = f"Когда начали «{spec.label}»? Потом отметите, когда закончили."
        prefix = "acs"
    else:
        prompt = f"Когда начали «{spec.label}»?"
        prefix = "acs"
    await state.update_data(
        activity_type=spec.key,
        act_action=action,
        act_start=None,
        act_prompt=prompt,
        tz=user.timezone,
    )
    await cb.answer()
    await safe_edit(cb.message, prompt, when_kb(prefix, back=f"act:o:{spec.key}"))


async def finish_activity_start(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    when: datetime,
) -> None:
    data = await state.get_data()
    key = data.get("activity_type")
    spec = spec_of(key or "")
    if spec is None:
        if isinstance(event, CallbackQuery):
            await event.answer(UNAVAILABLE, show_alert=True)
        else:
            await event.answer(UNAVAILABLE)
        return
    if data.get("act_action") == "complete":
        prompt = f"Когда закончили «{spec.label}»?"
        await state.update_data(act_start=to_iso(when), act_action="complete_end", act_prompt=prompt)
        markup = when_kb("ace", back=f"act:o:{spec.key}")
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit(event.message, prompt, markup)
            return
        await event.answer(prompt, reply_markup=markup)
        return
    item_id, error = await entries.start_activity(repo, user, spec.key, when)
    if error:
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    await show_saved_entry(event, repo, user, "act", item_id, state)


async def finish_activity_end(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    when: datetime,
) -> None:
    data = await state.get_data()
    key = data.get("activity_type")
    spec = spec_of(key or "")
    if spec is None:
        if isinstance(event, CallbackQuery):
            await event.answer(UNAVAILABLE, show_alert=True)
        else:
            await event.answer(UNAVAILABLE)
        return
    start_at = None
    if data.get("act_action") == "complete_end" and data.get("act_start"):
        start_at = parse_iso(data["act_start"])
    item_id, error, kind = await entries.end_activity(
        repo, user, spec.key, when, start_at=start_at
    )
    if error:
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    await show_saved_entry(event, repo, user, kind, item_id, state)


async def _save_duration_now(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    duration: int,
) -> None:
    data = await state.get_data()
    key = data.get("activity_type")
    if not key:
        if isinstance(event, CallbackQuery):
            await event.answer(UNAVAILABLE, show_alert=True)
        else:
            await event.answer(UNAVAILABLE)
        return
    if data.get("defer_time"):
        spec = spec_of(key)
        label = spec.label if spec else "активность"
        prompt = f"Когда была «{label}»?"
        await state.update_data(duration=duration, comment=None, act_prompt=prompt)
        markup = when_kb("actt", back=f"act:bd:{key}")
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit(event.message, prompt, markup)
            return
        await event.answer(prompt, reply_markup=markup)
        return
    item_id, error = await entries.add_activity(
        repo, user, key, duration, data.get("comment"), user_now(user.timezone)
    )
    if error:
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    await show_saved_entry(event, repo, user, "act", item_id, state)


def _key_from(data: str) -> str:
    return data.split(":")[2]


@router.callback_query(F.data == ENTRY_ACT)
async def act_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.clear()
    tracked = prefs_of(user).tracked
    keys = {spec.key for spec in ACTIVITIES if spec.key in tracked}
    await cb.answer()
    await safe_edit(
        cb.message,
        "Какая активность?",
        activity_types(keys or None),
    )


@router.callback_query(F.data.startswith("act:o:"))
@router.callback_query(F.data.startswith("act:t:"))
async def open_activity(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _show_screen(cb, state, repo, user, _key_from(cb.data))


@router.callback_query(F.data.startswith("act:bd:"))
async def duration_back(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _show_screen(cb, state, repo, user, _key_from(cb.data), later=True)


@router.callback_query(F.data.startswith("act:st:"))
async def activity_start(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    key = _key_from(cb.data)
    if await repo.get_open_activity(user.telegram_id, key):
        await cb.answer("Уже идёт — сначала закончите.", show_alert=True)
        return
    await _begin_interval(cb, state, user, key, "start")


@router.callback_query(F.data.startswith("act:en:"))
async def activity_end(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    key = _key_from(cb.data)
    open_rec = await repo.get_open_activity(user.telegram_id, key)
    await _begin_interval(cb, state, user, key, "end" if open_rec else "complete")


@router.callback_query(F.data == "acs:now")
async def activity_start_now(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    if "activity_type" not in await state.get_data():
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await finish_activity_start(cb, state, repo, user, user_now(user.timezone))


@router.callback_query(F.data == "ace:now")
async def activity_end_now(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    if "activity_type" not in await state.get_data():
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await finish_activity_end(cb, state, repo, user, user_now(user.timezone))


@router.callback_query(F.data == "act:later", ActivitySG.duration)
async def activity_later(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    spec = spec_of(data.get("activity_type") or "")
    if spec is None:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    later = not bool(data.get("defer_time"))
    await state.update_data(defer_time=later)
    await cb.answer()
    await safe_edit(
        cb.message,
        activity_screen_text(spec, None, user.timezone, later=later),
        activity_duration_kb(NAV_MAIN, later=later),
    )


@router.callback_query(F.data.startswith("act:d:"), ActivitySG.duration)
async def act_duration_pick(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _save_duration_now(cb, state, repo, user, int(cb.data.split(":")[2]))


@router.message(ActivitySG.duration)
async def act_duration(message: Message, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    try:
        duration = _activity_duration(message.text or "")
    except ValueError:
        later = bool(data.get("defer_time"))
        await message.answer(
            "Введите длительность, например 35, 90 мин, 1 час или 1ч 20м.",
            reply_markup=activity_duration_kb(NAV_MAIN, later=later),
        )
        return
    await _save_duration_now(message, state, repo, user, duration)


@router.callback_query(F.data == "actt:now")
async def act_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    if "activity_type" not in data:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
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
    if "activity_type" not in data:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    key = data["activity_type"]
    await start_time_pick(
        cb,
        state,
        "act",
        {
            "tz": user.timezone,
            "activity_type": key,
            "duration": data.get("duration"),
            "comment": data.get("comment"),
            "act_prompt": data.get("act_prompt"),
            "time_exit": "when:actt",
        },
    )


@router.callback_query(F.data.startswith("act:cmt:"))
async def act_comment_start(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.set_state(ActivitySG.comment)
    await state.update_data(act_comment_id=int(cb.data.split(":")[2]))
    await cb.answer()
    await safe_edit(cb.message, "Комментарий к активности:", skip_comment_kb(NAV_MAIN))


@router.callback_query(F.data == "wb:skip", ActivitySG.comment)
async def act_skip(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    if data.get("act_comment_id"):
        await show_saved_entry(cb, repo, user, "act", int(data["act_comment_id"]), state)
        return
    await state.update_data(comment=None)
    await cb.answer()
    await safe_edit(cb.message, "Когда была активность?", when_kb("actt"))


@router.message(ActivitySG.comment)
async def act_comment(message: Message, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    comment = (message.text or "").strip() or None
    if data.get("act_comment_id"):
        await repo.update_activity(int(data["act_comment_id"]), user.telegram_id, comment=comment)
        await show_saved_entry(message, repo, user, "act", int(data["act_comment_id"]), state)
        return
    await state.update_data(comment=comment)
    await message.answer("Когда была активность?", reply_markup=when_kb("actt"))

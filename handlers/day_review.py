"""Manual end-of-day mood and wellbeing review."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main
from keyboards.main import score_kb, skip_comment_kb
from services import entries
from services.users import can_write
from states.diary import DayReviewSG
from utils.callbacks import NAV_DAY
from utils.telegram import safe_edit
from utils.time import day_bounds_utc, to_iso, user_now, user_today

router = Router(name="day_review")


async def _today_records(repo: Repo, user: User):
    start, end = day_bounds_utc(user.timezone, user_today(user.timezone))
    moods = await repo.list_mood(user.telegram_id, to_iso(start), to_iso(end))
    wbs = await repo.list_wellbeing(user.telegram_id, to_iso(start), to_iso(end))
    return moods, wbs


@router.callback_query(F.data == NAV_DAY)
async def day_root(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    moods, wbs = await _today_records(repo, user)
    extra = ""
    if moods or wbs:
        extra = "\n\nЗа сегодня уже есть оценки. Можно добавить ещё одну."
    await state.set_state(DayReviewSG.mood)
    await cb.answer()
    await safe_edit(cb.message, "🌙 Оценка дня.\nСначала настроение." + extra, score_kb("drv_m"))


@router.callback_query(F.data.startswith("drv_m:"), DayReviewSG.mood)
async def day_mood(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.update_data(score_mood=int(cb.data.split(":")[1]))
    await state.set_state(DayReviewSG.wellbeing)
    await cb.answer()
    await safe_edit(cb.message, "Теперь самочувствие:", score_kb("drv_w"))


@router.callback_query(F.data.startswith("drv_w:"), DayReviewSG.wellbeing)
async def day_wb(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.update_data(score_wb=int(cb.data.split(":")[1]))
    await state.set_state(DayReviewSG.comment)
    await cb.answer()
    await safe_edit(cb.message, "Короткий комментарий к дню? Можно пропустить.", skip_comment_kb())


@router.callback_query(F.data == "wb:skip", DayReviewSG.comment)
async def day_skip(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    await _save_review(cb, state, repo, config, db_user, is_owner, None)


@router.message(DayReviewSG.comment)
async def day_comment(
    message: Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    await _save_review(message, state, repo, config, db_user, is_owner, (message.text or "").strip())


async def _save_review(event, state, repo, config, db_user, is_owner, comment) -> None:
    user = await require_writable(event, db_user)
    if user is None:
        return
    data = await state.get_data()
    when = user_now(user.timezone)
    _, err1 = await entries.add_mood(repo, user, int(data["score_mood"]), when)
    _, err2 = await entries.add_wellbeing(repo, user, int(data["score_wb"]), comment, when)
    error = err1 or err2
    await state.clear()
    if isinstance(event, CallbackQuery):
        if error:
            await event.answer(error, show_alert=True)
            return
        await event.answer("День оценён")
        await show_main(event, user, config, is_owner, state)
    else:
        if error:
            await event.answer(error)
            return
        await event.answer("День оценён")
        await show_main(event, user, config, is_owner, state)

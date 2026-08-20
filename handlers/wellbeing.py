"""Wellbeing logging."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main, start_time_pick
from keyboards.main import skip_comment_kb, when_kb
from services import entries
from states.diary import WellbeingSG
from utils.callbacks import ENTRY_WB
from utils.telegram import safe_edit
from utils.time import user_now

router = Router(name="wellbeing")


@router.callback_query(F.data.startswith("wb:"), F.data != "wb:skip")
async def wb_score(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if not cb.data.startswith("wb:") or cb.data.count(":") != 1:
        return
    token = cb.data.split(":")[1]
    if not token.isdigit():
        return
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await state.set_state(WellbeingSG.comment)
    await state.update_data(score=int(token))
    await cb.answer()
    await safe_edit(cb.message, "Комментарий? Можно пропустить.", skip_comment_kb(ENTRY_WB))


@router.callback_query(F.data == "wb:skip", WellbeingSG.comment)
async def wb_skip_comment(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.update_data(comment=None)
    await cb.answer()
    await safe_edit(cb.message, "Когда оценить самочувствие?", when_kb("wbt"))


@router.message(WellbeingSG.comment)
async def wb_comment(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    await state.update_data(comment=(message.text or "").strip() or None)
    await message.answer("Когда оценить самочувствие?", reply_markup=when_kb("wbt"))


@router.callback_query(F.data == "wbt:now")
async def wb_now(
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
    _, error = await entries.add_wellbeing(
        repo, user, int(data["score"]), data.get("comment"), user_now(user.timezone)
    )
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Сохранено")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "wbt:time")
async def wb_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(
        cb,
        state,
        "wb",
        {"tz": user.timezone, "score": data["score"], "comment": data.get("comment")},
    )

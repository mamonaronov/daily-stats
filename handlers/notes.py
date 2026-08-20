"""Free-form notes."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main, start_time_pick
from keyboards.main import back_kb, when_kb
from services import entries
from states.diary import NoteSG
from utils.time import user_now

router = Router(name="notes")


@router.message(NoteSG.text)
async def note_text(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    body = (message.text or "").strip()
    if not body:
        await message.answer("Заметка пустая. Напишите текст.", reply_markup=back_kb())
        return
    await state.update_data(body=body)
    await message.answer("Когда добавить заметку?", reply_markup=when_kb("nt"))


@router.callback_query(F.data == "nt:now")
async def note_now(
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
    _, error = await entries.add_note(repo, user, data["body"], user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Сохранено")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "nt:time")
async def note_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(cb, state, "note", {"tz": user.timezone, "body": data["body"]})

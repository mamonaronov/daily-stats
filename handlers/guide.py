"""In-bot user guide."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from handlers.common import require_active
from keyboards.main import guide_index_kb, guide_page_kb
from services.guide import INDEX_TEXT, page_text
from utils.callbacks import NAV_GUIDE
from utils.telegram import safe_edit

router = Router(name="guide")


async def _show_index(event: CallbackQuery | Message, state: FSMContext) -> None:
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, INDEX_TEXT, guide_index_kb())
        return
    await event.answer(INDEX_TEXT, reply_markup=guide_index_kb())


@router.message(Command("guide"))
@router.message(Command("help"))
async def cmd_guide(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(message, db_user)
    if user is None:
        return
    await _show_index(message, state)


@router.callback_query(F.data == NAV_GUIDE)
async def guide_index(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await _show_index(cb, state)


@router.callback_query(F.data.startswith("g:"))
async def guide_page(cb: CallbackQuery, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    topic = cb.data.split(":", 1)[1]
    text = page_text(topic)
    if text is None:
        await cb.answer("Нет такого раздела", show_alert=True)
        return
    await cb.answer()
    await safe_edit(cb.message, text, guide_page_kb())

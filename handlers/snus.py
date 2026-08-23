"""Snus can lifetime: bought → finished → duration in stats."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import snus_menu
from services import entries
from services.entries import _elapsed_minutes
from utils.formatting import duration_human
from utils.telegram import safe_edit
from utils.time import format_dt, parse_iso, to_iso, user_now

router = Router(name="snus")


async def show_snus_menu(target: CallbackQuery | Message, repo: Repo, user: User) -> None:
    open_pack = await repo.oldest_open_snus(user.telegram_id)
    open_count = await repo.count_open_snus(user.telegram_id)
    if open_pack and open_pack.bought_at:
        bought = format_dt(parse_iso(open_pack.bought_at), user.timezone)
        elapsed = _elapsed_minutes(open_pack.bought_at, to_iso(user_now(user.timezone)))
        extra = f"Открыта с {bought} · идёт {duration_human(elapsed)}"
        if open_count > 1:
            extra += f"\nОткрытых шайб: {open_count}"
    else:
        extra = "Открытой шайбы нет. Отметьте покупку, потом — когда закончится."
    text = f"🟢 Снюс\n\n{extra}"
    markup = snus_menu()
    if isinstance(target, CallbackQuery):
        await target.answer()
        await safe_edit(target.message, text, markup)
        return
    await target.answer(text, reply_markup=markup)


@router.callback_query(F.data == "sns:buy")
async def snus_buy_now(
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
    item_id, error = await entries.add_snus_bought(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "snb", item_id, state, toast="Шайба открыта")


@router.callback_query(F.data == "sns:end")
async def snus_end_now(
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
    item_id, error = await entries.add_snus_finished(repo, user, user_now(user.timezone))
    if error:
        await cb.answer(error, show_alert=True)
        return
    pack = await repo.get_snus_pack(item_id, user.telegram_id) if item_id else None
    lasted = duration_human(pack.duration_minutes) if pack else "—"
    await show_saved_entry(
        cb,
        repo,
        user,
        "snf",
        item_id,
        state,
        toast=f"Хватило на {lasted}",
        heading=f"✅ Хватило на {lasted}",
    )


@router.callback_query(F.data == "sns:tbuy")
async def snus_buy_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(cb, state, "snus_buy", {"tz": user.timezone})


@router.callback_query(F.data == "sns:tend")
async def snus_end_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await start_time_pick(cb, state, "snus_end", {"tz": user.timezone})

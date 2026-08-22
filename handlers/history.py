"""History of a user's own records."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, start_time_pick
from keyboards.main import (
    _btn,
    calendar_kb,
    confirm_remove_kb,
    entry_actions,
    history_period_kb,
    with_nav,
)
from services.history import build_timeline, format_timeline
from services.users import can_write
from states.diary import HistorySG, TimePickSG
from utils.callbacks import NAV_HISTORY
from utils.telegram import safe_edit
from utils.time import add_days, format_dt, parse_calendar_token, parse_iso, user_today

router = Router(name="history")

KIND_MAP = {
    "cigarette": "cig",
    "fooling": "fool",
    "snus_buy": "snb",
    "snus_end": "snf",
    "sleep_bed": "sb",
    "sleep_wake": "sw",
    "mood": "mood",
    "wellbeing": "wb",
    "caffeine": "caf",
    "alcohol": "alc",
    "activity": "act",
    "note": "note",
    "custom": "cm",
}


async def _show_day(cb: CallbackQuery, repo: Repo, user: User, start: date, end: date) -> None:
    items = await build_timeline(repo, user, start, end)
    text = format_timeline(user, start, items)
    if start != end:
        from utils.time import format_date_long

        text = format_timeline(user, start, items).replace(
            format_date_long(start),
            f"{format_date_long(start)} — {format_date_long(end)}",
            1,
        )
    b = InlineKeyboardBuilder()
    for item in items[:20]:
        kind = KIND_MAP.get(item.kind, item.kind)
        label = f"{item.title}"
        b.row(_btn(label[:40], f"h:o:{kind}:{item.id}"))
    await cb.answer()
    await safe_edit(cb.message, text, with_nav(b, NAV_HISTORY))


@router.callback_query(F.data == NAV_HISTORY)
async def history_root(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, "📅 История. Выберите период:", history_period_kb())


@router.callback_query(F.data == "hist:today")
async def hist_today(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    today = user_today(user.timezone)
    await _show_day(cb, repo, user, today, today)


@router.callback_query(F.data == "hist:yesterday")
async def hist_yesterday(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    day = add_days(user_today(user.timezone), -1)
    await _show_day(cb, repo, user, day, day)


@router.callback_query(F.data == "hist:date")
async def hist_pick_date(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    today = user_today(user.timezone)
    await state.set_state(HistorySG.custom_date)
    await state.update_data(hist_mode="date")
    await cb.answer()
    await safe_edit(cb.message, "Выберите дату:", calendar_kb(today.year, today.month, prefix="hcal", back=NAV_HISTORY))


@router.callback_query(F.data == "hist:range")
async def hist_range(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    today = user_today(user.timezone)
    await state.set_state(HistorySG.custom_date)
    await state.update_data(hist_mode="range")
    await cb.answer()
    await safe_edit(cb.message, "Начало периода:", calendar_kb(today.year, today.month, prefix="hcal", back=NAV_HISTORY))


@router.callback_query(F.data.startswith("hcalm:"))
async def hist_month(cb: CallbackQuery, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    await cb.answer()
    await safe_edit(cb.message, "Выберите дату:", calendar_kb(year, month, prefix="hcal", back=NAV_HISTORY))


@router.callback_query(F.data.startswith("hcal:"), HistorySG.custom_date)
async def hist_got_date(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    try:
        day = parse_calendar_token(token, user_today(user.timezone))
    except ValueError:
        await cb.answer()
        return
    data = await state.get_data()
    if data.get("hist_mode") == "range" and not data.get("range_start"):
        await state.update_data(range_start=day.isoformat())
        await state.set_state(HistorySG.range_end)
        await cb.answer()
        await safe_edit(cb.message, "Конец периода:", calendar_kb(day.year, day.month, prefix="hcal", back=NAV_HISTORY))
        return
    start = date.fromisoformat(data["range_start"]) if data.get("range_start") else day
    end = day
    if end < start:
        start, end = end, start
    await state.clear()
    await _show_day(cb, repo, user, start, end)


@router.callback_query(F.data.startswith("hcal:"), HistorySG.range_end)
async def hist_got_end(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    await hist_got_date(cb, state, repo, db_user)


@router.callback_query(F.data.startswith("h:o:"))
async def hist_open(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    _, _, kind, raw_id = cb.data.split(":")
    item_id = int(raw_id)
    text = await _entry_text(repo, user, kind, item_id)
    writable = can_write(user)
    await cb.answer()
    await safe_edit(cb.message, text, entry_actions(kind, item_id, writable))


async def _entry_text(repo: Repo, user: User, kind: str, item_id: int) -> str:
    loaders = {
        "cig": repo.get_cigarette,
        "fool": repo.get_fooling,
        "snb": repo.get_snus_pack,
        "snf": repo.get_snus_pack,
        "sb": repo.get_sleep,
        "sw": repo.get_sleep,
        "slp": repo.get_sleep,
        "mood": repo.get_mood,
        "wb": repo.get_wellbeing,
        "caf": repo.get_caffeine,
        "alc": repo.get_alcohol,
        "act": repo.get_activity,
        "note": repo.get_note,
        "cm": repo.get_metric_value,
    }
    loader = loaders.get(kind)
    if loader is None:
        return "Запись не найдена."
    rec = await loader(item_id, user.telegram_id)
    if rec is None:
        return "Запись не найдена или принадлежит другому пользователю."
    if kind in {"snb", "snf"}:
        from utils.formatting import duration_human

        bought = format_dt(parse_iso(rec.bought_at), user.timezone) if rec.bought_at else "—"
        finished = format_dt(parse_iso(rec.finished_at), user.timezone) if rec.finished_at else "ещё открыта"
        return (
            f"🟢 Шайба #{item_id}\n"
            f"Купил: {bought}\n"
            f"Закончилась: {finished}\n"
            f"Хватило: {duration_human(rec.duration_minutes)}"
        )
    when = getattr(rec, "occurred_at", None) or getattr(rec, "bedtime", None) or getattr(rec, "wake_time", None)
    stamp = format_dt(parse_iso(when), user.timezone) if when else "—"
    return f"Запись #{item_id}\nТип: {kind}\nВремя: {stamp}"


@router.callback_query(F.data.startswith("ed:"))
async def edit_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable

    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, kind, raw_id = cb.data.split(":")
    purpose = f"edit:{kind}:{raw_id}"
    await start_time_pick(cb, state, purpose, {"tz": user.timezone})


@router.callback_query(F.data.startswith("rm:"))
async def remove_ask(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable

    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, kind, raw_id = cb.data.split(":")
    await cb.answer()
    await safe_edit(cb.message, "Удалить запись?", confirm_remove_kb(kind, int(raw_id)))


@router.callback_query(F.data.startswith("rmok:"))
async def remove_ok(cb: CallbackQuery, repo: Repo, db_user: User | None, config: Config, is_owner: bool, state: FSMContext) -> None:
    from handlers.common import require_writable, show_main

    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, kind, raw_id = cb.data.split(":")
    item_id = int(raw_id)
    tid = user.telegram_id
    mapping = {
        "cig": repo.delete_cigarette,
        "fool": repo.delete_fooling,
        "snb": repo.delete_snus_pack,
        "snf": repo.delete_snus_pack,
        "sb": repo.delete_sleep,
        "sw": repo.delete_sleep,
        "slp": repo.delete_sleep,
        "mood": repo.delete_mood,
        "wb": repo.delete_wellbeing,
        "caf": repo.delete_caffeine,
        "alc": repo.delete_alcohol,
        "act": repo.delete_activity,
        "note": repo.delete_note,
        "cm": repo.delete_metric_value,
    }
    fn = mapping.get(kind)
    if fn:
        await fn(item_id, tid)
    await cb.answer("Удалено")
    await show_main(cb, user, config, is_owner, state)

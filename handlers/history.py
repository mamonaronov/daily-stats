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
    "sleep_phone": "sp",
    "sleep_away": "sa",
    "sleep_onset": "so",
    "sleep_wake": "sw",
    "sleep_up": "su",
    "caffeine": "caf",
    "alcohol": "alc",
    "activity": "act",
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


async def show_saved_entry(
    event: CallbackQuery | Message,
    repo: Repo,
    user: User,
    kind: str,
    item_id: int | None,
    state: FSMContext | None = None,
    *,
    toast: str = "Записано",
    heading: str = "✅ Записано",
) -> None:
    if state:
        await state.clear()
    if not item_id:
        text = heading
        markup = None
    else:
        text = await entry_text(repo, user, kind, item_id, heading=heading)
        markup = entry_actions(kind, item_id, can_write(user), undo=True)
    if isinstance(event, CallbackQuery):
        await event.answer(toast)
        await safe_edit(event.message, text, markup)
        return
    await event.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("h:o:"))
@router.callback_query(F.data.startswith("sv:"))
async def hist_open(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    parts = cb.data.split(":")
    kind, item_id = parts[1] if cb.data.startswith("sv:") else parts[2], int(parts[-1])
    if cb.data.startswith("sv:"):
        _, kind, raw_id = parts
        item_id = int(raw_id)
        text = await entry_text(repo, user, kind, item_id, heading="✅ Записано")
        markup = entry_actions(kind, item_id, can_write(user), undo=True)
    else:
        _, _, kind, raw_id = parts
        item_id = int(raw_id)
        text = await entry_text(repo, user, kind, item_id)
        markup = entry_actions(kind, item_id, can_write(user))
    await cb.answer()
    await safe_edit(cb.message, text, markup)


async def entry_text(repo: Repo, user: User, kind: str, item_id: int, *, heading: str | None = None) -> str:
    loaders = {
        "cig": repo.get_cigarette,
        "fool": repo.get_fooling,
        "snb": repo.get_snus_pack,
        "snf": repo.get_snus_pack,
        "sb": repo.get_sleep,
        "sp": repo.get_sleep,
        "sa": repo.get_sleep,
        "so": repo.get_sleep,
        "sw": repo.get_sleep,
        "su": repo.get_sleep,
        "slp": repo.get_sleep,
        "wu": repo.get_sleep,
        "caf": repo.get_caffeine,
        "alc": repo.get_alcohol,
        "act": repo.get_activity,
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
        body = (
            f"🟢 Шайба #{item_id}\n"
            f"Купил: {bought}\n"
            f"Закончилась: {finished}\n"
            f"Хватило: {duration_human(rec.duration_minutes)}"
        )
        return f"{heading}\n\n{body}" if heading else body
    if kind in {"sb", "sp", "sa", "so", "sw", "su", "slp", "wu"}:
        from utils.formatting import duration_human, score_text

        def _stamp(value: str | None) -> str:
            return format_dt(parse_iso(value), user.timezone) if value else "—"

        lines = [
            f"😴 Сон #{item_id}",
            f"С телефоном: {_stamp(rec.phone_in_bed_at)}",
            f"Без телефона: {_stamp(rec.phone_away_at)}",
            f"Заснул: {_stamp(rec.sleep_onset_at)}",
            f"Проснулся: {_stamp(rec.wake_time)}",
            f"Встал: {_stamp(rec.out_of_bed_at)}",
            f"Длительность: {duration_human(rec.duration_minutes)}",
        ]
        if rec.quality:
            lines.append(f"Качество: {score_text(rec.quality)}")
        body = "\n".join(lines)
        return f"{heading}\n\n{body}" if heading else body
    when = getattr(rec, "occurred_at", None) or getattr(rec, "bedtime", None) or getattr(rec, "wake_time", None)
    stamp = format_dt(parse_iso(when), user.timezone) if when else "—"
    if kind == "cig":
        body = f"🚬 Сигарета\nВремя: {stamp}"
    elif kind == "fool":
        body = f"🤌 Валять дурака\nВремя: {stamp}"
    elif kind == "caf":
        from utils.formatting import CAFFEINE_TYPES
        from utils.quantity import format_quantity

        label = CAFFEINE_TYPES.get(rec.drink_type, rec.drink_type)
        extra = format_quantity(rec.amount, rec.unit)
        body = f"☕ {label.capitalize()}\nВремя: {stamp}"
        if extra:
            body += f"\nОбъём: {extra}"
    elif kind == "alc":
        from utils.formatting import ALCOHOL_TYPES
        from utils.quantity import format_quantity

        label = ALCOHOL_TYPES.get(rec.drink_type, rec.drink_type)
        extra = format_quantity(rec.amount, rec.unit)
        body = f"🍺 {label.capitalize()}\nВремя: {stamp}"
        if extra:
            body += f"\nОбъём: {extra}"
    elif kind == "act":
        from utils.formatting import ACTIVITY_TYPES, duration_human

        label = ACTIVITY_TYPES.get(rec.activity_type, rec.activity_type)
        body = f"🏃 {label.capitalize()}\nВремя: {stamp}\nДлительность: {duration_human(rec.duration_minutes)}"
        if rec.comment:
            body += f"\nКомментарий: {rec.comment}"
    elif kind == "cm":
        value = rec.value_text
        if rec.value_number is not None:
            value = f"{rec.value_number:g}"
            if rec.unit:
                value += f" {rec.unit}"
        elif rec.value_bool is not None:
            value = "да" if rec.value_bool else "нет"
        name = rec.metric_name or "Метрика"
        body = f"📌 {name}\nВремя: {stamp}"
        if value:
            body += f"\nЗначение: {value}"
    else:
        body = f"Запись #{item_id}\nТип: {kind}\nВремя: {stamp}"
    return f"{heading}\n\n{body}" if heading else body


_entry_text = entry_text


@router.callback_query(F.data.startswith("ed:"))
async def edit_entry(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_writable

    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, kind, raw_id = cb.data.split(":")
    purpose = f"edit:{kind}:{raw_id}"
    await start_time_pick(cb, state, purpose, {"tz": user.timezone})


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
        "sp": repo.delete_sleep,
        "sa": repo.delete_sleep,
        "so": repo.delete_sleep,
        "sw": repo.delete_sleep,
        "su": repo.delete_sleep,
        "slp": repo.delete_sleep,
        "wu": repo.delete_sleep,
        "caf": repo.delete_caffeine,
        "alc": repo.delete_alcohol,
        "act": repo.delete_activity,
        "cm": repo.delete_metric_value,
    }
    fn = mapping.get(kind)
    if fn:
        await fn(item_id, tid)
    await cb.answer("Удалено")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data.startswith("rm:"))
async def remove_ask(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable

    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, kind, raw_id = cb.data.split(":")
    await cb.answer()
    await safe_edit(cb.message, "Удалить запись?", confirm_remove_kb(kind, int(raw_id)))


@router.callback_query(F.data.startswith("unok:"))
async def undo_ok(cb: CallbackQuery, repo: Repo, db_user: User | None, config: Config, is_owner: bool, state: FSMContext) -> None:
    from handlers.common import require_writable, show_main
    from services.entries import undo_entry

    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, kind, raw_id = cb.data.split(":")
    error = await undo_entry(repo, user, kind, int(raw_id))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Отменено")
    await show_main(cb, user, config, is_owner, state, repo)


@router.callback_query(F.data.startswith("un:"))
async def undo_ask(cb: CallbackQuery, db_user: User | None) -> None:
    from handlers.common import require_writable

    user = await require_writable(cb, db_user)
    if user is None:
        return
    _, kind, raw_id = cb.data.split(":")
    await cb.answer()
    await safe_edit(
        cb.message,
        "Отменить эту запись? Если нажали случайно — так и нужно.",
        confirm_remove_kb(kind, int(raw_id), undo=True),
    )

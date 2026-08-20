"""Reusable date/hour/minute picker. Result is dispatched by time_purpose."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_writable, show_main
from keyboards.main import back_kb, calendar_kb, hours_kb, minutes_kb, score_kb, sleep_menu, when_kb, when_title
from services import entries
from services.reminders import refresh_user_reminder
from states.diary import SleepSG, TimePickSG
from utils.callbacks import ENTRY_SLEEP, NAV_BACK
from utils.telegram import safe_edit
from utils.time import combine_local, parse_hhmm, user_today

router = Router(name="time_pick")


async def _finish(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    user: User,
    is_owner: bool,
    when: datetime,
) -> None:
    data = await state.get_data()
    purpose = data.get("time_purpose")
    item_id = None
    error = None
    if purpose == "cig":
        item_id, error = await entries.add_cigarette(repo, user, when)
    elif purpose == "fool":
        item_id, error = await entries.add_fooling(repo, user, when)
    elif purpose == "slp_bed":
        item_id, error = await entries.add_sleep_bed(repo, user, when)
        await refresh_user_reminder(repo, user, config)
    elif purpose == "slp_wake":
        quality = data.get("quality")
        item_id, error = await entries.add_sleep_wake(repo, user, when, quality)
        await refresh_user_reminder(repo, user, config)
    elif purpose == "snus_buy":
        item_id, error = await entries.add_snus_bought(repo, user, when)
    elif purpose == "snus_end":
        item_id, error = await entries.add_snus_finished(repo, user, when)
    elif purpose == "mood":
        item_id, error = await entries.add_mood(repo, user, int(data["score"]), when)
    elif purpose == "wb":
        item_id, error = await entries.add_wellbeing(repo, user, int(data["score"]), data.get("comment"), when)
    elif purpose == "caf":
        item_id, error = await entries.add_caffeine(
            repo, user, data["drink_type"], data.get("amount"), data.get("unit") or "шт", when
        )
    elif purpose == "alc":
        item_id, error = await entries.add_alcohol(
            repo, user, data["drink_type"], data.get("amount"), data.get("unit") or "шт", when
        )
    elif purpose == "act":
        item_id, error = await entries.add_activity(
            repo, user, data["activity_type"], data.get("duration"), data.get("comment"), when
        )
    elif purpose == "note":
        item_id, error = await entries.add_note(repo, user, data["body"], when)
    elif purpose == "cm":
        item_id, error = await entries.add_custom_value(
            repo,
            user,
            int(data["metric_id"]),
            when,
            value_number=data.get("value_number"),
            value_text=data.get("value_text"),
            value_bool=data.get("value_bool"),
        )
    elif purpose and purpose.startswith("edit:"):
        error = await _apply_edit(repo, user, purpose, when)
    else:
        error = "Неизвестный сценарий ввода времени."

    from utils.formatting import duration_human
    from utils.time import format_dt

    await state.clear()
    if error:
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    notice = f"Сохранено: {format_dt(when, user.timezone)}"
    if purpose == "snus_end" and item_id:
        pack = await repo.get_snus_pack(item_id, user.telegram_id)
        if pack:
            notice = f"Хватило на {duration_human(pack.duration_minutes)}"
    if isinstance(event, CallbackQuery):
        await event.answer(notice)
        await show_main(event, user, config, is_owner, state)
    else:
        await event.answer(notice)
        await show_main(event, user, config, is_owner, state)


async def _apply_edit(repo: Repo, user: User, purpose: str, when: datetime) -> str | None:
    blocked = await entries.require_write(user)
    if blocked:
        return blocked
    _, kind, raw_id = purpose.split(":", 2)
    item_id = int(raw_id)
    from services.entries import _duration, _elapsed_minutes
    from utils.time import to_iso

    iso = to_iso(when)
    if kind in {"sb", "slp_bed"}:
        rec = await repo.get_sleep(item_id, user.telegram_id)
        if rec is None:
            return "Запись не найдена."
        duration = _duration(iso, rec.wake_time)
        await repo.update_sleep(item_id, user.telegram_id, bedtime=iso, duration_minutes=duration)
        return None
    if kind in {"sw", "slp_wake"}:
        rec = await repo.get_sleep(item_id, user.telegram_id)
        if rec is None:
            return "Запись не найдена."
        duration = _duration(rec.bedtime, iso)
        await repo.update_sleep(item_id, user.telegram_id, wake_time=iso, duration_minutes=duration)
        return None
    if kind in {"snb", "snus_buy"}:
        rec = await repo.get_snus_pack(item_id, user.telegram_id)
        if rec is None:
            return "Запись не найдена."
        duration = _elapsed_minutes(iso, rec.finished_at) if rec.finished_at else None
        if rec.finished_at and duration is None:
            return "Время окончания раньше покупки."
        await repo.update_snus_pack(item_id, user.telegram_id, bought_at=iso, duration_minutes=duration)
        return None
    if kind in {"snf", "snus_end"}:
        rec = await repo.get_snus_pack(item_id, user.telegram_id)
        if rec is None:
            return "Запись не найдена."
        duration = _elapsed_minutes(rec.bought_at, iso)
        if duration is None:
            return "Время окончания раньше покупки."
        await repo.update_snus_pack(item_id, user.telegram_id, finished_at=iso, duration_minutes=duration)
        return None
    mapping = {
        "cig": repo.update_cigarette_time,
        "fool": repo.update_fooling_time,
        "mood": lambda i, t, v: repo.update_mood(i, t, occurred_at=v),
        "wb": lambda i, t, v: repo.update_wellbeing(i, t, occurred_at=v),
        "caf": lambda i, t, v: repo.update_caffeine(i, t, occurred_at=v),
        "alc": lambda i, t, v: repo.update_alcohol(i, t, occurred_at=v),
        "act": lambda i, t, v: repo.update_activity(i, t, occurred_at=v),
        "note": lambda i, t, v: repo.update_note(i, t, occurred_at=v),
    }
    fn = mapping.get(kind)
    if fn is None:
        return "Этот тип записи нельзя изменить таким образом."
    await fn(item_id, user.telegram_id, iso)
    return None


@router.callback_query(F.data.startswith("cal:"), TimePickSG.date)
async def pick_date(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    if token == "today":
        day = user_today(user.timezone)
    else:
        day = date.fromisoformat(token)
    await state.update_data(picked_date=day.isoformat())
    await state.set_state(TimePickSG.hour)
    await cb.answer()
    data = await state.get_data()
    await safe_edit(
        cb.message,
        f"Дата: {day.isoformat()}\nВыберите час — можно уже прошедший:"
        if data.get("time_date_shortcuts")
        else f"Дата: {day.isoformat()}\nВыберите час:",
        hours_kb(date_shortcuts=bool(data.get("time_date_shortcuts"))),
    )


def time_pick_back_action(current: str | None, *, date_shortcuts: bool) -> str:
    if current in {TimePickSG.minute.state, TimePickSG.manual.state}:
        return "hours"
    if current == TimePickSG.hour.state:
        return "exit" if date_shortcuts else "date"
    if current == TimePickSG.date.state:
        return "hours" if date_shortcuts else "exit"
    return "exit"


async def _restore_before_time_pick(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    user: User,
) -> None:
    data = await state.get_data()
    exit_to = data.get("time_exit") or "when:cig"
    metric_id = data.get("metric_id")
    await state.set_state(None)
    if exit_to.startswith("when:"):
        prefix = exit_to.split(":", 1)[1]
        await cb.answer()
        await safe_edit(cb.message, when_title(prefix), when_kb(prefix, metric_id=metric_id))
        return
    if exit_to == "sleep":
        await cb.answer()
        await safe_edit(cb.message, "😴 Сон", sleep_menu())
        return
    if exit_to == "snus":
        from handlers.snus import show_snus_menu

        await show_snus_menu(cb, repo, user)
        return
    if exit_to == "slq":
        await state.set_state(SleepSG.quality)
        await cb.answer()
        await safe_edit(cb.message, "Как спалось?", score_kb("slq", back=ENTRY_SLEEP))
        return
    if exit_to.startswith("hist:"):
        _, kind, raw_id = exit_to.split(":", 2)
        from handlers.history import _entry_text
        from keyboards.main import entry_actions
        from services.users import can_write

        text = await _entry_text(repo, user, kind, int(raw_id))
        await cb.answer()
        await safe_edit(cb.message, text, entry_actions(kind, int(raw_id), can_write(user)))
        return
    if exit_to.startswith("cm:"):
        from keyboards.main import metric_card_kb
        from services.metric_types import METRIC_TYPES

        metric = await repo.get_metric(int(exit_to.split(":")[1]), user.telegram_id)
        if metric is None:
            await cb.answer()
            await safe_edit(cb.message, "📌 Ваши показатели", None)
            return
        spec = METRIC_TYPES[metric.data_type]
        text = (
            f"📌 <b>{metric.name}</b>\n"
            f"Тип: {spec.label}\n"
            f"Ед.: {metric.unit or '—'}\n"
            f"Статус: {'вкл' if metric.enabled else 'выкл'}"
        )
        await cb.answer()
        await safe_edit(cb.message, text, metric_card_kb(metric.id, bool(metric.enabled), True))
        return
    await cb.answer()
    await safe_edit(cb.message, when_title("cig"), when_kb("cig"))


@router.callback_query(F.data == NAV_BACK, TimePickSG.date)
@router.callback_query(F.data == NAV_BACK, TimePickSG.hour)
@router.callback_query(F.data == NAV_BACK, TimePickSG.minute)
@router.callback_query(F.data == NAV_BACK, TimePickSG.manual)
async def time_pick_back(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    action = time_pick_back_action(await state.get_state(), date_shortcuts=bool(data.get("time_date_shortcuts")))
    if action == "exit":
        await _restore_before_time_pick(cb, state, repo, user)
        return
    today = user_today(data.get("tz") or user.timezone)
    if action == "date":
        day = date.fromisoformat(data.get("picked_date") or today.isoformat())
        await state.set_state(TimePickSG.date)
        await cb.answer()
        await safe_edit(cb.message, "Выберите дату:", calendar_kb(day.year, day.month, back=NAV_BACK))
        return
    day = date.fromisoformat(data.get("picked_date") or today.isoformat())
    await state.set_state(TimePickSG.hour)
    await cb.answer()
    await safe_edit(
        cb.message,
        _hours_prompt(day, today) if data.get("time_date_shortcuts") else f"Дата: {day.isoformat()}\nВыберите час:",
        hours_kb(date_shortcuts=bool(data.get("time_date_shortcuts"))),
    )


@router.callback_query(F.data.startswith("calm:"), TimePickSG.date)
async def change_month(cb: CallbackQuery, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    await cb.answer()
    await safe_edit(cb.message, "Выберите дату:", calendar_kb(year, month, back=NAV_BACK))


def _hours_prompt(day: date, today: date) -> str:
    if day == today:
        label = "сегодня"
    elif day == today - timedelta(days=1):
        label = "вчера"
    else:
        label = day.isoformat()
    return f"Дата: {day.isoformat()} ({label})\nВыберите час — можно уже прошедший:"


@router.callback_query(F.data.startswith("hdt:"), TimePickSG.hour)
async def hour_date_shortcut(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    data = await state.get_data()
    tz = data.get("tz") or user.timezone
    today = user_today(tz)
    if token == "calendar":
        await state.set_state(TimePickSG.date)
        await cb.answer()
        await safe_edit(cb.message, "Выберите дату:", calendar_kb(today.year, today.month, back=NAV_BACK))
        return
    if token == "today":
        day = today
    elif token == "yesterday":
        day = today - timedelta(days=1)
    else:
        await cb.answer()
        return
    await state.update_data(picked_date=day.isoformat())
    await cb.answer()
    await safe_edit(cb.message, _hours_prompt(day, today), hours_kb(date_shortcuts=True))


@router.callback_query(F.data.startswith("hr:"), TimePickSG.hour)
async def pick_hour(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    token = cb.data.split(":", 1)[1]
    if token == "manual":
        await state.set_state(TimePickSG.manual)
        await cb.answer()
        await safe_edit(cb.message, "Введите время в формате ЧЧ:ММ", back_kb(NAV_BACK))
        return
    await state.update_data(picked_hour=int(token))
    await state.set_state(TimePickSG.minute)
    await cb.answer()
    await safe_edit(cb.message, f"Час: {int(token):02d}\nВыберите минуты:", minutes_kb())


@router.callback_query(F.data.startswith("mn:"), TimePickSG.minute)
async def pick_minute(
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
    minute = int(cb.data.split(":", 1)[1])
    data = await state.get_data()
    day = date.fromisoformat(data["picked_date"])
    hour = int(data["picked_hour"])
    when = combine_local(user.timezone, day, hour, minute)
    await _finish(cb, state, repo, config, user, is_owner, when)


@router.message(TimePickSG.manual)
async def manual_time(
    message: Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    try:
        hour, minute = parse_hhmm(message.text or "")
    except ValueError:
        await message.answer("Некорректное время. Пример: 14:35", reply_markup=back_kb(NAV_BACK))
        return
    data = await state.get_data()
    day = date.fromisoformat(data["picked_date"])
    when = combine_local(user.timezone, day, hour, minute)
    await _finish(message, state, repo, config, user, is_owner, when)

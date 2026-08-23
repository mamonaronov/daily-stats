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
from keyboards.main import (
    ago_pick_kb,
    back_kb,
    calendar_kb,
    hours_kb,
    minutes_kb,
    score_kb,
    sleep_onset_kb,
    when_kb,
    when_title,
)
from services import entries
from states.diary import SleepSG, TimePickSG
from utils.callbacks import NAV_BACK, NAV_MAIN
from utils.telegram import safe_edit
from utils.time import (
    combine_local,
    minutes_ago,
    parse_calendar_token,
    parse_hhmm,
    parse_minutes_ago,
    parse_when_text,
    to_iso,
    user_today,
)

router = Router(name="time_pick")

WHEN_PREFIXES = ("cig", "fool", "caft", "alct", "actt", "slw", "cmt", "slp")
WHEN_TO_PURPOSE = {
    "cig": "cig",
    "fool": "fool",
    "caft": "caf",
    "alct": "alc",
    "actt": "act",
    "slw": "slp_wake",
    "cmt": "cm",
}
_WHEN_RE = r"^(?:cig|fool|caft|alct|actt|slw|cmt|slp)"
MANUAL_TIME_PROMPT = "Введите время, например 10:00, 1000 или 10 00"
WHEN_TEXT_PROMPT = "Введите время (10:00, 1000, 10 00) или сколько минут назад (например 7 или 1 час)"
AGO_MINUTES_PROMPT = "Сколько минут назад это было? Например 7 или 1 час"


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
    elif purpose == "slp_onset":
        item_id, error = await entries.add_sleep_onset(repo, user, when)
    elif purpose == "snus_buy":
        item_id, error = await entries.add_snus_bought(repo, user, when)
    elif purpose == "snus_end":
        item_id, error = await entries.add_snus_finished(repo, user, when)
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
    else:
        await event.answer(notice)
    if purpose == "cm":
        from handlers.custom_metrics import show_custom_metrics

        await show_custom_metrics(event, repo, user, state)
        return
    await show_main(event, user, config, is_owner, state, repo)


async def _apply_edit(repo: Repo, user: User, purpose: str, when: datetime) -> str | None:
    blocked = await entries.require_write(user)
    if blocked:
        return blocked
    _, kind, raw_id = purpose.split(":", 2)
    item_id = int(raw_id)
    from services.entries import _elapsed_minutes
    from utils.time import to_iso

    iso = to_iso(when)
    rec = None
    field = None
    if kind in {"sb", "slp_bed", "sa"}:
        rec = await repo.get_sleep(item_id, user.telegram_id)
        field = "phone_away_at"
    elif kind == "sp":
        rec = await repo.get_sleep(item_id, user.telegram_id)
        field = "phone_in_bed_at"
    elif kind == "so":
        rec = await repo.get_sleep(item_id, user.telegram_id)
        field = "sleep_onset_at"
    elif kind == "su":
        rec = await repo.get_sleep(item_id, user.telegram_id)
        field = "out_of_bed_at"
    elif kind in {"sw", "slp_wake"}:
        rec = await repo.get_sleep(item_id, user.telegram_id)
        field = "wake_time"
    if field is not None:
        if rec is None:
            return "Запись не найдена."
        from services.entries import _elapsed_minutes

        updates: dict = {field: iso}
        phone_in = iso if field == "phone_in_bed_at" else rec.phone_in_bed_at
        phone_away = iso if field == "phone_away_at" else rec.phone_away_at
        if field in {"phone_in_bed_at", "phone_away_at"}:
            updates["bedtime"] = phone_in or phone_away
        onset = iso if field == "sleep_onset_at" else rec.sleep_onset_at
        wake = iso if field == "wake_time" else rec.wake_time
        if onset and wake:
            duration = _elapsed_minutes(onset, wake)
            if duration is None:
                return "Время засыпания позже пробуждения."
            updates["duration_minutes"] = duration
        elif field in {"sleep_onset_at", "wake_time"}:
            updates["duration_minutes"] = None
        await repo.update_sleep(item_id, user.telegram_id, **updates)
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
        "caf": lambda i, t, v: repo.update_caffeine(i, t, occurred_at=v),
        "alc": lambda i, t, v: repo.update_alcohol(i, t, occurred_at=v),
        "act": lambda i, t, v: repo.update_activity(i, t, occurred_at=v),
    }
    fn = mapping.get(kind)
    if fn is None:
        return "Этот тип записи нельзя изменить таким образом."
    await fn(item_id, user.telegram_id, iso)
    return None


def _when_prefix(data: str) -> str:
    prefix = data.split(":", 1)[0]
    if prefix not in WHEN_PREFIXES:
        raise ValueError(prefix)
    return prefix


async def _prepare_when_purpose(state: FSMContext, prefix: str, user: User) -> None:
    payload: dict = {"when_prefix": prefix, "tz": user.timezone}
    purpose = WHEN_TO_PURPOSE.get(prefix)
    if purpose:
        payload["time_purpose"] = purpose
    data = await state.get_data()
    if "time_exit" not in data:
        payload["time_exit"] = "sleep" if prefix == "slp" else f"when:{prefix}"
    await state.update_data(**payload)


async def _show_when_screen(cb: CallbackQuery, state: FSMContext, data: dict) -> None:
    prefix = data.get("when_prefix") or "cig"
    await cb.answer()
    if prefix == "slp":
        await state.set_state(None)
        await safe_edit(cb.message, "😴 Сон", sleep_onset_kb())
        return
    if prefix == "slw":
        await state.set_state(SleepSG.when)
    else:
        await state.set_state(None)
    await safe_edit(cb.message, when_title(prefix), when_kb(prefix, metric_id=data.get("metric_id")))


async def _sleep_after_when(event: CallbackQuery | Message, state: FSMContext, when) -> None:
    await state.set_state(None)
    await state.update_data(sleep_when=to_iso(when))
    text = "Что отметить?"
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, text, sleep_onset_kb())
    else:
        await event.answer(text, reply_markup=sleep_onset_kb())


async def _save_relative(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    user: User,
    is_owner: bool,
    prefix: str,
    when,
) -> None:
    await _prepare_when_purpose(state, prefix, user)
    if prefix == "slp":
        await _sleep_after_when(event, state, when)
        return
    await _finish(event, state, repo, config, user, is_owner, when)


@router.callback_query(F.data.regexp(_WHEN_RE + r":ago:\d+$"))
async def when_ago(
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
    prefix = _when_prefix(cb.data)
    minutes = int(cb.data.rsplit(":", 1)[1])
    when = minutes_ago(user.timezone, minutes)
    await _save_relative(cb, state, repo, config, user, is_owner, prefix, when)


@router.callback_query(F.data.regexp(_WHEN_RE + r":agoask$"))
async def when_ago_ask(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    prefix = _when_prefix(cb.data)
    await _prepare_when_purpose(state, prefix, user)
    await state.set_state(TimePickSG.ago_pick)
    await cb.answer()
    await safe_edit(cb.message, "Сколько времени назад это было?", ago_pick_kb(prefix))


@router.callback_query(F.data.regexp(_WHEN_RE + r":agon$"), TimePickSG.ago_pick)
async def when_ago_number(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.set_state(TimePickSG.ago_minutes)
    await cb.answer()
    await safe_edit(cb.message, AGO_MINUTES_PROMPT, back_kb(NAV_BACK))


@router.callback_query(F.data.regexp(_WHEN_RE + r":txt$"))
async def when_text_ask(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    prefix = _when_prefix(cb.data)
    await _prepare_when_purpose(state, prefix, user)
    await state.set_state(TimePickSG.when_text)
    await cb.answer()
    await safe_edit(cb.message, WHEN_TEXT_PROMPT, back_kb(NAV_BACK))


@router.message(TimePickSG.ago_minutes)
async def when_ago_minutes_text(
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
        minutes = parse_minutes_ago(message.text or "")
    except ValueError:
        await message.answer("Некорректное число. Пример: 7 или 1 час", reply_markup=back_kb(NAV_BACK))
        return
    data = await state.get_data()
    prefix = data.get("when_prefix") or "cig"
    when = minutes_ago(user.timezone, minutes)
    await _save_relative(message, state, repo, config, user, is_owner, prefix, when)


@router.message(TimePickSG.when_text)
async def when_free_text(
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
        when = parse_when_text(message.text or "", user.timezone)
    except ValueError:
        await message.answer(
            "Не понял время. Примеры: 10:00, 1000, 10 00, 7, 1 час",
            reply_markup=back_kb(NAV_BACK),
        )
        return
    data = await state.get_data()
    prefix = data.get("when_prefix") or "cig"
    await _save_relative(message, state, repo, config, user, is_owner, prefix, when)


@router.callback_query(F.data.startswith("cal:"), TimePickSG.date)
async def pick_date(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    try:
        day = parse_calendar_token(token, user_today(user.timezone))
    except ValueError:
        await cb.answer()
        return
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
    if current == TimePickSG.ago_minutes.state:
        return "ago_pick"
    if current in {TimePickSG.when_text.state, TimePickSG.ago_pick.state}:
        return "when"
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
    if exit_to in {"sleep", "slp_onset"}:
        await cb.answer()
        await safe_edit(cb.message, "Когда заснули?", sleep_onset_kb())
        return
    if exit_to == "snus":
        from handlers.snus import show_snus_menu

        await show_snus_menu(cb, repo, user)
        return
    if exit_to == "slq":
        await state.set_state(SleepSG.quality)
        await cb.answer()
        await safe_edit(cb.message, "Как спалось?", score_kb("slq", back=NAV_MAIN))
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
        from services.metric_types import metric_card_text

        metric = await repo.get_metric(int(exit_to.split(":")[1]), user.telegram_id)
        if metric is None:
            await cb.answer()
            await safe_edit(cb.message, "📌 Кастомные метрики", None)
            return
        await cb.answer()
        await safe_edit(cb.message, metric_card_text(metric), metric_card_kb(metric.id, bool(metric.enabled), True))
        return
    await cb.answer()
    await safe_edit(cb.message, when_title("cig"), when_kb("cig"))


@router.callback_query(F.data == NAV_BACK, TimePickSG.date)
@router.callback_query(F.data == NAV_BACK, TimePickSG.hour)
@router.callback_query(F.data == NAV_BACK, TimePickSG.minute)
@router.callback_query(F.data == NAV_BACK, TimePickSG.manual)
@router.callback_query(F.data == NAV_BACK, TimePickSG.ago_pick)
@router.callback_query(F.data == NAV_BACK, TimePickSG.ago_minutes)
@router.callback_query(F.data == NAV_BACK, TimePickSG.when_text)
async def time_pick_back(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    action = time_pick_back_action(await state.get_state(), date_shortcuts=bool(data.get("time_date_shortcuts")))
    if action == "exit":
        await _restore_before_time_pick(cb, state, repo, user)
        return
    if action == "when":
        await _show_when_screen(cb, state, data)
        return
    if action == "ago_pick":
        prefix = data.get("when_prefix") or "cig"
        await state.set_state(TimePickSG.ago_pick)
        await cb.answer()
        await safe_edit(cb.message, "Сколько времени назад это было?", ago_pick_kb(prefix))
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
    elif day == today - timedelta(days=2):
        label = "позавчера"
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
    try:
        day = parse_calendar_token(token, today)
    except ValueError:
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
        await safe_edit(cb.message, MANUAL_TIME_PROMPT, back_kb(NAV_BACK))
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
        await message.answer("Некорректное время. Примеры: 14:35, 1435, 14 35", reply_markup=back_kb(NAV_BACK))
        return
    data = await state.get_data()
    day = date.fromisoformat(data["picked_date"])
    when = combine_local(user.timezone, day, hour, minute)
    await _finish(message, state, repo, config, user, is_owner, when)

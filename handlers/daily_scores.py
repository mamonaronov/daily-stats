"""Daily 1–5 ratings: one value per kind per local day, upsert anytime."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from database.queries import Repo
from handlers.common import require_writable
from keyboards.main import calendar_kb, daily_scores_day_kb, daily_scores_value_kb
from services import entries
from services.daily_scores import (
    HUB_LABEL,
    SCORE_BY_CODE,
    SCORE_BY_KEY,
    format_score_line,
    spec_of,
    tracked_score_keys,
)
from services.ui_prefs import prefs_of
from states.diary import DailyScoreSG
from utils.callbacks import ENTRY_DS
from utils.telegram import safe_edit
from utils.time import format_date_long, parse_calendar_token, user_today

router = Router(name="daily_scores")


def _filled_label(records, keys: list[str]) -> str | None:
    if not keys:
        return None
    have = {rec.kind for rec in records}
    n = sum(1 for key in keys if key in have)
    if n == 0:
        return None
    return f"{n}/{len(keys)}"


def _day_heading(day: date, today: date) -> str:
    if day == today:
        return f"сегодня ({format_date_long(day)})"
    if day == today - timedelta(days=1):
        return f"вчера ({format_date_long(day)})"
    return format_date_long(day)


def _value_text(day: date, today: date, specs, current: dict[str, int]) -> str:
    extra = (
        "День ещё идёт — можно записать сейчас и потом поменять."
        if day == today
        else "Можно поменять значение в любой момент."
    )
    lines = [f"{HUB_LABEL} за {_day_heading(day, today)}", extra, ""]
    for spec in specs:
        lines.append(format_score_line(spec, current.get(spec.key)))
        lines.append(spec.hint)
        lines.append("")
    lines.append("Нажмите оценку от 1 до 5. Лица: 😢 ужасно … 🤩 отлично.")
    return "\n".join(lines)


async def _score_keys(user: User, extra: str | None = None) -> list[str]:
    keys = tracked_score_keys(prefs_of(user).tracked)
    if extra and extra not in keys and extra in SCORE_BY_KEY:
        keys = [*keys, extra]
    return keys


async def _ask_values(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    day: date,
    *,
    extra_kind: str | None = None,
) -> None:
    keys = await _score_keys(user, extra_kind)
    if not keys:
        text = "Нет выбранных оценок. Включите их в Настройках → Метрики."
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit(event.message, text, daily_scores_day_kb())
            return
        await event.answer(text)
        return
    rows = await repo.list_daily_scores_for_day(user.telegram_id, day.isoformat())
    current = {rec.kind: rec.score for rec in rows}
    specs = [spec_of(key) for key in keys]
    await state.set_state(DailyScoreSG.value)
    await state.update_data(ds_day=day.isoformat())
    text = _value_text(day, user_today(user.timezone), specs, current)
    markup = daily_scores_value_kb(specs, current, back=ENTRY_DS)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, text, markup)
        return
    await event.answer(text, reply_markup=markup)


async def show_daily_scores_menu(cb: CallbackQuery, repo: Repo, user: User, state: FSMContext) -> None:
    await state.clear()
    keys = await _score_keys(user)
    today = user_today(user.timezone)
    yesterday = today - timedelta(days=1)
    today_rows = await repo.list_daily_scores_for_day(user.telegram_id, today.isoformat())
    yest_rows = await repo.list_daily_scores_for_day(user.telegram_id, yesterday.isoformat())
    await cb.answer()
    await safe_edit(
        cb.message,
        f"{HUB_LABEL} за какой день?",
        daily_scores_day_kb(
            today_filled=_filled_label(today_rows, keys),
            yesterday_filled=_filled_label(yest_rows, keys),
        ),
    )


@router.callback_query(F.data == "ds:today")
async def scores_today(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _ask_values(cb, state, repo, user, user_today(user.timezone))


@router.callback_query(F.data == "ds:yest")
async def scores_yesterday(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _ask_values(cb, state, repo, user, user_today(user.timezone) - timedelta(days=1))


@router.callback_query(F.data == "ds:date")
async def scores_pick_date(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    today = user_today(user.timezone)
    await state.set_state(DailyScoreSG.pick_date)
    await cb.answer()
    await safe_edit(cb.message, "Дата оценок:", calendar_kb(today.year, today.month, prefix="dscal", back=ENTRY_DS))


@router.callback_query(F.data.startswith("dscalm:"))
async def scores_month(cb: CallbackQuery, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    await cb.answer()
    await safe_edit(cb.message, "Дата оценок:", calendar_kb(year, month, prefix="dscal", back=ENTRY_DS))


@router.callback_query(F.data.startswith("dscal:"), DailyScoreSG.pick_date)
async def scores_got_date(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    try:
        day = parse_calendar_token(token, user_today(user.timezone))
    except ValueError:
        await cb.answer()
        return
    await _ask_values(cb, state, repo, user, day)


@router.callback_query(F.data.startswith("ds:e:"))
async def scores_edit(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    rec = await repo.get_daily_score(int(cb.data.split(":")[2]), user.telegram_id)
    if rec is None:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    await _ask_values(cb, state, repo, user, date.fromisoformat(rec.day), extra_kind=rec.kind)


@router.callback_query(F.data.startswith("ds:q:"))
async def scores_pick(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer("Некорректная оценка", show_alert=True)
        return
    spec = SCORE_BY_CODE.get(parts[2])
    try:
        score = int(parts[3])
    except ValueError:
        spec = None
        score = 0
    if spec is None or score < 1 or score > 5:
        await cb.answer("Некорректная оценка", show_alert=True)
        return
    data = await state.get_data()
    raw_day = data.get("ds_day")
    if not raw_day:
        await cb.answer("Сначала выберите день", show_alert=True)
        return
    day = date.fromisoformat(raw_day)
    _, error, updated = await entries.upsert_daily_score(repo, user, day, spec.key, score)
    if error:
        await cb.answer(error, show_alert=True)
        return
    toast = "Обновлено" if updated else "Записано"
    keys = await _score_keys(user, spec.key)
    rows = await repo.list_daily_scores_for_day(user.telegram_id, day.isoformat())
    current = {rec.kind: rec.score for rec in rows}
    specs = [spec_of(key) for key in keys]
    await state.set_state(DailyScoreSG.value)
    await state.update_data(ds_day=day.isoformat())
    text = _value_text(day, user_today(user.timezone), specs, current)
    markup = daily_scores_value_kb(specs, current, back=ENTRY_DS)
    await cb.answer(toast)
    await safe_edit(cb.message, text, markup)

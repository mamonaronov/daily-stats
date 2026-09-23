"""Daily 1–5 ratings: one value per kind per local day, upsert anytime."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from database.queries import Repo
from handlers.common import require_writable
from keyboards.main import calendar_kb, daily_scores_day_kb, daily_scores_value_kb, score_gaps_kb
from services import entries
from services.daily_scores import (
    HUB_LABEL,
    OPEN_SCORES_CB,
    SCORE_BY_CODE,
    SCORE_BY_KEY,
    dismiss_open_score,
    format_open_scores,
    format_score_line,
    list_open_score_gaps,
    missing_by_day,
    missing_score_count,
    page_open_scores,
    spec_of,
    tracked_score_keys,
)
from services.ui_prefs import prefs_of
from states.diary import DailyScoreSG
from utils.callbacks import ENTRY_DS
from utils.telegram import safe_edit
from utils.time import format_date_long, parse_calendar_token, user_today

router = Router(name="daily_scores")


def _month_last(year: int, month: int) -> date:
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def _score_mark_days(year: int, month: int, today: date) -> list[date]:
    first = date(year, month, 1)
    last = min(_month_last(year, month), today)
    days: list[date] = []
    if first <= last:
        day = first
        while day <= last:
            days.append(day)
            day += timedelta(days=1)
    for offset in range(3):
        day = today - timedelta(days=offset)
        if day not in days:
            days.append(day)
    return days


async def _open_scores(repo: Repo, user: User, year: int, month: int) -> dict[date, int]:
    keys = await _score_keys(user)
    today = user_today(user.timezone)
    days = _score_mark_days(year, month, today)
    if not keys or not days:
        return {}
    start = min(days).isoformat()
    end = max(days).isoformat()
    pairs = await repo.list_daily_score_kinds_between(user.telegram_id, start, end)
    return missing_by_day(pairs, keys, days)


async def _scores_calendar(repo: Repo, user: User, year: int, month: int):
    today = user_today(user.timezone)
    return calendar_kb(
        year,
        month,
        prefix="dscal",
        back=ENTRY_DS,
        open_scores=await _open_scores(repo, user, year, month),
        today=today,
    )


def _day_heading(day: date, today: date) -> str:
    if day == today:
        return f"сегодня ({format_date_long(day)})"
    if day == today - timedelta(days=1):
        return f"вчера ({format_date_long(day)})"
    return format_date_long(day)


def _value_text(day: date, today: date, specs, current: dict[str, int]) -> str:
    extra = (
        "День ещё идёт — можно записать сейчас, потом поменять или снять."
        if day == today
        else "Можно поменять или снять значение в любой момент."
    )
    lines = [f"{HUB_LABEL} за {_day_heading(day, today)}", extra, ""]
    for spec in specs:
        lines.append(format_score_line(spec, current.get(spec.key)))
        lines.append(spec.hint)
        lines.append("")
    lines.append("Нажмите оценку от 1 до 5. Ещё раз или ✖️ — снять. Лица: 😢 ужасно … 🤩 отлично.")
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
    toast: str | None = None,
    back: str = ENTRY_DS,
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
    await state.update_data(ds_day=day.isoformat(), ds_back=back)
    text = _value_text(day, user_today(user.timezone), specs, current)
    markup = daily_scores_value_kb(specs, current, back=back)
    if isinstance(event, CallbackQuery):
        await event.answer(toast or "")
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
            today_missing=missing_score_count({rec.kind for rec in today_rows}, keys),
            yesterday_missing=missing_score_count({rec.kind for rec in yest_rows}, keys),
        ),
    )


def _gap_back(page: int) -> str:
    if page <= 0:
        return OPEN_SCORES_CB
    return f"ds:gpg:{page}"


def _callback_int(parts: list[str], index: int) -> int:
    if len(parts) <= index:
        return 0
    try:
        return max(0, int(parts[index]))
    except ValueError:
        return 0


async def _show_score_gaps(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    user: User,
    page: int,
    *,
    toast: str | None = None,
) -> None:
    await state.clear()
    today = user_today(user.timezone)
    gaps = await list_open_score_gaps(repo, user)
    shown, page, pages = page_open_scores(gaps, page)
    await cb.answer(toast or "")
    await safe_edit(
        cb.message,
        format_open_scores(shown, today),
        score_gaps_kb(shown, today, page=page, pages=pages),
    )


@router.callback_query(F.data == OPEN_SCORES_CB)
async def scores_gaps(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _show_score_gaps(cb, state, repo, user, 0)


@router.callback_query(F.data.startswith("ds:gpg:"))
async def scores_gaps_page(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    parts = (cb.data or "").split(":")
    await _show_score_gaps(cb, state, repo, user, _callback_int(parts, 2))


@router.callback_query(F.data.startswith("ds:gap:"))
async def scores_gap_day(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    parts = (cb.data or "").split(":")
    if len(parts) < 3:
        await cb.answer()
        return
    try:
        day = date.fromisoformat(parts[2])
    except ValueError:
        await cb.answer()
        return
    if day > user_today(user.timezone):
        await cb.answer("Этот день ещё не наступил", show_alert=True)
        return
    await _ask_values(cb, state, repo, user, day, back=_gap_back(_callback_int(parts, 3)))


@router.callback_query(F.data.startswith("ds:sk:"))
async def scores_skip(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    parts = (cb.data or "").split(":")
    if len(parts) < 4:
        await cb.answer()
        return
    try:
        day = date.fromisoformat(parts[2])
    except ValueError:
        await cb.answer()
        return
    spec = SCORE_BY_CODE.get(parts[3])
    if spec is None:
        await cb.answer("Некорректная оценка", show_alert=True)
        return
    error = await dismiss_open_score(repo, user, day, spec.key)
    if error:
        await cb.answer(error, show_alert=True)
        return
    await _show_score_gaps(cb, state, repo, user, _callback_int(parts, 4), toast="Убрано")


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
async def scores_pick_date(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    today = user_today(user.timezone)
    await state.set_state(DailyScoreSG.pick_date)
    await cb.answer()
    await safe_edit(cb.message, "Дата оценок:", await _scores_calendar(repo, user, today.year, today.month))


@router.callback_query(F.data.startswith("dscalm:"))
async def scores_month(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    await cb.answer()
    await safe_edit(cb.message, "Дата оценок:", await _scores_calendar(repo, user, year, month))


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
    rec = await repo.get_daily_score_by_day(user.telegram_id, day.isoformat(), spec.key)
    if rec is not None and rec.score == score:
        error = await entries.clear_daily_score(repo, user, day, spec.key)
        toast = "Снято"
    else:
        _, error, updated = await entries.upsert_daily_score(repo, user, day, spec.key, score)
        toast = "Обновлено" if updated else "Записано"
    if error:
        await cb.answer(error, show_alert=True)
        return
    await _ask_values(
        cb,
        state,
        repo,
        user,
        day,
        extra_kind=spec.key,
        toast=toast,
        back=str(data.get("ds_back") or ENTRY_DS),
    )


@router.callback_query(F.data.startswith("ds:x:"))
async def scores_clear(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    parts = (cb.data or "").split(":")
    spec = SCORE_BY_CODE.get(parts[2]) if len(parts) >= 3 else None
    if spec is None:
        await cb.answer("Некорректная оценка", show_alert=True)
        return
    data = await state.get_data()
    raw_day = data.get("ds_day")
    if not raw_day:
        await cb.answer("Сначала выберите день", show_alert=True)
        return
    day = date.fromisoformat(raw_day)
    error = await entries.clear_daily_score(repo, user, day, spec.key)
    if error:
        await cb.answer(error, show_alert=True)
        return
    await _ask_values(
        cb,
        state,
        repo,
        user,
        day,
        extra_kind=spec.key,
        toast="Снято",
        back=str(data.get("ds_back") or ENTRY_DS),
    )

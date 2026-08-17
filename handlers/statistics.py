"""Flexible statistics constructor."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active
from keyboards.main import calendar_kb, stats_metrics_kb, stats_period_kb
from services.charts import build_charts
from services.statistics import render_stats
from states.diary import StatsSG
from utils.callbacks import NAV_STATS
from utils.telegram import png_file, safe_edit
from utils.time import add_days, user_today

router = Router(name="statistics")

DEFAULT_METRICS = {"cigarettes", "sleep", "mood"}


def _period(user: User, token: str, data: dict) -> tuple[date, date] | None:
    today = user_today(user.timezone)
    if token == "today":
        return today, today
    if token == "yesterday":
        day = add_days(today, -1)
        return day, day
    if token in {"7", "14", "30"}:
        days = int(token)
        return add_days(today, -(days - 1)), today
    if token == "custom":
        start = date.fromisoformat(data["range_start"])
        end = date.fromisoformat(data["range_end"])
        if end < start:
            start, end = end, start
        return start, end
    return None


@router.callback_query(F.data == NAV_STATS)
async def stats_root(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.clear()
    await state.update_data(stats_metrics=list(DEFAULT_METRICS))
    await cb.answer()
    await safe_edit(cb.message, "📊 Статистика\nСначала выберите период:", stats_period_kb())


@router.callback_query(F.data.startswith("stp:"))
async def stats_period(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":")[1]
    if token == "range":
        today = user_today(user.timezone)
        await state.set_state(StatsSG.custom_date)
        await state.update_data(stats_mode="range")
        await cb.answer()
        await safe_edit(cb.message, "Начало периода:", calendar_kb(today.year, today.month, prefix="scal"))
        return
    await state.update_data(period=token)
    data = await state.get_data()
    selected = set(data.get("stats_metrics") or DEFAULT_METRICS)
    await cb.answer()
    await safe_edit(cb.message, "Показатели и вид результата:", stats_metrics_kb(selected))


@router.callback_query(F.data.startswith("scalm:"))
async def stats_month(cb: CallbackQuery, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    await cb.answer()
    await safe_edit(cb.message, "Выберите дату:", calendar_kb(year, month, prefix="scal"))


@router.callback_query(F.data.startswith("scal:"), StatsSG.custom_date)
async def stats_date(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    day = user_today(user.timezone) if token == "today" else date.fromisoformat(token)
    data = await state.get_data()
    if not data.get("range_start"):
        await state.update_data(range_start=day.isoformat())
        await state.set_state(StatsSG.range_end)
        await cb.answer()
        await safe_edit(cb.message, "Конец периода:", calendar_kb(day.year, day.month, prefix="scal"))
        return
    await state.update_data(range_end=day.isoformat(), period="custom")
    await state.set_state(None)
    selected = set(data.get("stats_metrics") or DEFAULT_METRICS)
    await cb.answer()
    await safe_edit(cb.message, "Показатели и вид результата:", stats_metrics_kb(selected))


@router.callback_query(F.data.startswith("scal:"), StatsSG.range_end)
async def stats_date_end(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    await stats_date(cb, state, db_user)


@router.callback_query(F.data.startswith("stm:"))
async def toggle_metric(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    key = cb.data.split(":")[1]
    data = await state.get_data()
    selected = set(data.get("stats_metrics") or DEFAULT_METRICS)
    if key in selected:
        selected.discard(key)
    else:
        selected.add(key)
    if not selected:
        selected.add(key)
    await state.update_data(stats_metrics=list(selected))
    await cb.answer()
    await safe_edit(cb.message, "Показатели и вид результата:", stats_metrics_kb(selected))


@router.callback_query(F.data.startswith("stv:"))
async def stats_view(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    mode = cb.data.split(":")[1]
    data = await state.get_data()
    period = data.get("period")
    if not period:
        await cb.answer("Сначала выберите период", show_alert=True)
        return
    bounds = _period(user, period, data)
    if bounds is None:
        await cb.answer("Период не выбран", show_alert=True)
        return
    start, end = bounds
    selected = list(data.get("stats_metrics") or DEFAULT_METRICS)
    await cb.answer("Считаю…")
    if mode == "text":
        text = await render_stats(repo, user, start, end, selected)
        from keyboards.main import stats_metrics_kb

        await safe_edit(cb.message, text[:4000], stats_metrics_kb(set(selected)))
        return
    charts = await build_charts(repo, user, start, end, selected)
    if not charts:
        await safe_edit(cb.message, "Недостаточно данных для графика.")
        return
    for title, png in charts[:8]:
        await cb.message.answer_photo(png_file(png, f"{title}.png"), caption=title)

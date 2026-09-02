"""Owner-only button-click analytics."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import Config
from database.queries import Repo
from handlers.admin import _owner, _owner_timezone
from keyboards.main import admin_clicks_kb
from services.click_charts import build_click_charts
from services.click_stats import click_period_key, click_window, render_click_report
from utils.telegram import png_file, safe_edit, safe_send

router = Router(name="admin_clicks")
logger = logging.getLogger(__name__)


def _parse_click_period(data: str | None, prefix: str) -> str:
    if data and data.startswith(prefix):
        return click_period_key(data.split(":", 1)[1] if ":" in data else "")
    return "today"


@router.callback_query(F.data == "ad:clk")
@router.callback_query(F.data.startswith("adclk:"))
async def admin_clicks(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    period = _parse_click_period(cb.data, "adclk:")
    tz_name = await _owner_timezone(repo, config)
    start, end, title = click_window(period, tz_name)
    text = await render_click_report(repo, start=start, end=end, title=title, tz_name=tz_name)
    await cb.answer()
    await safe_edit(cb.message, text, admin_clicks_kb(period))


@router.callback_query(F.data.startswith("adclkc:"))
async def admin_click_charts(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    clicks = repo.db.clicks_db
    if clicks is None:
        await cb.answer("База нажатий не подключена", show_alert=True)
        return
    period = _parse_click_period(cb.data, "adclkc:")
    tz_name = await _owner_timezone(repo, config)
    start, end, title = click_window(period, tz_name)
    await cb.answer("Строю графики")
    if cb.message is None:
        return
    try:
        charts = await build_click_charts(clicks, start, end, title, tz_name)
    except Exception:
        logger.exception("Click charts failed")
        await safe_send(cb.message.answer, "Не удалось построить графики.")
        return
    if not charts:
        await safe_send(cb.message.answer, "Нет нажатий пользователей за этот период.")
        return
    filenames = {
        "по дням": "clicks-daily.png",
        "по часам": "clicks-hourly.png",
    }
    for caption, png in charts:
        name = "clicks-kinds.png"
        for needle, filename in filenames.items():
            if needle in caption:
                name = filename
                break
        await safe_send(
            cb.message.answer_document,
            png_file(png, name),
            caption=caption[:1024],
        )

"""Notify the service owner through the same bot."""

from __future__ import annotations

import html
import logging
import traceback
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from config import Config
from database.queries import Repo
from utils.time import now_utc
from utils.timeouts import await_or_abandon

logger = logging.getLogger(__name__)

_NOTIFY_TIMEOUT = 15.0

BOT_STARTED_TEXT = "✅ Бот запущен и готов к работе."
BOT_STOPPED_TEXT = "⏹ Бот выключается."


def format_exception_reason(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc).strip()
    if isinstance(exc, TimeoutError) and not text:
        return "превышено время ожидания"
    if not text:
        return name
    if text.startswith(name):
        return text
    return f"{name}: {text}"


def format_alert(kind: str, description: str, context: str | None = None, exc: BaseException | None = None) -> str:
    ts = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = [
        "🚨 <b>Алерт сервиса</b>",
        f"Тип: {html.escape(kind)}",
        f"Время: {ts}",
        f"Описание: {html.escape(description)}",
    ]
    if context:
        parts.append(f"Контекст: {html.escape(context)}")
    if exc is not None:
        parts.append(f"Причина: {html.escape(format_exception_reason(exc))}")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        trimmed = tb[-1500:]
        parts.append(f"<pre>{html.escape(trimmed)}</pre>")
    return "\n".join(parts)


def format_backup_problems(when: str, problems: list[tuple[str, BaseException]]) -> str:
    ts = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = [
        "🚨 <b>Алерт сервиса</b>",
        "Тип: backup",
        f"Время: {ts}",
        f"Не удалось сделать или отправить бэкап {html.escape(when)}.",
    ]
    for action, exc in problems:
        parts.append(f"Причина ({html.escape(action)}): {html.escape(format_exception_reason(exc))}")
    return "\n".join(parts)


async def notify_owner(
    bot: Bot,
    config: Config,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    kwargs: dict = {"request_timeout": int(_NOTIFY_TIMEOUT)}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        await await_or_abandon(
            bot.send_message(config.owner_id, text, **kwargs),
            _NOTIFY_TIMEOUT,
            name="notify_owner",
        )
    except TelegramForbiddenError:
        logger.error("Owner has blocked the bot; cannot send alert")
    except TimeoutError:
        logger.warning("Owner notify timed out after %.0fs", _NOTIFY_TIMEOUT)
    except Exception:
        logger.exception("Failed to notify owner")


async def send_owner_start_screen(bot: Bot, repo: Repo, config: Config) -> None:
    from handlers.common import start_payload

    try:
        user = await repo.get_user(config.owner_id)
        text, markup = start_payload(user, config, is_owner=True)
        await notify_owner(bot, config, text, reply_markup=markup)
    except Exception:
        logger.exception("Failed to send /start screen to owner")


async def notify_owner_lifecycle(
    bot: Bot,
    repo: Repo,
    config: Config,
    *,
    started: bool,
    backup_problems: list[tuple[str, BaseException]] | None = None,
) -> None:
    """Status, then the /start screen, then backup failures with reasons if any."""
    await notify_owner(bot, config, BOT_STARTED_TEXT if started else BOT_STOPPED_TEXT)
    await send_owner_start_screen(bot, repo, config)
    if backup_problems:
        when = "при запуске" if started else "при выключении"
        await notify_owner(bot, config, format_backup_problems(when, backup_problems))

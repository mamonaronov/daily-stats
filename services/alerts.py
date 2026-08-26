"""Owner notices go to the owner; service errors go to the backup group."""

from __future__ import annotations

import html
import logging
import traceback
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from config import Config
from database.database import Database
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


async def _send_notice(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    name: str,
) -> None:
    kwargs: dict = {"request_timeout": int(_NOTIFY_TIMEOUT)}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        await await_or_abandon(
            bot.send_message(chat_id, text, **kwargs),
            _NOTIFY_TIMEOUT,
            name=name,
        )
    except TelegramForbiddenError:
        logger.error("Cannot send %s to chat %s", name, chat_id)
    except TimeoutError:
        logger.warning("%s timed out after %.0fs", name, _NOTIFY_TIMEOUT)
    except Exception:
        logger.exception("Failed to send %s", name)


async def notify_owner(
    bot: Bot,
    config: Config,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    await _send_notice(bot, config.owner_id, text, reply_markup, name="notify_owner")


async def notify_alert(
    bot: Bot,
    config: Config,
    text: str,
    *,
    db: Database | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chat_id = None
    if db is not None:
        try:
            from services.telegram_backup import telegram_backup_chat

            chat_id, _ = await telegram_backup_chat(db)
        except Exception:
            logger.exception("Failed to resolve backup group for alert")
    if chat_id is None:
        logger.warning("Alert not sent: backup group is not bound")
        return
    await _send_notice(bot, chat_id, text, reply_markup, name="notify_alert")


async def send_owner_start_screen(bot: Bot, repo: Repo, config: Config) -> None:
    from handlers.common import start_payload

    try:
        user = await repo.get_user(config.owner_id)
        sleep = await repo.latest_sleep(user.telegram_id) if user else None
        text, markup = start_payload(user, config, is_owner=True, sleep=sleep)
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
    """Status, then the /start screen on startup, then backup failures with reasons if any."""
    await notify_owner(bot, config, BOT_STARTED_TEXT if started else BOT_STOPPED_TEXT)
    if started:
        await send_owner_start_screen(bot, repo, config)
    if backup_problems:
        when = "при запуске" if started else "при выключении"
        await notify_alert(bot, config, format_backup_problems(when, backup_problems), db=repo.db)

"""Notify the service owner through the same bot."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from config import Config
from utils.time import now_utc

logger = logging.getLogger(__name__)


def format_alert(kind: str, description: str, context: str | None = None, exc: BaseException | None = None) -> str:
    ts = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = [
        "🚨 <b>Алерт сервиса</b>",
        f"Тип: {kind}",
        f"Время: {ts}",
        f"Описание: {description}",
    ]
    if context:
        parts.append(f"Контекст: {context}")
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        trimmed = tb[-1500:]
        parts.append(f"<pre>{trimmed}</pre>")
    return "\n".join(parts)


async def notify_owner(bot: Bot, config: Config, text: str) -> None:
    try:
        await bot.send_message(config.owner_id, text)
    except TelegramForbiddenError:
        logger.error("Owner has blocked the bot; cannot send alert")
    except Exception:
        logger.exception("Failed to notify owner")

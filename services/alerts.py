"""Notify the service owner through the same bot."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from config import Config
from utils.time import now_utc
from utils.timeouts import await_or_abandon

logger = logging.getLogger(__name__)

_NOTIFY_TIMEOUT = 15.0


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
        await await_or_abandon(
            bot.send_message(
                config.owner_id,
                text,
                request_timeout=int(_NOTIFY_TIMEOUT),
            ),
            _NOTIFY_TIMEOUT,
            name="notify_owner",
        )
    except TelegramForbiddenError:
        logger.error("Owner has blocked the bot; cannot send alert")
    except TimeoutError:
        logger.warning("Owner notify timed out after %.0fs", _NOTIFY_TIMEOUT)
    except Exception:
        logger.exception("Failed to notify owner")

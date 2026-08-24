"""Safe Telegram message helpers."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


async def answer_with_reply_kb(
    message: Message | None,
    text: str,
    inline: InlineKeyboardMarkup | None = None,
    *,
    replace: bool = False,
) -> Message | None:
    """Send text, attach the persistent reply keyboard, then set inline buttons."""
    if message is None:
        return None
    from keyboards.main import reply_main_kb

    try:
        sent = await message.answer(text, reply_markup=reply_main_kb())
    except Exception:
        logger.exception("Failed to send message with reply keyboard")
        try:
            return await message.answer(text, reply_markup=inline)
        except Exception:
            logger.exception("Failed to send fallback message")
            return None
    if inline is not None:
        try:
            await sent.edit_reply_markup(reply_markup=inline)
        except Exception:
            logger.exception("Failed to set inline keyboard after reply keyboard")
    if replace:
        try:
            await message.delete()
        except Exception:
            logger.debug("Could not delete previous message after attaching reply keyboard", exc_info=True)
    return sent


async def safe_edit(
    message: Message | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    if message is None:
        return None
    try:
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return message
        try:
            return await message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.exception("Failed to send fallback message")
            return None
    except Exception:
        logger.exception("Failed to edit message")
        try:
            return await message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.exception("Failed to send fallback message")
            return None


async def safe_send(
    sender: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Any | None:
    try:
        return await sender(*args, **kwargs)
    except TelegramRetryAfter as exc:
        logger.warning("Flood wait: %s", exc.retry_after)
        return None
    except TelegramForbiddenError:
        raise
    except TelegramBadRequest:
        logger.exception("Bad request while sending")
        return None
    except Exception:
        logger.exception("Send failed")
        return None


def png_file(data: bytes, filename: str = "chart.png") -> BufferedInputFile:
    return BufferedInputFile(data, filename=filename)


def text_file(data: str, filename: str = "log.txt") -> BufferedInputFile:
    return BufferedInputFile(data.encode("utf-8"), filename=filename)

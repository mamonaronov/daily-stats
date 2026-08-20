"""Timeouts that return even when the inner coroutine ignores cancellation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _consume_abandoned(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("Abandoned task %s finished with %s", task.get_name(), exc)


async def await_or_abandon(awaitable: Awaitable[T], timeout: float, *, name: str = "task") -> T:
    """Wait for *awaitable*, but do not block on a hung cancel.

    SOCKS/aiohttp calls sometimes ignore cancellation and aiohttp's total
    timeout. ``asyncio.wait_for`` would then wait forever for the cancelled
    task. This helper cancels, leaves the task in the background, and returns.
    """
    if timeout <= 0:
        return await awaitable
    task = asyncio.ensure_future(awaitable)
    task.set_name(name)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()
    task.cancel()
    task.add_done_callback(_consume_abandoned)
    logger.warning("%s timed out after %.1fs and was abandoned", name, timeout)
    raise TimeoutError(f"{name} timed out after {timeout:.1f}s")


async def reset_bot_session(bot: Any) -> None:
    """Drop the shared aiohttp session so a hung SOCKS connect can be replaced."""
    session = getattr(bot, "session", None)
    close = getattr(session, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        logger.exception("Failed to reset bot session")

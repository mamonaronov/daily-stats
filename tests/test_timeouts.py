from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from utils.timeouts import await_or_abandon, reset_bot_session


async def test_await_or_abandon_returns_result():
    async def ok() -> str:
        await asyncio.sleep(0.01)
        return "done"

    assert await await_or_abandon(ok(), 1.0, name="ok") == "done"


async def test_await_or_abandon_does_not_wait_for_slow_cancellation():
    async def slow_cancel() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.4)
            raise

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="slow"):
        await await_or_abandon(slow_cancel(), 0.05, name="slow")
    assert time.monotonic() - started < 0.25
    await asyncio.sleep(0.5)


async def test_reset_bot_session_closes_when_present():
    class Session:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    bot = SimpleNamespace(session=Session())
    await reset_bot_session(bot)
    assert bot.session.closed == 1
    await reset_bot_session(object())

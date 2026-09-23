from __future__ import annotations

import asyncio
import time

from services.chart_theme import render_off_loop


async def test_render_off_loop_lets_the_event_loop_run():
    loop_progressed = asyncio.Event()

    def draw() -> str:
        deadline = time.monotonic() + 2
        while not loop_progressed.is_set():
            if time.monotonic() > deadline:
                raise AssertionError("event loop did not run during chart render")
            time.sleep(0.01)
        return "png"

    async def marker() -> None:
        await asyncio.sleep(0)
        loop_progressed.set()

    task = asyncio.create_task(marker())
    assert await render_off_loop(draw) == "png"
    await task


async def test_render_off_loop_draws_one_chart_at_a_time():
    current = 0
    peak = 0

    def draw() -> int:
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        time.sleep(0.05)
        current -= 1
        return peak

    await asyncio.gather(render_off_loop(draw), render_off_loop(draw))
    assert peak == 1

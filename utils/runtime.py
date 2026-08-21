"""Process stop/restart requested from handlers (restore, signals)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager, nullcontext

_current: RuntimeControl | None = None


def get_runtime() -> RuntimeControl | None:
    return _current


def set_runtime(runtime: RuntimeControl | None) -> None:
    global _current
    _current = runtime


def hold(reason: str):
    """Track an in-flight operation. No-op if the bot runtime is not running."""
    runtime = get_runtime()
    if runtime is None:
        return nullcontext()
    return runtime.hold(reason)


class RuntimeControl:
    def __init__(self) -> None:
        self.stop = asyncio.Event()
        self.restart = False
        self.draining = False
        self._kick: Callable[[], None] | None = None
        self._holds: dict[str, int] = {}
        self._cond = asyncio.Condition()

    def bind(self, kick: Callable[[], None]) -> None:
        self._kick = kick

    def request_stop(self) -> None:
        self.stop.set()
        if self._kick is not None:
            self._kick()

    def request_restart(self) -> None:
        self.restart = True
        self.request_stop()

    def begin_drain(self) -> None:
        self.draining = True

    def end_drain(self) -> None:
        self.draining = False

    def blockers(self) -> dict[str, int]:
        return {key: count for key, count in self._holds.items() if count > 0}

    def is_idle(self) -> bool:
        return not self.blockers()

    @asynccontextmanager
    async def hold(self, reason: str):
        async with self._cond:
            self._holds[reason] = self._holds.get(reason, 0) + 1
            self._cond.notify_all()
        try:
            yield
        finally:
            async with self._cond:
                left = self._holds.get(reason, 1) - 1
                if left <= 0:
                    self._holds.pop(reason, None)
                else:
                    self._holds[reason] = left
                self._cond.notify_all()

    async def wait_idle(self, timeout: float, quiet: float = 1.5) -> bool:
        """True when there are no holds and they stay gone for `quiet` seconds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        quiet_deadline: float | None = None
        async with self._cond:
            while True:
                now = loop.time()
                remaining = deadline - now
                if remaining <= 0:
                    return self.is_idle() and (
                        quiet <= 0 or (quiet_deadline is not None and now >= quiet_deadline)
                    )
                if self.blockers():
                    quiet_deadline = None
                    try:
                        await asyncio.wait_for(self._cond.wait(), remaining)
                    except TimeoutError:
                        return False
                    continue
                if quiet <= 0:
                    return True
                if quiet_deadline is None:
                    quiet_deadline = now + quiet
                pause = min(remaining, max(0.0, quiet_deadline - now))
                if pause <= 0:
                    return True
                try:
                    await asyncio.wait_for(self._cond.wait(), pause)
                except TimeoutError:
                    if not self.blockers() and loop.time() >= quiet_deadline:
                        return True

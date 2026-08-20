from __future__ import annotations

import asyncio

from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe


def _network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=GetMe(), message="ClientOSError")


class _Session:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class _Bot:
    def __init__(self) -> None:
        self.session = _Session()


class _Dispatcher:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def resolve_used_update_types(self) -> list[str]:
        return []

    async def start_polling(self, bot, allowed_updates=None) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise _network_error()


async def test_polling_retries_telegram_network_error():
    from bot import _start_polling_with_retry

    dp = _Dispatcher(failures=2)
    bot = _Bot()
    await _start_polling_with_retry(dp, bot, asyncio.Event(), initial_delay=0.01, max_delay=0.02)
    assert dp.calls == 3
    assert bot.session.closed == 2


async def test_polling_retry_stops_when_asked():
    from bot import _start_polling_with_retry

    dp = _Dispatcher(failures=99)
    bot = _Bot()
    stop = asyncio.Event()
    stop.set()
    await _start_polling_with_retry(dp, bot, stop, initial_delay=0.01, max_delay=0.02)
    assert dp.calls == 0


async def test_polling_retry_aborts_during_backoff():
    from bot import _start_polling_with_retry

    dp = _Dispatcher(failures=99)
    bot = _Bot()
    stop = asyncio.Event()

    async def cancel() -> None:
        await asyncio.sleep(0.02)
        stop.set()

    asyncio.create_task(cancel())
    await _start_polling_with_retry(dp, bot, stop, initial_delay=1.0, max_delay=1.0)
    assert dp.calls == 1
    assert bot.session.closed == 1


async def test_reminder_job_releases_slot_when_telegram_hangs(monkeypatch):
    import time
    from types import SimpleNamespace

    from services import jobs as jobs_mod

    hung = asyncio.Event()

    async def hanging_send(bot, repo, config, telegram_id) -> None:
        hung.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.4)
            raise

    class Session:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    class Repo:
        async def due_reminders(self, now):
            return [SimpleNamespace(telegram_id=1)]

    monkeypatch.setattr(jobs_mod, "_send_reminder", hanging_send)
    monkeypatch.setattr(jobs_mod, "_REMINDER_JOB_TIMEOUT", 0.05)
    bot = SimpleNamespace(session=Session())
    started = time.monotonic()
    await jobs_mod.reminder_job(Repo(), object(), bot)
    assert hung.is_set()
    assert time.monotonic() - started < 0.3
    assert bot.session.closed == 0
    await asyncio.sleep(0.5)


async def test_vpn_monitor_job_releases_slot_when_tick_hangs(monkeypatch):
    import time
    from types import SimpleNamespace

    from services import jobs as jobs_mod

    class Session:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    class Monitor:
        config = SimpleNamespace(vpn_monitor_timeout_seconds=0)
        probe_resets = 0

        async def tick(self, bot, repo) -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.4)
                raise

        async def reset_probe(self) -> None:
            self.probe_resets += 1

    monkeypatch.setattr(jobs_mod, "_VPN_MONITOR_JOB_SLACK", 0.05)
    bot = SimpleNamespace(session=Session())
    monitor = Monitor()
    started = time.monotonic()
    await jobs_mod.vpn_monitor_job(monitor, bot, object())
    assert time.monotonic() - started < 0.3
    assert bot.session.closed == 0
    assert monitor.probe_resets == 1
    await asyncio.sleep(0.5)

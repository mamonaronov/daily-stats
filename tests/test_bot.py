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
        self._me = None

    async def me(self):
        if self._me is None:
            self._me = await self.get_me()
        return self._me


class _Dispatcher:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def resolve_used_update_types(self) -> list[str]:
        return []

    async def start_polling(self, bot, allowed_updates=None, **kwargs) -> None:
        self.calls += 1
        self.kwargs = kwargs
        if self.calls <= self.failures:
            raise _network_error()


async def test_polling_retries_telegram_network_error():
    from bot import _start_polling_with_retry

    dp = _Dispatcher(failures=2)
    bot = _Bot()
    session = bot.session
    await _start_polling_with_retry(dp, bot, asyncio.Event(), initial_delay=0.01, max_delay=0.02)
    assert dp.calls == 3
    assert session.closed == 1
    assert bot.session is not session
    assert dp.kwargs.get("close_bot_session") is False
    assert dp.kwargs.get("handle_signals") is False
    await bot.session.close()


async def test_polling_retries_timeout_error():
    from bot import _start_polling_with_retry

    class TimeoutDispatcher(_Dispatcher):
        async def start_polling(self, bot, allowed_updates=None, **kwargs) -> None:
            self.calls += 1
            self.kwargs = kwargs
            if self.calls <= self.failures:
                raise TimeoutError("startup.get_me timed out")

    dp = TimeoutDispatcher(failures=1)
    bot = _Bot()
    session = bot.session
    await _start_polling_with_retry(dp, bot, asyncio.Event(), initial_delay=0.01, max_delay=0.02)
    assert dp.calls == 2
    assert session.closed == 1
    assert bot.session is not session
    await bot.session.close()


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
    session = bot.session
    await _start_polling_with_retry(dp, bot, stop, initial_delay=1.0, max_delay=1.0)
    assert dp.calls == 1
    assert session.closed == 1
    await bot.session.close()


async def test_wait_until_telegram_ready_succeeds():
    from bot import _wait_until_telegram_ready

    class ReadyBot(_Bot):
        async def get_me(self):
            return object()

    bot = ReadyBot()
    assert await _wait_until_telegram_ready(bot, asyncio.Event(), None, attempt_timeout=0.2)
    assert bot.session.closed == 0


async def test_wait_until_telegram_ready_retries_then_succeeds(monkeypatch):
    from bot import _wait_until_telegram_ready

    class FlakyBot(_Bot):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def get_me(self):
            self.calls += 1
            if self.calls == 1:
                raise _network_error()
            return object()

    recycled: list[str | None] = []

    async def fake_recycle(bot, proxy_url):
        recycled.append(proxy_url)
        await bot.session.close()

    monkeypatch.setattr("bot._recycle_bot_session", fake_recycle)
    bot = FlakyBot()
    ok = await _wait_until_telegram_ready(
        bot,
        asyncio.Event(),
        "socks5://127.0.0.1:11808",
        attempt_timeout=0.2,
        initial_delay=0.01,
        max_delay=0.02,
    )
    assert ok is True
    assert bot.calls == 2
    assert recycled == ["socks5://127.0.0.1:11808"]
    assert bot.session.closed == 1


async def test_wait_until_telegram_ready_stops_during_backoff(monkeypatch):
    from bot import _wait_until_telegram_ready

    class SlowBot(_Bot):
        async def get_me(self):
            raise _network_error()

    async def fake_recycle(bot, proxy_url):
        await bot.session.close()

    monkeypatch.setattr("bot._recycle_bot_session", fake_recycle)
    stop = asyncio.Event()

    async def cancel() -> None:
        await asyncio.sleep(0.02)
        stop.set()

    asyncio.create_task(cancel())
    bot = SlowBot()
    assert (
        await _wait_until_telegram_ready(
            bot, stop, None, attempt_timeout=0.05, initial_delay=1.0, max_delay=1.0
        )
        is False
    )
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
        config = SimpleNamespace(vpn_monitor_timeout_seconds=0, vpn_monitor_interval_seconds=10)
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


async def test_billing_job_releases_slot_when_tick_hangs(monkeypatch):
    import time

    from services import jobs as jobs_mod

    async def hanging_billing(repo):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.4)
            raise

    monkeypatch.setattr(jobs_mod, "run_billing_tick", hanging_billing)
    monkeypatch.setattr(jobs_mod, "_BILLING_JOB_TIMEOUT", 0.05)
    started = time.monotonic()
    await jobs_mod.billing_job(object(), object(), object())
    assert time.monotonic() - started < 0.3
    await asyncio.sleep(0.5)


def test_vpn_monitor_job_timeout_stays_below_interval():
    from types import SimpleNamespace

    from services.jobs import vpn_monitor_job_timeout

    monitor = SimpleNamespace(
        config=SimpleNamespace(vpn_monitor_timeout_seconds=8, vpn_monitor_interval_seconds=10)
    )
    assert vpn_monitor_job_timeout(monitor) == 9.0

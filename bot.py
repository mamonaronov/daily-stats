"""Single-process Telegram diary bot. Polling uses one Bot(); VPN probes use another session."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import ConfigError, load_config
from database.database import Database, DatabaseUnrecoverableError
from database.queries import Repo
from handlers import setup_routers
from middlewares import (
    CallbackIdempotencyMiddleware,
    ContextMiddleware,
    ErrorIsolationMiddleware,
    UserMiddleware,
)
from services.alerts import format_alert, notify_owner
from services.jobs import setup_scheduler
from services.reminders import restore_all_reminders
from services.billing import run_billing_tick
from utils.logging import setup_logging
from utils.timeouts import reset_bot_session
from utils.uptime import mark_bot_started

logger = logging.getLogger("bot")

_POLLING_RETRY_INITIAL = 2.0
_POLLING_RETRY_MAX = 30.0


def _bot_session(proxy_url: str | None) -> AiohttpSession | None:
    if not proxy_url:
        logger.info("telegram_proxy_disabled")
        return None
    try:
        session = AiohttpSession(proxy=proxy_url, timeout=30.0)
    except ImportError as exc:
        raise ConfigError(
            "TELEGRAM_PROXY_URL is set but aiohttp-socks is not installed"
        ) from exc
    logger.info("telegram_proxy_enabled")
    return session


async def _start_polling_with_retry(
    dp: Dispatcher,
    bot: Bot,
    stop: asyncio.Event,
    *,
    initial_delay: float = _POLLING_RETRY_INITIAL,
    max_delay: float = _POLLING_RETRY_MAX,
) -> None:
    """Retry the initial Telegram handshake; a SOCKS TLS reset must not kill the process."""
    delay = initial_delay
    while not stop.is_set():
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            return
        except TelegramNetworkError as exc:
            logger.warning("Telegram network error, retry in %.0fs: %s", delay, exc)
            await reset_bot_session(bot)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                delay = min(delay * 2, max_delay)


async def _on_error(event, bot: Bot, config) -> None:
    exc = event.exception
    logger.exception("Dispatcher error")
    await notify_owner(bot, config, format_alert("dispatcher", "Ошибка диспетчера", exc=exc))


async def graceful_shutdown(bot: Bot, db: Database, scheduler: AsyncIOScheduler, config) -> None:
    logger.info("Graceful shutdown started")
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("Scheduler shutdown failed")
    try:
        await db.backup(prefix="shutdown")
    except Exception:
        logger.exception("Shutdown backup failed")
    try:
        await bot.session.close()
    except Exception:
        logger.exception("Bot session close failed")
    try:
        await db.close()
    except Exception:
        logger.exception("DB close failed")
    logger.info("Shutdown complete")


async def run() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    setup_logging(config.log_level)
    mark_bot_started()
    logger.info("Starting daily-stats bot")

    try:
        session = _bot_session(config.telegram_proxy_url)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    db = Database(config)
    # One Bot for the whole process — polling, admin, alerts.
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )

    try:
        await db.initialize()
    except DatabaseUnrecoverableError as exc:
        logger.critical("Database unrecoverable: %s", exc)
        await notify_owner(bot, config, format_alert("database", str(exc), exc=exc))
        await bot.session.close()
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.exception("Startup database failure")
        try:
            await db.backup(prefix="crash")
        except Exception:
            logger.exception("Crash backup failed")
        await notify_owner(bot, config, format_alert("startup", "Ошибка инициализации БД", exc=exc))
        await bot.session.close()
        raise SystemExit(1) from exc

    repo = Repo(db)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    scheduler = AsyncIOScheduler(timezone="UTC")

    dp.update.outer_middleware(ContextMiddleware(repo, config, scheduler, bot))
    dp.update.outer_middleware(ErrorIsolationMiddleware())
    dp.update.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(CallbackIdempotencyMiddleware())
    dp.include_router(setup_routers())

    @dp.error()
    async def dispatcher_error(event) -> None:
        await _on_error(event, bot, config)

    try:
        await run_billing_tick(repo)
        restored = await restore_all_reminders(repo, config)
        logger.info("Reminders restored: %s", restored)
        setup_scheduler(scheduler, bot, repo, db, config)
        scheduler.start()
    except Exception as exc:
        logger.exception("Failed to start background jobs")
        await notify_owner(bot, config, format_alert("scheduler", "Ошибка запуска задач", exc=exc))

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _ask_stop() -> None:
        stop.set()
        loop.create_task(dp.stop_polling())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _ask_stop)
        except NotImplementedError:
            pass

    logger.info("Polling started")
    try:
        await _start_polling_with_retry(dp, bot, stop)
    finally:
        await graceful_shutdown(bot, db, scheduler, config)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("bot").exception("Fatal error")
        raise SystemExit(1)

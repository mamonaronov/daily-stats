"""Single-process Telegram diary bot. Polling uses one Bot(); VPN probes use another session."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import ConfigError, load_config
from database.database import Database, DatabaseUnrecoverableError
from database.queries import Repo
from handlers import setup_routers
from middlewares import (
    CallbackIdempotencyMiddleware,
    ContextMiddleware,
    ErrorIsolationMiddleware,
    SpamWatchMiddleware,
    UserMiddleware,
)
from services.alerts import format_alert, format_backup_problems, notify_alert, notify_owner, notify_owner_lifecycle
from services.spam_watch import SpamWatch, set_spam_watch
from services.jobs import setup_scheduler
from services.billing import run_billing_tick
from services.telegram_restore import RestoreError, apply_pending_telegram_restore, format_restore_done
from utils.logging import setup_logging
from utils.runtime import RuntimeControl, set_runtime
from utils.telegram_session import make_telegram_session
from utils.timeouts import await_or_abandon, reset_bot_session
from utils.uptime import mark_bot_started

logger = logging.getLogger("bot")

_POLLING_RETRY_INITIAL = 2.0
_POLLING_RETRY_MAX = 30.0
_POLLING_SESSION_TIMEOUT = 60.0
_STARTUP_GET_ME_TIMEOUT = 20.0
_SHUTDOWN_TELEGRAM_BACKUP_TIMEOUT = 15.0


def _make_session(proxy_url: str | None):
    return make_telegram_session(proxy_url, _POLLING_SESSION_TIMEOUT)


def _bot_session(proxy_url: str | None):
    if not proxy_url:
        logger.info("telegram_proxy_disabled")
    try:
        session = _make_session(proxy_url)
    except ImportError as exc:
        raise ConfigError(
            "TELEGRAM_PROXY_URL is set but aiohttp-socks is not installed"
        ) from exc
    if proxy_url:
        logger.info("telegram_proxy_enabled")
    return session


async def _recycle_bot_session(bot: Bot, proxy_url: str | None) -> None:
    """Drop a hung SOCKS session and attach a fresh one before the next attempt."""
    await reset_bot_session(bot)
    try:
        bot.session = _make_session(proxy_url)
    except Exception:
        logger.exception("Failed to recreate bot session")


async def _wait_until_telegram_ready(
    bot: Bot,
    stop: asyncio.Event,
    proxy_url: str | None,
    *,
    attempt_timeout: float = _STARTUP_GET_ME_TIMEOUT,
    initial_delay: float = _POLLING_RETRY_INITIAL,
    max_delay: float = _POLLING_RETRY_MAX,
) -> bool:
    """Block until getMe works. start_polling then uses the cached Bot.me()."""
    delay = initial_delay
    while not stop.is_set():
        try:
            await await_or_abandon(bot.me(), attempt_timeout, name="startup.get_me")
            logger.info("Telegram API reachable")
            return True
        except (TimeoutError, TelegramNetworkError) as exc:
            logger.warning("Telegram not reachable, retry in %.0fs: %s", delay, exc)
        except Exception:
            logger.exception("Telegram handshake failed")
        await _recycle_bot_session(bot, proxy_url)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
            return False
        except asyncio.TimeoutError:
            delay = min(delay * 2, max_delay)
    return False


async def _start_polling_with_retry(
    dp: Dispatcher,
    bot: Bot,
    stop: asyncio.Event,
    *,
    proxy_url: str | None = None,
    initial_delay: float = _POLLING_RETRY_INITIAL,
    max_delay: float = _POLLING_RETRY_MAX,
) -> None:
    """Retry the initial Telegram handshake; a SOCKS TLS reset must not kill the process."""
    delay = initial_delay
    while not stop.is_set():
        try:
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                handle_signals=False,
                close_bot_session=False,
            )
            return
        except (TelegramNetworkError, TimeoutError) as exc:
            logger.warning("Telegram network error, retry in %.0fs: %s", delay, exc)
            await _recycle_bot_session(bot, proxy_url)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                delay = min(delay * 2, max_delay)


async def _on_error(event, bot: Bot, config, db: Database | None = None) -> None:
    exc = event.exception
    logger.exception("Dispatcher error")
    await notify_alert(bot, config, format_alert("dispatcher", "Ошибка диспетчера", exc=exc), db=db)


async def graceful_shutdown(
    bot: Bot,
    db: Database,
    scheduler: AsyncIOScheduler,
    config,
    repo: Repo | None = None,
    *,
    notify: bool = False,
) -> None:
    logger.info("Graceful shutdown started")
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("Scheduler shutdown failed")
    backup_problems: list[tuple[str, BaseException]] = []
    try:
        await db.backup(prefix="shutdown")
    except Exception as exc:
        logger.exception("Shutdown backup failed")
        backup_problems.append(("сделать копию на диск", exc))
    if notify and repo is not None:
        await notify_owner_lifecycle(bot, repo, config, started=False)
        if config.telegram_backup_interval_minutes > 0:
            try:
                from services.telegram_backup import send_telegram_backup, telegram_backup_chat

                chat_id, _ = await telegram_backup_chat(db)
                if chat_id is not None:
                    await await_or_abandon(
                        send_telegram_backup(db, bot, config, silent=False),
                        _SHUTDOWN_TELEGRAM_BACKUP_TIMEOUT,
                        name="shutdown.telegram_backup",
                    )
            except Exception as exc:
                logger.exception("Shutdown telegram backup failed")
                backup_problems.append(("отправить бэкап в Telegram", exc))
        if backup_problems:
            await notify_alert(
                bot, config, format_backup_problems("при выключении", backup_problems), db=db
            )
    elif backup_problems:
        await notify_alert(
            bot, config, format_backup_problems("при выключении", backup_problems), db=db
        )
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

    restore_preview = None
    try:
        restore_preview = await apply_pending_telegram_restore(config)
        if restore_preview is not None:
            logger.info(
                "Applied pending telegram restore db=v%s users=%s",
                restore_preview.db_version,
                restore_preview.users_count,
            )
        await db.initialize()
    except RestoreError as exc:
        logger.critical("Telegram restore failed: %s", exc)
        await notify_alert(bot, config, format_alert("restore", str(exc), exc=exc), db=db)
        await bot.session.close()
        raise SystemExit(1) from exc
    except DatabaseUnrecoverableError as exc:
        logger.critical("Database unrecoverable: %s", exc)
        await notify_alert(bot, config, format_alert("database", str(exc), exc=exc), db=db)
        await bot.session.close()
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.exception("Startup database failure")
        backup_error: BaseException | None = None
        try:
            await db.backup(prefix="crash")
        except Exception as backup_exc:
            logger.exception("Crash backup failed")
            backup_error = backup_exc
        await notify_alert(
            bot, config, format_alert("startup", "Ошибка инициализации БД", exc=exc), db=db
        )
        if backup_error is not None:
            await notify_alert(
                bot,
                config,
                format_alert("backup", "Не удалось сделать бэкап после ошибки запуска", exc=backup_error),
                db=db,
            )
        await bot.session.close()
        raise SystemExit(1) from exc

    repo = Repo(db)
    from database.fsm_storage import SqliteStorage

    storage = SqliteStorage(db)
    dp = Dispatcher(storage=storage)
    scheduler = AsyncIOScheduler(timezone="UTC")
    runtime = RuntimeControl()
    set_runtime(runtime)
    set_spam_watch(SpamWatch(bot, repo, config))

    dp.update.outer_middleware(ContextMiddleware(repo, config, scheduler, bot, runtime))
    dp.update.outer_middleware(ErrorIsolationMiddleware())
    dp.update.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(CallbackIdempotencyMiddleware())
    dp.callback_query.outer_middleware(SpamWatchMiddleware())
    dp.include_router(setup_routers())

    @dp.error()
    async def dispatcher_error(event) -> None:
        await _on_error(event, bot, config, db)

    loop = asyncio.get_running_loop()

    def _ask_stop() -> None:
        loop.create_task(dp.stop_polling())

    runtime.bind(_ask_stop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:
            pass

    logger.info("Waiting for Telegram API")
    telegram_ok = await _wait_until_telegram_ready(bot, runtime.stop, config.telegram_proxy_url)
    if runtime.stop.is_set() or not telegram_ok:
        set_spam_watch(None)
        set_runtime(None)
        await graceful_shutdown(bot, db, scheduler, config, repo)
        return

    if restore_preview is not None:
        await notify_owner(bot, config, format_restore_done(restore_preview))

    try:
        await run_billing_tick(repo)
        setup_scheduler(scheduler, bot, repo, db, config)
        scheduler.start()
    except Exception as exc:
        logger.exception("Failed to start background jobs")
        await notify_alert(
            bot, config, format_alert("scheduler", "Ошибка запуска задач", exc=exc), db=db
        )

    became_ready = False

    @dp.startup.register
    async def _on_bot_ready() -> None:
        nonlocal became_ready
        if became_ready:
            return
        became_ready = True
        logger.info("Polling started")
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Регистрация и вход"),
                    BotCommand(command="menu", description="Главный экран"),
                    BotCommand(command="today", description="Сводка дня"),
                    BotCommand(command="stats", description="Статистика"),
                    BotCommand(command="guide", description="Как пользоваться ботом"),
                ]
            )
        except Exception:
            logger.exception("Failed to set bot commands")
        try:
            await notify_owner_lifecycle(bot, repo, config, started=True)
        except Exception:
            logger.exception("Failed to send ready notification")

    try:
        await _start_polling_with_retry(dp, bot, runtime.stop, proxy_url=config.telegram_proxy_url)
    finally:
        set_spam_watch(None)
        set_runtime(None)
        await graceful_shutdown(bot, db, scheduler, config, repo, notify=became_ready)
    if runtime.restart:
        logger.info("Exiting for restart after restore")
        raise SystemExit(0)


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

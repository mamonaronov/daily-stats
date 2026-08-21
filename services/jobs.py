"""Periodic jobs: billing, reminders, backup, telegram backup, cleanup."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from database.database import Database
from database.queries import Repo
from services.alerts import format_alert, notify_owner
from services.billing import run_billing_tick
from services.reminders import refresh_user_reminder, user_filled_day_review
from utils.time import now_utc, to_iso, user_today
from utils.timeouts import await_or_abandon
from utils.runtime import get_runtime, hold

logger = logging.getLogger(__name__)

_TELEGRAM_BACKUP_START_DELAY = timedelta(seconds=5)
_TELEGRAM_BACKUP_RETRY = timedelta(minutes=15)
_REMINDER_JOB_TIMEOUT = 45.0
_TELEGRAM_BACKUP_SEND_TIMEOUT = 180.0
_REMINDER_SEND_TIMEOUT = 20
_VPN_MONITOR_JOB_SLACK = 5.0


def _skip_if_draining() -> bool:
    runtime = get_runtime()
    return runtime is not None and runtime.draining


def reschedule_telegram_backup(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    db: Database,
    config: Config,
    last_sent: datetime | None,
) -> None:
    if config.telegram_backup_interval_hours <= 0:
        try:
            scheduler.remove_job("telegram_backup")
        except Exception:
            pass
        return
    from services.telegram_backup import next_telegram_backup_at

    when = next_telegram_backup_at(last_sent, config.telegram_backup_interval_hours)
    _schedule_telegram_backup_at(scheduler, bot, db, config, when)


def _schedule_telegram_backup_at(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    db: Database,
    config: Config,
    when: datetime,
) -> None:
    grace = max(3600, config.telegram_backup_interval_hours * 3600)
    scheduler.add_job(
        telegram_backup_job,
        "date",
        run_date=when,
        id="telegram_backup",
        replace_existing=True,
        kwargs={"scheduler": scheduler, "db": db, "bot": bot, "config": config},
        misfire_grace_time=grace,
        next_run_time=when,
    )


def setup_scheduler(scheduler: AsyncIOScheduler, bot: Bot, repo: Repo, db: Database, config: Config) -> None:
    scheduler.add_job(
        billing_job,
        "interval",
        minutes=config.billing_check_minutes,
        id="billing",
        replace_existing=True,
        kwargs={"repo": repo, "config": config, "bot": bot},
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        reminder_job,
        "interval",
        minutes=config.reminder_check_minutes,
        id="reminders",
        replace_existing=True,
        kwargs={"repo": repo, "config": config, "bot": bot},
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(60, config.reminder_check_minutes * 60),
    )
    scheduler.add_job(
        backup_job,
        "interval",
        hours=config.backup_interval_hours,
        id="backup",
        replace_existing=True,
        kwargs={"db": db, "bot": bot, "config": config},
        max_instances=1,
        coalesce=True,
    )
    if config.telegram_backup_interval_hours > 0:
        _schedule_telegram_backup_at(
            scheduler,
            bot,
            db,
            config,
            datetime.now(timezone.utc) + _TELEGRAM_BACKUP_START_DELAY,
        )
    scheduler.add_job(
        cleanup_job,
        "interval",
        hours=6,
        id="cleanup",
        replace_existing=True,
        kwargs={"repo": repo},
        max_instances=1,
        coalesce=True,
    )
    if config.vpn_monitor_enabled:
        from services.vpn_monitor import VpnMonitor, make_probe_bot

        monitor = VpnMonitor(config, probe_bot=make_probe_bot(config))
        scheduler.add_job(
            vpn_monitor_job,
            "interval",
            seconds=max(5, config.vpn_monitor_interval_seconds),
            id="vpn_monitor",
            replace_existing=True,
            kwargs={"monitor": monitor, "bot": bot, "repo": repo},
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(1, config.vpn_monitor_interval_seconds - 1),
            next_run_time=datetime.now(timezone.utc)
            + timedelta(seconds=max(5, config.vpn_monitor_interval_seconds)),
        )
        logger.info(
            "VPN monitor enabled interval=%ss group=%s",
            config.vpn_monitor_interval_seconds,
            config.mihomo_proxy_group,
        )


async def billing_job(repo: Repo, config: Config, bot: Bot) -> None:
    if _skip_if_draining():
        return
    async with hold("billing"):
        try:
            stats = await run_billing_tick(repo)
            logger.info("Billing tick %s", stats)
        except Exception as exc:
            logger.exception("Billing tick failed")
            await notify_owner(bot, config, format_alert("billing", "Сбой ежедневного списания", exc=exc))


async def reminder_job(repo: Repo, config: Config, bot: Bot) -> None:
    if _skip_if_draining():
        return
    async with hold("reminder"):
        try:
            await await_or_abandon(
                _reminder_tick(repo, config, bot),
                _REMINDER_JOB_TIMEOUT,
                name="reminder_job",
            )
        except TimeoutError:
            logger.warning(
                "Reminder job timed out after %.0fs; polling session left intact",
                _REMINDER_JOB_TIMEOUT,
            )
        except Exception as exc:
            logger.exception("Reminder tick failed")
            await notify_owner(bot, config, format_alert("reminder", "Сбой рассылки напоминаний", exc=exc))


async def _reminder_tick(repo: Repo, config: Config, bot: Bot) -> None:
    due = await repo.due_reminders(to_iso(now_utc()))
    for reminder in due:
        try:
            await _send_reminder(bot, repo, config, reminder.telegram_id)
        except Exception:
            logger.exception("Reminder failed for %s", reminder.telegram_id)


async def _send_reminder(bot: Bot, repo: Repo, config: Config, telegram_id: int) -> None:
    user = await repo.get_user(telegram_id)
    if user is None or not user.is_active or not user.reminders_enabled:
        return
    local_today = user_today(user.timezone).isoformat()
    reminder = await repo.get_reminder(telegram_id)
    if reminder and reminder.last_sent_local_date == local_today:
        await refresh_user_reminder(repo, user, config)
        return
    if await user_filled_day_review(repo, user):
        await refresh_user_reminder(repo, user, config)
        return
    from keyboards.main import main_menu
    from utils.callbacks import NAV_DAY
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from keyboards.main import _btn, with_nav

    try:
        b = InlineKeyboardBuilder()
        b.row(_btn("🌙 Оценить день", NAV_DAY))
        await bot.send_message(
            telegram_id,
            "Как прошёл день? Отметьте настроение и самочувствие.",
            reply_markup=with_nav(b),
            request_timeout=_REMINDER_SEND_TIMEOUT,
        )
    except TelegramForbiddenError:
        await repo.mark_bot_blocked(telegram_id)
        logger.info("User %s blocked the bot", telegram_id)
        return
    next_when = await refresh_user_reminder(repo, user, config)
    await repo.mark_reminder_sent(
        telegram_id,
        to_iso(now_utc()),
        local_today,
        to_iso(next_when) if next_when else to_iso(now_utc() + timedelta(days=1)),
    )


async def backup_job(db: Database, bot: Bot, config: Config) -> None:
    if _skip_if_draining():
        return
    try:
        path = await db.backup(prefix="scheduled")
        logger.info("Scheduled backup %s", path.name)
    except Exception as exc:
        logger.exception("Scheduled backup failed")
        await notify_owner(bot, config, format_alert("backup", "Не удалось сделать бэкап на диск", exc=exc))


async def telegram_backup_job(
    scheduler: AsyncIOScheduler,
    db: Database,
    bot: Bot,
    config: Config,
) -> None:
    from services.telegram_backup import (
        last_telegram_backup_at,
        next_telegram_backup_at,
        send_telegram_backup,
        telegram_backup_due,
    )

    interval = config.telegram_backup_interval_hours
    if _skip_if_draining():
        _schedule_telegram_backup_at(scheduler, bot, db, config, now_utc() + timedelta(seconds=30))
        return
    try:
        last = await last_telegram_backup_at(db)
        if not telegram_backup_due(last, interval):
            when = next_telegram_backup_at(last, interval)
            logger.info("Telegram backup not due, next at %s", when.isoformat())
            _schedule_telegram_backup_at(scheduler, bot, db, config, when)
            return
        async with hold("telegram_backup"):
            await await_or_abandon(
                send_telegram_backup(db, bot, config),
                _TELEGRAM_BACKUP_SEND_TIMEOUT,
                name="telegram_backup",
            )
        when = now_utc() + timedelta(hours=interval)
        logger.info("Telegram backup next at %s", when.isoformat())
        _schedule_telegram_backup_at(scheduler, bot, db, config, when)
    except Exception as exc:
        logger.exception("Telegram backup failed")
        await notify_owner(
            bot,
            config,
            format_alert("telegram_backup", "Не удалось сделать или отправить бэкап в Telegram", exc=exc),
        )
        _schedule_telegram_backup_at(
            scheduler,
            bot,
            db,
            config,
            now_utc() + _TELEGRAM_BACKUP_RETRY,
        )


async def cleanup_job(repo: Repo) -> None:
    if _skip_if_draining():
        return
    try:
        threshold = now_utc() - timedelta(days=2)
        await repo.cleanup_callbacks(to_iso(threshold))
    except Exception:
        logger.exception("Callback cleanup failed")


async def vpn_monitor_job(monitor, bot: Bot, repo: Repo) -> None:
    if _skip_if_draining():
        return
    timeout = float(monitor.config.vpn_monitor_timeout_seconds) + _VPN_MONITOR_JOB_SLACK
    try:
        await await_or_abandon(monitor.tick(bot, repo), timeout, name="vpn_monitor_job")
    except TimeoutError:
        logger.warning(
            "VPN monitor tick timed out after %.0fs, resetting probe session",
            timeout,
        )
        await monitor.reset_probe()
    except Exception:
        logger.exception("VPN monitor tick failed")

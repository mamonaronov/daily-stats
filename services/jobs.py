"""Periodic jobs: billing, reminders, backup, cleanup."""

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

logger = logging.getLogger(__name__)


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
        from services.vpn_monitor import VpnMonitor

        monitor = VpnMonitor(config)
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
            next_run_time=datetime.now(timezone.utc),
        )
        logger.info(
            "VPN monitor enabled interval=%ss group=%s",
            config.vpn_monitor_interval_seconds,
            config.mihomo_proxy_group,
        )


async def billing_job(repo: Repo, config: Config, bot: Bot) -> None:
    try:
        stats = await run_billing_tick(repo)
        logger.info("Billing tick %s", stats)
    except Exception as exc:
        logger.exception("Billing tick failed")
        await notify_owner(bot, config, format_alert("billing", "Сбой ежедневного списания", exc=exc))


async def reminder_job(repo: Repo, config: Config, bot: Bot) -> None:
    try:
        due = await repo.due_reminders(to_iso(now_utc()))
        for reminder in due:
            try:
                await _send_reminder(bot, repo, config, reminder.telegram_id)
            except Exception:
                logger.exception("Reminder failed for %s", reminder.telegram_id)
    except Exception as exc:
        logger.exception("Reminder tick failed")
        await notify_owner(bot, config, format_alert("reminder", "Сбой рассылки напоминаний", exc=exc))


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
    try:
        path = await db.backup(prefix="scheduled")
        logger.info("Scheduled backup %s", path.name)
    except Exception as exc:
        logger.exception("Scheduled backup failed")
        await notify_owner(bot, config, format_alert("backup", "Сбой резервного копирования", exc=exc))


async def cleanup_job(repo: Repo) -> None:
    try:
        threshold = now_utc() - timedelta(days=2)
        await repo.cleanup_callbacks(to_iso(threshold))
    except Exception:
        logger.exception("Callback cleanup failed")


async def vpn_monitor_job(monitor, bot: Bot, repo: Repo) -> None:
    try:
        await monitor.tick(bot, repo)
    except Exception:
        logger.exception("VPN monitor tick failed")

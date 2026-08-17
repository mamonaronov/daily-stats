"""Reminder time: 3 hours before average bedtime of last 3 days."""

from __future__ import annotations

import logging
from datetime import datetime

from config import Config
from database.models import User
from database.queries import Repo
from utils.time import (
    circular_mean_minutes,
    hhmm_to_minutes,
    minutes_of_day,
    next_local_datetime,
    now_utc,
    parse_iso,
    to_iso,
    user_today,
)

logger = logging.getLogger(__name__)


def average_bedtime_minutes(bedtimes_utc: list[str], tz_name: str) -> int | None:
    values = [minutes_of_day(parse_iso(item), tz_name) for item in bedtimes_utc]
    return circular_mean_minutes(values)


def reminder_clock_minutes(
    avg_bedtime: int | None,
    hours_before: int,
    fallback_hhmm: str,
) -> int:
    if avg_bedtime is None:
        return hhmm_to_minutes(fallback_hhmm)
    return (avg_bedtime - hours_before * 60) % (24 * 60)


def next_reminder_at(user: User, clock_minutes: int, after: datetime | None = None) -> datetime:
    hour, minute = divmod(clock_minutes, 60)
    return next_local_datetime(user.timezone, hour, minute, after=after)


async def compute_clock_minutes(repo: Repo, user: User, config: Config) -> int:
    records = await repo.last_completed_sleep(user.telegram_id, limit=3)
    bedtimes = [item.bedtime for item in records if item.bedtime]
    avg = average_bedtime_minutes(bedtimes, user.timezone) if len(bedtimes) >= 3 else None
    fallback = user.default_sleep_time or config.default_sleep_time
    # If no sleep average, remind at fallback evening time, not sleep-3h of default sleep.
    if avg is None:
        return hhmm_to_minutes(config.reminder_fallback_time)
    return reminder_clock_minutes(avg, config.reminder_hours_before_sleep, fallback)


async def refresh_user_reminder(repo: Repo, user: User, config: Config) -> datetime | None:
    if user.is_deleted or user.status != "active" or not user.reminders_enabled:
        await repo.upsert_reminder(user.telegram_id, to_iso(now_utc()), enabled=0)
        return None
    clock = await compute_clock_minutes(repo, user, config)
    when = next_reminder_at(user, clock)
    await repo.upsert_reminder(user.telegram_id, to_iso(when), enabled=1)
    return when


async def restore_all_reminders(repo: Repo, config: Config) -> int:
    users = await repo.list_reminder_users()
    count = 0
    for user in users:
        try:
            await refresh_user_reminder(repo, user, config)
            count += 1
        except Exception:
            logger.exception("Failed to restore reminder for %s", user.telegram_id)
    return count


async def user_filled_day_review(repo: Repo, user: User) -> bool:
    from utils.time import day_bounds_utc, to_iso

    today = user_today(user.timezone)
    start, end = day_bounds_utc(user.timezone, today)
    moods = await repo.list_mood(user.telegram_id, to_iso(start), to_iso(end))
    wbs = await repo.list_wellbeing(user.telegram_id, to_iso(start), to_iso(end))
    return bool(moods) and bool(wbs)

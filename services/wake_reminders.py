"""Daily reminder to log getting out of bed if the user forgot."""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database.models import User
from database.queries import Repo
from keyboards.main import wake_up_reminder_kb
from utils.time import day_bounds_utc, parse_hhmm, to_iso, user_now

logger = logging.getLogger(__name__)

REMINDER_TEXT = (
    "Ещё не отметили, во сколько встали. Если уже встали — запишите время."
)


def is_wake_reminder_due(
    reminder_hhmm: str | None,
    sent_on: str | None,
    local_now: datetime,
    has_out_of_bed_today: bool,
) -> bool:
    if not reminder_hhmm or has_out_of_bed_today:
        return False
    if sent_on == local_now.date().isoformat():
        return False
    try:
        hour, minute = parse_hhmm(reminder_hhmm)
    except ValueError:
        return False
    if local_now.hour * 60 + local_now.minute < hour * 60 + minute:
        return False
    return True


async def _has_out_of_bed_today(repo: Repo, user: User, local_now: datetime) -> bool:
    start, end = day_bounds_utc(user.timezone, local_now.date())
    return await repo.has_out_of_bed_between(user.telegram_id, to_iso(start), to_iso(end))


async def send_wake_up_reminders(repo: Repo, bot: Bot) -> int:
    sent = 0
    for user in await repo.list_wake_reminder_users():
        local_now = user_now(user.timezone)
        has_up = await _has_out_of_bed_today(repo, user, local_now)
        if not is_wake_reminder_due(
            user.wake_up_reminder_time,
            user.wake_up_reminder_sent_on,
            local_now,
            has_up,
        ):
            continue
        sleep = await repo.latest_sleep(user.telegram_id)
        try:
            await bot.send_message(
                user.telegram_id,
                REMINDER_TEXT,
                reply_markup=wake_up_reminder_kb(sleep),
            )
        except TelegramForbiddenError:
            await repo.mark_bot_blocked(user.telegram_id)
            continue
        except TelegramBadRequest as exc:
            if "chat not found" in str(exc).lower() or "blocked" in str(exc).lower():
                await repo.mark_bot_blocked(user.telegram_id)
            else:
                logger.warning("Wake reminder bad request for %s: %s", user.telegram_id, exc)
            continue
        except Exception:
            logger.exception("Failed to send wake reminder to %s", user.telegram_id)
            continue
        await repo.mark_wake_up_reminder_sent(user.telegram_id, local_now.date().isoformat())
        sent += 1
    return sent

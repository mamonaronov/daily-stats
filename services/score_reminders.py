"""Daily reminder to rate the day if the user forgot."""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database.models import User
from database.queries import Repo
from keyboards.main import daily_score_reminder_kb
from services.daily_scores import SCORE_BY_KEY, tracked_score_keys
from services.ui_prefs import prefs_of
from utils.time import is_hhmm_due, user_now

logger = logging.getLogger(__name__)

REMINDER_TEXT = "Пора оценить сегодняшний день. Если уже оценили — можно поменять."


def is_score_reminder_due(
    reminder_hhmm: str | None,
    sent_on: str | None,
    local_now: datetime,
    missing_keys: list[str],
) -> bool:
    if not missing_keys:
        return False
    return is_hhmm_due(reminder_hhmm, sent_on, local_now)


def reminder_text(missing_keys: list[str]) -> str:
    labels = [SCORE_BY_KEY[key].label.lower() for key in missing_keys if key in SCORE_BY_KEY]
    if not labels:
        return REMINDER_TEXT
    if len(labels) == 1:
        return f"Пора оценить сегодняшний день. Ещё нет: {labels[0]}."
    return "Пора оценить сегодняшний день. Ещё нет: " + ", ".join(labels) + "."


async def _missing_score_keys(repo: Repo, user: User, local_now: datetime) -> list[str]:
    keys = tracked_score_keys(prefs_of(user).tracked)
    if not keys:
        return []
    rows = await repo.list_daily_scores_for_day(user.telegram_id, local_now.date().isoformat())
    have = {rec.kind for rec in rows}
    return [key for key in keys if key not in have]


async def send_daily_score_reminders(repo: Repo, bot: Bot) -> int:
    sent = 0
    for user in await repo.list_daily_score_reminder_users():
        local_now = user_now(user.timezone)
        missing = await _missing_score_keys(repo, user, local_now)
        if not is_score_reminder_due(
            user.daily_score_reminder_time,
            user.daily_score_reminder_sent_on,
            local_now,
            missing,
        ):
            continue
        try:
            await bot.send_message(
                user.telegram_id,
                reminder_text(missing),
                reply_markup=daily_score_reminder_kb(),
            )
        except TelegramForbiddenError:
            await repo.mark_bot_blocked(user.telegram_id)
            continue
        except TelegramBadRequest as exc:
            if "chat not found" in str(exc).lower() or "blocked" in str(exc).lower():
                await repo.mark_bot_blocked(user.telegram_id)
            else:
                logger.warning("Score reminder bad request for %s: %s", user.telegram_id, exc)
            continue
        except Exception:
            logger.exception("Failed to send score reminder to %s", user.telegram_id)
            continue
        await repo.mark_daily_score_reminder_sent(user.telegram_id, local_now.date().isoformat())
        sent += 1
    return sent

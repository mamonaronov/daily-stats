"""Low-balance coverage notices. Once a day per person, never at write-block."""

from __future__ import annotations

import logging

from aiogram import Bot

from config import Config
from database.queries import Repo
from services.alerts import notify_owner
from services.ui_prefs import prefs_of, save_prefs
from utils.formatting import coverage
from utils.time import user_today

logger = logging.getLogger(__name__)


async def send_coverage_notices(repo: Repo, bot: Bot, config: Config) -> None:
    digest: list[str] = []
    for user in await repo.list_active_billable():
        today = user_today(user.timezone)
        days, _until = coverage(user.balance, user.daily_price, today, user.paid_until_date)
        if days not in (2, 3):
            continue
        local = today.isoformat()
        prefs = prefs_of(user)
        digest.append(f"{user.display_name} ({user.telegram_id}): {days} дн.")
        if prefs.low_balance_notice_on == local:
            continue
        try:
            await bot.send_message(
                user.telegram_id,
                f"Покрытия осталось на {days} дн. Если уже оплатили — напишите «Я оплатил» в балансе.",
            )
        except Exception:
            logger.exception("Failed to send coverage notice to %s", user.telegram_id)
            continue
        prefs.low_balance_notice_on = local
        await save_prefs(repo, user, prefs)

    if not digest:
        return
    stamp = user_today("UTC").isoformat()
    owner = await repo.get_user(config.owner_id)
    if owner is not None:
        owner_prefs = prefs_of(owner)
        if owner_prefs.owner_digest_on == stamp:
            return
        owner_prefs.owner_digest_on = stamp
        await save_prefs(repo, owner, owner_prefs)
    await notify_owner(bot, config, "Скоро кончится покрытие:\n" + "\n".join(digest))

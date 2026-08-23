"""Idempotent daily billing in each user's timezone."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from database.models import User
from database.queries import Repo
from services import balance as balance_svc
from services.balance import SYSTEM_ACTOR
from utils.time import now_utc, user_today

logger = logging.getLogger(__name__)


def charge_key(telegram_id: int, day: date) -> str:
    return f"daily:{telegram_id}:{day.isoformat()}"


def days_to_charge(user: User, today: date) -> list[date]:
    if user.last_charge_date:
        start = date.fromisoformat(user.last_charge_date) + timedelta(days=1)
    else:
        from utils.time import local_date_of, parse_iso

        start = local_date_of(parse_iso(user.registered_at), user.timezone)
    if start > today:
        return []
    days: list[date] = []
    current = start
    while current <= today:
        days.append(current)
        current += timedelta(days=1)
    return days


async def charge_user_for_day(repo: Repo, user: User, day: date) -> str:
    key = charge_key(user.telegram_id, day)
    if user.daily_price <= 0:
        applied, before, after = await repo.apply_balance_change(
            user.telegram_id,
            "debit",
            delta=0,
            comment=f"Бесплатный день {day.isoformat()}",
            performed_by=SYSTEM_ACTOR,
            idempotency_key=key,
            paid_until_date=day.isoformat(),
            last_charge_date=day.isoformat(),
        )
        return "free" if applied else "duplicate"

    if user.balance >= user.daily_price:
        applied, before, after = await balance_svc.debit(
            repo,
            user.telegram_id,
            user.daily_price,
            comment=f"Ежедневное списание за {day.isoformat()}",
            performed_by=SYSTEM_ACTOR,
            idempotency_key=key,
            paid_until_date=day.isoformat(),
            last_charge_date=day.isoformat(),
        )
        logger.info(
            "Charged user %s day=%s %s -> %s applied=%s",
            user.telegram_id,
            day,
            before,
            after,
            applied,
        )
        return "charged" if applied else "duplicate"

    amount = max(0.0, float(user.balance))
    applied, before, after = await repo.apply_balance_change(
        user.telegram_id,
        "debit",
        delta=-amount,
        comment=f"Недостаточно средств за {day.isoformat()}",
        performed_by=SYSTEM_ACTOR,
        idempotency_key=key,
        last_charge_date=day.isoformat(),
    )
    return "insufficient" if applied else "duplicate"


async def process_user(repo: Repo, user: User, now=None) -> list[str]:
    today = user_today(user.timezone)
    results = []
    current = await repo.get_user(user.telegram_id)
    if current is None or current.is_deleted:
        return results
    for day in days_to_charge(current, today):
        result = await charge_user_for_day(repo, current, day)
        results.append(result)
        current = await repo.get_user(user.telegram_id)
        if current is None:
            break
    return results


async def run_billing_tick(repo: Repo) -> dict[str, int]:
    stats = {"charged": 0, "insufficient": 0, "free": 0, "duplicate": 0, "users": 0}
    users = await repo.list_active_billable()
    stats["users"] = len(users)
    for user in users:
        try:
            results = await process_user(repo, user, now_utc())
            for item in results:
                stats[item] = stats.get(item, 0) + 1
        except Exception:
            logger.exception("Billing failed for user %s", user.telegram_id)
    return stats

"""User access rules: activity, deletion, write permission."""

from __future__ import annotations

from datetime import date

from database.models import User
from database.queries import Repo
from utils.formatting import BALANCE_ENDED, BANNED_ACCOUNT, DELETED_ACCOUNT
from utils.time import user_today


def can_write(user: User, today: date | None = None) -> bool:
    if not user.is_active or user.is_banned:
        return False
    if user.daily_price <= 0:
        return True
    today_iso = (today or user_today(user.timezone)).isoformat()
    if user.paid_until_date and user.paid_until_date >= today_iso:
        return True
    return user.balance >= user.daily_price


def access_message(user: User) -> str | None:
    if user.is_deleted:
        return DELETED_ACCOUNT
    if user.is_banned:
        return BANNED_ACCOUNT
    return None


def write_block_message(user: User) -> str | None:
    blocked = access_message(user)
    if blocked:
        return blocked
    if not can_write(user):
        return BALANCE_ENDED
    return None


async def ensure_user(repo: Repo, telegram_id: int) -> User:
    user = await repo.get_user(telegram_id)
    if user is None:
        raise ValueError("user not found")
    return user

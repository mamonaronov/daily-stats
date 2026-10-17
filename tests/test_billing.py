from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from services.billing import charge_key, days_to_charge, process_user
from utils.time import to_iso


async def _user(repo, telegram_id=100, price=10.0, balance=0.0, tz="Europe/Moscow"):
    user = await repo.create_user(telegram_id, "u", "Name", None, tz, price, "23:00")
    if balance:
        await repo.apply_balance_change(
            telegram_id, "credit", delta=balance, comment="test", performed_by=1
        )
        user = await repo.get_user(telegram_id)
    return user


@pytest.mark.asyncio
async def test_idempotent_daily_charge(repo):
    user = await _user(repo, balance=100)
    today = date.fromisoformat("2026-08-17")
    user.last_charge_date = None
    # Force last_charge so only one day is billed in isolation via charge_user
    from services.billing import charge_user_for_day

    first = await charge_user_for_day(repo, await repo.get_user(100), today)
    second = await charge_user_for_day(repo, await repo.get_user(100), today)
    assert first == "charged"
    assert second == "duplicate"
    user = await repo.get_user(100)
    assert user.balance == pytest.approx(90)
    assert user.paid_until_date == "2026-08-17"
    ops = await repo.list_operations(100)
    assert len(ops) == 2  # credit + one debit


@pytest.mark.asyncio
async def test_insufficient_funds_does_not_extend_paid_until(repo):
    user = await _user(repo, balance=3, price=10)
    from services.billing import charge_user_for_day

    day = date(2026, 8, 17)
    result = await charge_user_for_day(repo, await repo.get_user(100), day)
    assert result == "insufficient"
    user = await repo.get_user(100)
    assert user.balance == pytest.approx(0)
    assert user.paid_until_date is None
    assert user.last_charge_date == "2026-08-17"


def test_charge_key_unique():
    assert charge_key(1, date(2026, 1, 2)) == "daily:1:2026-01-02"


@pytest.mark.asyncio
async def test_topup_same_day_charges_after_insufficient(repo):
    from services.billing import charge_user_for_day

    await _user(repo, balance=3, price=10)
    day = date(2026, 8, 17)
    assert await charge_user_for_day(repo, await repo.get_user(100), day) == "insufficient"
    await repo.apply_balance_change(100, "credit", delta=100, comment="topup", performed_by=1)
    assert await charge_user_for_day(repo, await repo.get_user(100), day) == "charged"
    user = await repo.get_user(100)
    assert user.balance == pytest.approx(90)
    assert user.paid_until_date == "2026-08-17"
    assert await charge_user_for_day(repo, await repo.get_user(100), day) == "duplicate"
    assert (await repo.get_user(100)).balance == pytest.approx(90)


@pytest.mark.asyncio
async def test_small_topup_does_not_take_balance_again(repo):
    from services.billing import charge_user_for_day

    await _user(repo, balance=3, price=10)
    day = date(2026, 8, 17)
    assert await charge_user_for_day(repo, await repo.get_user(100), day) == "insufficient"
    await repo.apply_balance_change(100, "credit", delta=2, comment="topup", performed_by=1)
    assert await charge_user_for_day(repo, await repo.get_user(100), day) == "insufficient"
    user = await repo.get_user(100)
    assert user.balance == pytest.approx(2)
    assert user.paid_until_date is None


def test_days_to_charge_catch_up():
    from database.models import User

    user = User(
        telegram_id=1,
        username=None,
        first_name="A",
        last_name=None,
        registered_at="2026-08-10T00:00:00+00:00",
        timezone="UTC",
        status="active",
        last_activity_at=None,
        balance=100,
        daily_price=10,
        paid_until_date=None,
        last_charge_date="2026-08-14",
        deleted_at=None,
        bot_blocked_at=None,
        created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:00:00+00:00",
    )
    days = days_to_charge(user, date(2026, 8, 17))
    assert days[0] == date(2026, 8, 15)
    assert days[-1] == date(2026, 8, 17)
    assert len(days) == 3


def test_days_to_charge_retries_unpaid_today():
    from database.models import User

    user = User(
        telegram_id=1,
        username=None,
        first_name="A",
        last_name=None,
        registered_at="2026-08-10T00:00:00+00:00",
        timezone="UTC",
        status="active",
        last_activity_at=None,
        balance=100,
        daily_price=10,
        paid_until_date=None,
        last_charge_date="2026-08-17",
        deleted_at=None,
        bot_blocked_at=None,
        created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:00:00+00:00",
    )
    assert days_to_charge(user, date(2026, 8, 17)) == [date(2026, 8, 17)]

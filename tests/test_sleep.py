from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.entries import (
    add_sleep_onset,
    add_sleep_phone_away,
    add_sleep_phone_in,
    add_sleep_up,
    add_sleep_wake,
    add_sleep_wake_and_up,
)


@pytest.mark.asyncio
async def test_sleep_phone_then_away_then_wake_up_and_onset(repo):
    user = await repo.create_user(31, "s", "S", None, "UTC", 0, "23:00")
    phone = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    away = datetime(2026, 8, 16, 20, 40, tzinfo=timezone.utc)
    wake = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)
    onset = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)

    item_id, error = await add_sleep_phone_in(repo, user, phone)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    assert rec.phase() == "with_phone"

    _, error = await add_sleep_phone_away(repo, user, away)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "no_phone"
    assert rec.phone_away_at is not None

    _, error = await add_sleep_wake_and_up(repo, user, wake, quality=5)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "need_onset"
    assert rec.wake_time is not None
    assert rec.out_of_bed_at == rec.wake_time
    assert rec.quality == 5
    assert rec.duration_minutes is None

    _, error = await add_sleep_onset(repo, user, onset)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "idle"
    assert rec.duration_minutes == 7 * 60


@pytest.mark.asyncio
async def test_new_night_leaves_previous_open(repo):
    user = await repo.create_user(32, "s", "S", None, "UTC", 0, "23:00")
    first = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    wake = datetime(2026, 8, 17, 5, 0, tzinfo=timezone.utc)
    second = datetime(2026, 8, 17, 22, 0, tzinfo=timezone.utc)

    first_id, error = await add_sleep_phone_away(repo, user, first)
    assert error is None
    _, error = await add_sleep_wake(repo, user, wake, quality=3)
    assert error is None
    rec = await repo.get_sleep(first_id, user.telegram_id)
    assert rec.phase() == "awake"

    second_id, error = await add_sleep_phone_in(repo, user, second)
    assert error is None
    assert second_id != first_id
    old = await repo.get_sleep(first_id, user.telegram_id)
    new = await repo.get_sleep(second_id, user.telegram_id)
    assert old.out_of_bed_at is None
    assert new.phase() == "with_phone"


@pytest.mark.asyncio
async def test_migrated_bedtime_is_no_phone_phase(repo):
    user = await repo.create_user(33, "s", "S", None, "UTC", 0, "23:00")
    item_id = await repo.add_sleep(user.telegram_id, bedtime="2026-08-16T20:00:00+00:00")
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    await repo.update_sleep(
        item_id,
        user.telegram_id,
        phone_away_at=rec.bedtime,
    )
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "no_phone"

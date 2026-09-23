"""Walk and run are start/end intervals; other activities stay durations."""

from __future__ import annotations

from datetime import timedelta

import pytest

from services.activities import is_open_activity
from services.entries import end_activity, start_activity
from utils.time import now_utc, parse_iso, to_iso


@pytest.mark.asyncio
async def test_walk_start_and_end_compute_duration(repo):
    await repo.create_user(21, "walk", "Ира", None, "UTC", 10, "23:00")
    await repo.apply_balance_change(21, "credit", delta=100, comment="pay", performed_by=1)
    user = await repo.get_user(21)
    start = now_utc() - timedelta(minutes=40)
    item_id, error = await start_activity(repo, user, "walk", start)
    assert error is None
    open_rec = await repo.get_open_activity(user.telegram_id, "walk")
    assert open_rec is not None
    assert open_rec.id == item_id
    assert is_open_activity(open_rec)

    finished, error, kind = await end_activity(repo, user, "walk", start + timedelta(minutes=40))
    assert error is None
    assert kind == "ace"
    assert finished == item_id
    saved = await repo.get_activity(item_id, user.telegram_id)
    assert saved is not None
    assert saved.duration_minutes == 40
    assert saved.ended_at is not None
    assert not is_open_activity(saved)
    assert await repo.get_open_activity(user.telegram_id, "walk") is None


@pytest.mark.asyncio
async def test_run_without_start_stores_both_ends(repo):
    await repo.create_user(22, "run", "Олег", None, "UTC", 10, "23:00")
    await repo.apply_balance_change(22, "credit", delta=100, comment="pay", performed_by=1)
    user = await repo.get_user(22)
    end = now_utc()
    start = end - timedelta(minutes=25)
    item_id, error, kind = await end_activity(repo, user, "run", end, start_at=start)
    assert error is None
    assert kind == "act"
    saved = await repo.get_activity(item_id, user.telegram_id)
    assert saved is not None
    assert saved.duration_minutes == 25
    assert parse_iso(saved.occurred_at) == parse_iso(to_iso(start))
    assert await repo.list_open_activities(user.telegram_id) == []


@pytest.mark.asyncio
async def test_old_duration_walk_is_not_an_open_session(repo):
    user = await repo.create_user(23, "old", "Нина", None, "UTC", 10, "23:00")
    item_id = await repo.add_activity(user.telegram_id, "walk", 30, None, to_iso(now_utc()))
    saved = await repo.get_activity(item_id, user.telegram_id)
    assert saved is not None
    assert saved.ended_at is None
    assert not is_open_activity(saved)
    assert await repo.get_open_activity(user.telegram_id, "walk") is None

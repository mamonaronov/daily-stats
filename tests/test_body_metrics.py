from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from services.charts import build_charts
from services.entries import add_weight, upsert_steps
from services.history import build_timeline, format_timeline
from services.statistics import render_stats
from services.today import day_snapshot
from utils.time import combine_local, to_iso, user_today


@pytest.mark.asyncio
async def test_steps_upsert_one_per_day_and_timeline(repo):
    user = await repo.create_user(80, "steps", "Саша", None, "UTC", 0, "23:00")
    today = user_today(user.timezone)
    item_id, error, updated = await upsert_steps(repo, user, today, 5000)
    assert error is None and item_id is not None and updated is False
    same_id, error, updated = await upsert_steps(repo, user, today, 8432)
    assert error is None and updated is True
    assert same_id == item_id
    rec = await repo.get_steps(item_id, user.telegram_id)
    assert rec is not None
    assert rec.steps == 8432
    assert rec.day == today.isoformat()
    rows = await repo.list_steps(
        user.telegram_id,
        "2000-01-01T00:00:00+00:00",
        "2100-01-01T00:00:00+00:00",
    )
    assert len(rows) == 1
    items = await build_timeline(repo, user, today, today)
    assert any(item.kind == "steps" and item.detail == "8 432" for item in items)
    text = format_timeline(user, today, items)
    assert "🚶 Шаги — 8 432" in text
    assert "00:00 🚶" not in text
    snap = await day_snapshot(repo, user)
    assert snap.steps == 8432
    assert "🚶 8 432" in snap.as_text()


@pytest.mark.asyncio
async def test_steps_for_past_day_independent_of_today(repo):
    user = await repo.create_user(81, "past", "Петя", None, "Europe/Moscow", 0, "23:00")
    today = user_today(user.timezone)
    yesterday = today - timedelta(days=1)
    _, error, _ = await upsert_steps(repo, user, yesterday, 3000)
    assert error is None
    _, error, _ = await upsert_steps(repo, user, today, 10000)
    assert error is None
    yest = await repo.get_steps_by_day(user.telegram_id, yesterday.isoformat())
    now = await repo.get_steps_by_day(user.telegram_id, today.isoformat())
    assert yest is not None and yest.steps == 3000
    assert now is not None and now.steps == 10000
    stats = await render_stats(repo, user, yesterday, today, ["steps"])
    assert "13 000" in stats or "13000" in stats
    charts = await build_charts(repo, user, yesterday, today, ["steps"])
    assert charts and charts[0][0] == "Шаги"


@pytest.mark.asyncio
async def test_weight_anytime_and_stats(repo):
    user = await repo.create_user(82, "wgt", "Вика", None, "UTC", 0, "23:00")
    morning = datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 9, 2, 19, 30, tzinfo=timezone.utc)
    first, error = await add_weight(repo, user, 72.4, morning)
    assert error is None and first
    second, error = await add_weight(repo, user, 71.9, evening)
    assert error is None and second
    assert first != second
    today = date(2026, 9, 2)
    items = await build_timeline(repo, user, today, today)
    weights = [item for item in items if item.kind == "weight"]
    assert len(weights) == 2
    stats = await render_stats(repo, user, today, today, ["weight"])
    assert "71,9 кг" in stats
    assert "Замеров: 2" in stats
    charts = await build_charts(repo, user, today, today, ["weight"])
    assert charts and charts[0][0] == "Вес"


@pytest.mark.asyncio
async def test_today_snapshot_shows_latest_weight(repo):
    user = await repo.create_user(85, "snap", "Соня", None, "UTC", 0, "23:00")
    now_day = user_today("UTC")
    when = combine_local("UTC", now_day, 12, 0)
    await add_weight(repo, user, 70.5, when)
    snap = await day_snapshot(repo, user)
    assert snap.weight_kg == 70.5
    assert "⚖️ 70,5 кг" in snap.as_text()


@pytest.mark.asyncio
async def test_steps_and_weight_isolated_between_users(repo):
    a = await repo.create_user(83, "a", "А", None, "UTC", 0, "23:00")
    b = await repo.create_user(84, "b", "Б", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    await upsert_steps(repo, a, today, 1000)
    await upsert_steps(repo, b, today, 2000)
    stamp = to_iso(datetime.now(timezone.utc))
    await repo.add_weight(a.telegram_id, 80, stamp)
    assert (await repo.get_steps_by_day(a.telegram_id, today.isoformat())).steps == 1000
    assert (await repo.get_steps_by_day(b.telegram_id, today.isoformat())).steps == 2000
    assert await repo.list_weight(b.telegram_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00") == []

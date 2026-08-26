from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from services.entries import end_metric_period, start_metric_period, undo_entry
from services.history import build_timeline
from services.statistics import render_stats
from utils.time import to_iso


def _dt(year, month, day, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_period_start_then_end(repo):
    user = await repo.create_user(51, "u", "U", None, "UTC", 0, "23:00")
    metric_id = await repo.add_metric(user.telegram_id, "Ванная", "period", None, None)
    start_id, error = await start_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 18, 0))
    assert error is None
    open_rec = await repo.get_open_metric_value(user.telegram_id, metric_id)
    assert open_rec is not None
    assert open_rec.id == start_id
    assert open_rec.value_bool == 1
    end_id, error = await end_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 18, 25))
    assert error is None
    assert end_id == start_id
    assert await repo.get_open_metric_value(user.telegram_id, metric_id) is None
    rec = await repo.get_metric_value(start_id, user.telegram_id)
    assert rec is not None
    assert rec.value_bool == 0
    assert rec.value_number == 25
    assert rec.value_text == to_iso(_dt(2026, 8, 26, 18, 25))


@pytest.mark.asyncio
async def test_period_end_without_start_uses_entered_time(repo):
    user = await repo.create_user(52, "u", "U", None, "UTC", 0, "23:00")
    metric_id = await repo.add_metric(user.telegram_id, "Ванная", "period", None, None)
    item_id, error = await end_metric_period(
        repo,
        user,
        metric_id,
        _dt(2026, 8, 26, 19, 10),
        start_at=_dt(2026, 8, 26, 18, 40),
    )
    assert error is None
    rec = await repo.get_metric_value(item_id, user.telegram_id)
    assert rec is not None
    assert rec.occurred_at == to_iso(_dt(2026, 8, 26, 18, 40))
    assert rec.value_number == 30
    assert rec.value_bool == 0
    assert await repo.get_open_metric_value(user.telegram_id, metric_id) is None


@pytest.mark.asyncio
async def test_cannot_start_twice_or_end_before_start(repo):
    user = await repo.create_user(53, "u", "U", None, "UTC", 0, "23:00")
    metric_id = await repo.add_metric(user.telegram_id, "Ванная", "period", None, None)
    _, error = await start_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 10))
    assert error is None
    _, error = await start_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 11))
    assert error == "Уже идёт — сначала закончите."
    _, error = await end_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 9))
    assert error == "Конец должен быть позже начала."
    still = await repo.get_open_metric_value(user.telegram_id, metric_id)
    assert still is not None


@pytest.mark.asyncio
async def test_period_end_without_open_needs_start(repo):
    user = await repo.create_user(54, "u", "U", None, "UTC", 0, "23:00")
    metric_id = await repo.add_metric(user.telegram_id, "Ванная", "period", None, None)
    item_id, error = await end_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 12))
    assert item_id is None
    assert error == "Сначала отметьте начало."


@pytest.mark.asyncio
async def test_undo_close_reopens_period(repo):
    user = await repo.create_user(55, "u", "U", None, "UTC", 0, "23:00")
    metric_id = await repo.add_metric(user.telegram_id, "Ванная", "period", None, None)
    item_id, error = await start_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 8))
    assert error is None
    _, error = await end_metric_period(repo, user, metric_id, _dt(2026, 8, 26, 9))
    assert error is None
    assert await undo_entry(repo, user, "cme", item_id) is None
    opened = await repo.get_open_metric_value(user.telegram_id, metric_id)
    assert opened is not None
    assert opened.id == item_id
    assert opened.value_bool == 1
    assert opened.value_number is None


@pytest.mark.asyncio
async def test_period_isolation_and_stats(repo):
    a = await repo.create_user(56, "a", "A", None, "UTC", 0, "23:00")
    b = await repo.create_user(57, "b", "B", None, "UTC", 0, "23:00")
    metric_a = await repo.add_metric(a.telegram_id, "Ванная", "period", None, None)
    metric_b = await repo.add_metric(b.telegram_id, "Ванная", "period", None, None)
    item_id, error = await end_metric_period(
        repo, a, metric_a, _dt(2026, 8, 26, 12, 40), start_at=_dt(2026, 8, 26, 12, 10)
    )
    assert error is None
    stolen = await repo.get_metric_value(item_id, b.telegram_id)
    assert stolen is None
    await start_metric_period(repo, b, metric_b, _dt(2026, 8, 26, 12, 0))
    assert await repo.get_open_metric_value(b.telegram_id, metric_b) is not None
    assert await repo.get_open_metric_value(a.telegram_id, metric_a) is None
    text = await render_stats(repo, a, date(2026, 8, 26), date(2026, 8, 26), [f"m{metric_a}"])
    assert "Ванная" in text
    assert "30 мин" in text
    items = await build_timeline(repo, a, date(2026, 8, 26), date(2026, 8, 26))
    custom = [item for item in items if item.kind == "custom"]
    assert len(custom) == 1
    assert "12:10" in custom[0].detail
    assert "12:40" in custom[0].detail
    assert "30 мин" in custom[0].detail

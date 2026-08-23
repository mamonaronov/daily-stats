from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from services.markers import add_marker, link_markers, unlink_period
from services.statistics import load_period, marker_stats, render_stats
from utils.time import to_iso


def _dt(year, month, day, hour=12) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_marker_isolation(repo):
    a = await repo.create_user(31, "a", "A", None, "UTC", 0, "23:00")
    b = await repo.create_user(32, "b", "B", None, "UTC", 0, "23:00")
    item_id, error = await add_marker(repo, a, "Экзамен", _dt(2026, 5, 12), "важно")
    assert error is None
    stolen = await repo.get_marker(item_id, b.telegram_id)
    assert stolen is None
    items_b = await repo.list_markers(b.telegram_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    assert items_b == []
    rec = await repo.get_marker(item_id, a.telegram_id)
    assert rec is not None
    assert rec.name == "Экзамен"
    assert rec.comment == "важно"


@pytest.mark.asyncio
async def test_period_start_end_and_unlink_keeps_markers(repo):
    user = await repo.create_user(33, "u", "U", None, "UTC", 0, "23:00")
    start_id, error = await add_marker(repo, user, "Сессия", _dt(2026, 6, 1), as_period_start=True)
    assert error is None
    opens = await repo.list_open_periods(user.telegram_id)
    assert len(opens) == 1
    assert opens[0].is_open
    end_id, error = await add_marker(
        repo,
        user,
        "Сессия",
        _dt(2026, 6, 20),
        close_period_id=opens[0].id,
    )
    assert error is None
    period = await repo.get_period(opens[0].id, user.telegram_id)
    assert period is not None
    assert period.end_marker_id == end_id
    assert not period.is_open
    assert await unlink_period(repo, user, period.id) is None
    assert await repo.get_period(period.id, user.telegram_id) is None
    assert await repo.get_marker(start_id, user.telegram_id) is not None
    assert await repo.get_marker(end_id, user.telegram_id) is not None
    assert await repo.get_period_for_marker(start_id, user.telegram_id) is None


@pytest.mark.asyncio
async def test_link_two_markers_orders_by_time(repo):
    user = await repo.create_user(34, "u", "U", None, "UTC", 0, "23:00")
    later, error = await add_marker(repo, user, "Конец", _dt(2026, 7, 10))
    assert error is None
    earlier, error = await add_marker(repo, user, "Начало", _dt(2026, 7, 1))
    assert error is None
    period_id, error = await link_markers(repo, user, later, earlier)
    assert error is None
    period = await repo.get_period(period_id, user.telegram_id)
    assert period.start_marker_id == earlier
    assert period.end_marker_id == later
    _, again = await link_markers(repo, user, later, earlier)
    assert again == "Первая метка уже в периоде."


@pytest.mark.asyncio
async def test_period_end_must_be_after_start(repo):
    user = await repo.create_user(35, "u", "U", None, "UTC", 0, "23:00")
    start_id, error = await add_marker(repo, user, "Старт", _dt(2026, 8, 10), as_period_start=True)
    assert error is None
    period = (await repo.list_open_periods(user.telegram_id))[0]
    end_id, error = await add_marker(
        repo,
        user,
        "Раньше",
        _dt(2026, 8, 1),
        close_period_id=period.id,
    )
    assert end_id is None
    assert error == "Конец должен быть позже начала."
    assert await repo.get_marker(start_id, user.telegram_id) is not None
    still_open = await repo.list_open_periods(user.telegram_id)
    assert len(still_open) == 1


@pytest.mark.asyncio
async def test_delete_end_reopens_period(repo):
    user = await repo.create_user(36, "u", "U", None, "UTC", 0, "23:00")
    start_id, error = await add_marker(repo, user, "Старт", _dt(2026, 9, 1), as_period_start=True)
    assert error is None
    period = (await repo.list_open_periods(user.telegram_id))[0]
    end_id, error = await add_marker(repo, user, "Финиш", _dt(2026, 9, 5), close_period_id=period.id)
    assert error is None
    assert await repo.delete_marker(end_id, user.telegram_id)
    reopened = await repo.get_period(period.id, user.telegram_id)
    assert reopened is not None
    assert reopened.is_open
    assert await repo.get_marker(start_id, user.telegram_id) is not None


@pytest.mark.asyncio
async def test_markers_in_stats_and_charts(repo):
    from services.charts import build_charts
    from services.history import build_timeline

    user = await repo.create_user(37, "u", "U", None, "UTC", 0, "23:00")
    await repo.add_cigarette(user.telegram_id, to_iso(_dt(2026, 5, 12, 8)))
    start_id, error = await add_marker(repo, user, "Экзамен", _dt(2026, 5, 12, 9), "билет 3", as_period_start=True)
    assert error is None
    period = (await repo.list_open_periods(user.telegram_id))[0]
    _, error = await add_marker(repo, user, "Экзамен", _dt(2026, 5, 14, 18), close_period_id=period.id)
    assert error is None
    data = await load_period(repo, user, date(2026, 5, 11), date(2026, 5, 15))
    assert len(data["markers"]) == 2
    assert len(data["periods"]) == 1
    text = marker_stats(user, data["markers"], data["periods"])
    assert "Экзамен" in text
    stats = await render_stats(repo, user, date(2026, 5, 11), date(2026, 5, 15), ["cigarettes"])
    assert "🔖" in stats
    items = await build_timeline(repo, user, date(2026, 5, 12), date(2026, 5, 12))
    kinds = {item.kind for item in items}
    assert "marker" in kinds
    assert "cigarette" in kinds
    charts = await build_charts(repo, user, date(2026, 5, 11), date(2026, 5, 15), ["cigarettes"])
    assert charts
    assert all(png.startswith(b"\x89PNG") for _, png in charts)
    assert start_id

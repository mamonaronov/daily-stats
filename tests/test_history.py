from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from services.history import build_timeline, format_timeline, history_button_label
from handlers.history import pack_hist_cursor, unpack_hist_cursor
from utils.time import to_iso


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_sleep_events_stay_on_their_own_days(repo):
    user = await repo.create_user(201, "hist", "Ника", None, "UTC", 0, "23:00")
    onset_day = date(2026, 8, 8)
    wake_day = date(2026, 8, 9)
    await repo.add_sleep(
        user.telegram_id,
        sleep_onset_at=to_iso(_at(onset_day, 23)),
        wake_time=to_iso(_at(wake_day, 7)),
        out_of_bed_at=to_iso(_at(wake_day, 7, 20)),
        duration_minutes=8 * 60,
        quality=4,
        wake_kind="self",
    )
    onset_items = await build_timeline(repo, user, onset_day, onset_day)
    assert [item.kind for item in onset_items] == ["sleep_onset"]
    wake_items = await build_timeline(repo, user, wake_day, wake_day)
    assert [item.kind for item in wake_items] == ["sleep_wake", "sleep_up"]
    text = format_timeline(user, wake_day, wake_items)
    assert "23:00 💤" not in text
    assert "☀️ Проснулся" in text
    assert "сам" in wake_items[0].detail


@pytest.mark.asyncio
async def test_long_sleep_visible_on_middle_days_and_period(repo):
    user = await repo.create_user(202, "span", "Соня", None, "UTC", 0, "23:00")
    start = date(2026, 8, 8)
    middle = date(2026, 8, 12)
    end = date(2026, 8, 15)
    await repo.add_sleep(
        user.telegram_id,
        sleep_onset_at=to_iso(_at(start, 23)),
        wake_time=to_iso(_at(end, 10)),
        out_of_bed_at=to_iso(_at(end, 10, 5)),
        duration_minutes=int((_at(end, 10) - _at(start, 23)).total_seconds() // 60),
        quality=3,
    )
    middle_items = await build_timeline(repo, user, middle, middle)
    assert len(middle_items) == 1
    night = middle_items[0]
    assert night.kind == "sleep_night"
    assert "8–15 августа" in night.title
    assert "ещё идёт" not in night.detail
    assert history_button_label(user, night).startswith("😴 Сон")

    empty_before = await build_timeline(repo, user, start - timedelta(days=1), start - timedelta(days=1))
    assert empty_before == []
    empty_after = await build_timeline(repo, user, end + timedelta(days=1), end + timedelta(days=1))
    assert empty_after == []

    period = await build_timeline(repo, user, middle, middle + timedelta(days=1))
    assert any(item.kind == "sleep_night" for item in period)
    period_text = format_timeline(user, middle, period, end=middle + timedelta(days=1))
    assert "📅 12 августа 2026 — 13 августа 2026" in period_text
    assert "😴 Сон 8–15 августа" in period_text

    week = await build_timeline(repo, user, start, end)
    kinds = [item.kind for item in week]
    assert kinds[0] == "sleep_night"
    assert "sleep_onset" in kinds
    assert "sleep_wake" in kinds
    assert "sleep_up" in kinds
    week_text = format_timeline(user, start, week, end=end)
    assert "8 августа" in week_text
    assert "15 августа" in week_text
    assert "23:00 💤 Заснул" in week_text
    assert "10:00 ☀️ Проснулся" in week_text


@pytest.mark.asyncio
async def test_open_sleep_shows_on_later_days(repo):
    user = await repo.create_user(203, "open", "Олег", None, "UTC", 0, "23:00")
    start = date(2026, 8, 1)
    later = date(2026, 8, 8)
    await repo.add_sleep(user.telegram_id, sleep_onset_at=to_iso(_at(start, 22)))
    items = await build_timeline(repo, user, later, later)
    assert len(items) == 1
    assert items[0].kind == "sleep_night"
    assert items[0].detail == "ещё идёт"
    assert "с 1 августа" in items[0].title


@pytest.mark.asyncio
async def test_list_sleep_overlapping_does_not_change_point_query(repo):
    user = await repo.create_user(204, "q", "Кира", None, "UTC", 0, "23:00")
    start = date(2026, 8, 8)
    end = date(2026, 8, 15)
    middle = date(2026, 8, 12)
    await repo.add_sleep(
        user.telegram_id,
        sleep_onset_at=to_iso(_at(start, 23)),
        wake_time=to_iso(_at(end, 10)),
        duration_minutes=7 * 24 * 60,
    )
    a, b = to_iso(_at(middle, 0)), to_iso(_at(middle + timedelta(days=1), 0))
    assert await repo.list_sleep(user.telegram_id, a, b) == []
    found = await repo.list_sleep_overlapping(user.telegram_id, a, b)
    assert len(found) == 1


def test_hist_cursor_roundtrip():
    packed = pack_hist_cursor(date(2026, 8, 8), date(2026, 8, 15), 2)
    assert packed == "2026-08-08:2026-08-15:2"
    assert unpack_hist_cursor(packed.split(":")) == (date(2026, 8, 8), date(2026, 8, 15), 2)
    assert unpack_hist_cursor([]) is None
    assert unpack_hist_cursor(["nope"]) is None

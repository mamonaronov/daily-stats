from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from database.models import SleepRecord
from services.sleep_strips import (
    PHASE_ASLEEP,
    PHASE_AWAKE_BED,
    PHASE_NOPHONE,
    PHASE_PHONE,
    build_sleep_strip,
    choose_day_hour,
    night_segments,
    sleep_span,
    strip_title,
)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _span(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    return start, end


def _night(
    *,
    phone_in: datetime | None = None,
    phone_away: datetime | None = None,
    onset: datetime | None = None,
    wake: datetime | None = None,
    up: datetime | None = None,
) -> SleepRecord:
    start = phone_in or phone_away or onset or wake or up
    assert start is not None
    minutes = None
    if onset and wake:
        minutes = int((wake - onset).total_seconds() // 60)
    return SleepRecord(
        id=1,
        telegram_id=1,
        bedtime=(phone_in or phone_away or onset).isoformat() if (phone_in or phone_away or onset) else None,
        wake_time=wake.isoformat() if wake else None,
        duration_minutes=minutes,
        quality=4,
        created_at=start.isoformat(),
        updated_at=start.isoformat(),
        phone_in_bed_at=phone_in.isoformat() if phone_in else None,
        phone_away_at=phone_away.isoformat() if phone_away else None,
        sleep_onset_at=onset.isoformat() if onset else None,
        out_of_bed_at=up.isoformat() if up else None,
    )


def _record(onset: datetime, wake: datetime | None) -> SleepRecord:
    return _night(onset=onset, wake=wake)


def test_night_sleep_keeps_noon():
    day = date(2026, 8, 16)
    spans = [_span(_at(day, 23), _at(day + timedelta(days=1), 7))]
    assert choose_day_hour(spans) == 12


def test_daytime_sleep_moves_cut_to_wake_gap():
    day = date(2026, 8, 16)
    spans = [_span(_at(day, 6), _at(day, 14))]
    hour = choose_day_hour(spans)
    assert hour == 22
    assert not (6 < hour < 14)


def test_sleep_longer_than_a_day_falls_back_to_noon():
    day = date(2026, 8, 16)
    spans = [_span(_at(day, 8), _at(day + timedelta(days=1), 10))]
    assert choose_day_hour(spans) == 12


def test_overlapping_clock_hours_split_the_shorter_sleep():
    day = date(2026, 8, 16)
    spans = [
        _span(_at(day, 23, 30), _at(day + timedelta(days=1), 12, 30)),
        _span(_at(day + timedelta(days=1), 11, 30), _at(day + timedelta(days=1), 23, 30)),
    ]
    hour = choose_day_hour(spans)
    assert hour != 12
    assert 13 <= hour <= 23


def test_daytime_sleep_stays_on_one_strip():
    day = date(2026, 8, 16)
    now = _at(day, 20)
    strip = build_sleep_strip([_record(_at(day, 6), _at(day, 14))], "UTC", day, day, now)
    assert strip is not None
    assert strip.day_hour == 22
    assert strip.splits_sleep is False
    filled = [row.segments for row in strip.rows if row.segments]
    assert len(filled) == 1
    assert len(filled[0]) == 1
    assert filled[0][0].phase == PHASE_ASLEEP
    offset, width = filled[0][0].offset, filled[0][0].width
    assert abs(offset - 8) < 1e-6
    assert abs(width - 8) < 1e-6


def test_night_sleep_is_unsplit_on_noon_strips():
    day = date(2026, 8, 16)
    now = _at(day, 20)
    onset = _at(day - timedelta(days=1), 23)
    wake = _at(day, 7)
    strip = build_sleep_strip([_record(onset, wake)], "UTC", day, day, now)
    assert strip is not None
    assert strip.day_hour == 12
    filled = [row.segments for row in strip.rows if row.segments]
    assert len(filled) == 1
    assert len(filled[0]) == 1
    offset, width = filled[0][0].offset, filled[0][0].width
    assert abs(offset - 11) < 1e-6
    assert abs(width - 8) < 1e-6


def test_open_sleep_is_clipped_to_now():
    now = _at(date(2026, 8, 16), 2)
    onset = _at(date(2026, 8, 15), 23)
    rec = _record(onset, None)
    span = sleep_span(rec, "UTC", now)
    assert span is not None
    assert span[1] == now


def test_stale_open_sleep_is_ignored():
    onset = _at(date(2026, 8, 10), 23)
    now = _at(date(2026, 8, 16), 2)
    assert sleep_span(_record(onset, None), "UTC", now) is None


def test_strip_title_mentions_cut_hour():
    day = date(2026, 8, 16)
    strip = build_sleep_strip(
        [_record(_at(day, 6), _at(day, 14))],
        "UTC",
        day,
        day,
        _at(day, 20),
    )
    assert strip is not None
    assert "22:00" in strip_title(strip)
    assert "границе" not in strip_title(strip)
    assert "пустых" not in strip_title(strip)


def test_nap_and_night_share_a_strip_without_splitting():
    day = date(2026, 8, 16)
    night = _record(_at(day - timedelta(days=1), 23), _at(day, 7))
    nap = _record(_at(day, 10), _at(day, 11, 30))
    strip = build_sleep_strip([night, nap], "UTC", day, day, _at(day, 20))
    assert strip is not None
    assert strip.day_hour == 12
    assert strip.splits_sleep is False
    filled = [row for row in strip.rows if row.segments]
    assert len(filled) == 1
    assert len(filled[0].segments) == 2
    assert all(seg.phase == PHASE_ASLEEP for seg in filled[0].segments)


def test_trailing_empty_strip_is_dropped_when_enabled():
    day = date(2026, 8, 16)
    rec = [_record(_at(day - timedelta(days=1), 23), _at(day, 7))]
    now = _at(day, 20)
    kept = build_sleep_strip(rec, "UTC", day, day, now)
    hidden = build_sleep_strip(rec, "UTC", day, day, now, trim_empty_edges=True)
    assert kept is not None and hidden is not None
    assert len(hidden.rows) == 1
    assert hidden.rows[0].segments
    assert len(kept.rows) > len(hidden.rows)
    assert not kept.rows[-1].segments


def test_gap_day_without_sleep_is_kept():
    first = _record(_at(date(2026, 8, 13), 23), _at(date(2026, 8, 14), 7))
    last = _record(_at(date(2026, 8, 15), 23), _at(date(2026, 8, 16), 7))
    strip = build_sleep_strip(
        [first, last],
        "UTC",
        date(2026, 8, 14),
        date(2026, 8, 16),
        _at(date(2026, 8, 16), 20),
    )
    assert strip is not None
    filled = [i for i, row in enumerate(strip.rows) if row.segments]
    assert len(filled) >= 2
    assert any(not strip.rows[i].segments for i in range(filled[0] + 1, filled[-1]))


@pytest.mark.asyncio
async def test_sleep_charts_include_day_strips(repo):
    from services.charts import build_charts
    from utils.time import to_iso

    user = await repo.create_user(501, "sleep-strip", "Сон", None, "UTC", 0, "23:00")
    day = date(2026, 8, 16)
    onset = _at(day, 6)
    wake = _at(day, 14)
    await repo.add_sleep(
        user.telegram_id,
        bedtime=to_iso(onset),
        wake_time=to_iso(wake),
        duration_minutes=8 * 60,
        quality=4,
        sleep_onset_at=to_iso(onset),
    )
    charts = await build_charts(repo, user, day, day, ["sleep"])
    strip_charts = [(title, png) for title, png in charts if title.startswith("Сон по суткам")]
    assert len(strip_charts) == 1
    title, png = strip_charts[0]
    assert "22:00" in title
    assert "пустых" not in title
    assert png.startswith(b"\x89PNG")

    from services.ui_prefs import prefs_of, save_prefs

    prefs = prefs_of(user)
    prefs.hide_sleep_empty_edges = True
    user = await save_prefs(repo, user, prefs)
    hidden_charts = await build_charts(repo, user, day, day, ["sleep"])
    hidden_strips = [title for title, _ in hidden_charts if title.startswith("Сон по суткам")]
    assert len(hidden_strips) == 1


def test_night_phases_are_separate_segments():
    day = date(2026, 8, 16)
    rec = _night(
        phone_in=_at(day - timedelta(days=1), 22),
        phone_away=_at(day - timedelta(days=1), 22, 40),
        onset=_at(day - timedelta(days=1), 23),
        wake=_at(day, 7),
        up=_at(day, 7, 20),
    )
    now = _at(day, 20)
    phases = [phase for _start, _end, phase in night_segments(rec, "UTC", now)]
    assert phases == [PHASE_PHONE, PHASE_NOPHONE, PHASE_ASLEEP, PHASE_AWAKE_BED]
    strip = build_sleep_strip([rec], "UTC", day, day, now)
    assert strip is not None
    drawn = [seg.phase for row in strip.rows for seg in row.segments]
    assert drawn == [PHASE_PHONE, PHASE_NOPHONE, PHASE_ASLEEP, PHASE_AWAKE_BED]

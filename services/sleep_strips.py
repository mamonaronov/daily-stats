"""Horizontal day-strips for sleep: one 24h bar per day, cut at a whole hour."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from database.models import SleepRecord
from utils.time import (
    MONTHS_RU,
    circular_mean_minutes,
    format_date,
    parse_iso,
    to_user,
    zone,
)

DEFAULT_DAY_HOUR = 12
_MINUTES_IN_DAY = 24 * 60
_OPEN_NIGHT_LIMIT = timedelta(hours=36)

PHASE_PHONE = "phone"
PHASE_NOPHONE = "nophone"
PHASE_ASLEEP = "asleep"
PHASE_AWAKE_BED = "awake_bed"

PHASE_LABELS = {
    PHASE_PHONE: "С телефоном",
    PHASE_NOPHONE: "Без телефона",
    PHASE_ASLEEP: "Сон",
    PHASE_AWAKE_BED: "Проснулся",
}
PHASE_COLORS = {
    PHASE_PHONE: "#E39B2B",
    PHASE_NOPHONE: "#7A6FF0",
    PHASE_ASLEEP: "#3B6FE8",
    PHASE_AWAKE_BED: "#2BB673",
}

_KIND_ORDER = {
    "phone_in": 0,
    "phone_away": 1,
    "onset": 2,
    "wake": 3,
    "up": 4,
    "now": 5,
}
_PHASE_AFTER = {
    "phone_in": PHASE_PHONE,
    "phone_away": PHASE_NOPHONE,
    "onset": PHASE_ASLEEP,
    "wake": PHASE_AWAKE_BED,
}


@dataclass(frozen=True, slots=True)
class SleepStripSeg:
    offset: float
    width: float
    phase: str


@dataclass(frozen=True, slots=True)
class SleepStripRow:
    start: datetime
    end: datetime
    label: str
    segments: tuple[SleepStripSeg, ...]


@dataclass(frozen=True, slots=True)
class SleepStrip:
    day_hour: int
    rows: tuple[SleepStripRow, ...]
    mean_onset_axis: float | None
    mean_wake_axis: float | None
    splits_sleep: bool


def strip_title(strip: SleepStrip) -> str:
    clock = f"{strip.day_hour:02d}:00"
    title = f"Сон по суткам · с {clock} до {clock}"
    if strip.splits_sleep:
        title += " · сон на границе суток"
    return title


def sleep_span(
    record: SleepRecord,
    tz_name: str,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    segments = night_segments(record, tz_name, now)
    if not segments:
        return None
    return segments[0][0], segments[-1][1]


def night_segments(
    record: SleepRecord,
    tz_name: str,
    now: datetime,
) -> list[tuple[datetime, datetime, str]]:
    marks = _night_marks(record, tz_name, now)
    if len(marks) < 2:
        return []
    segments: list[tuple[datetime, datetime, str]] = []
    for (start, kind), (end, _next_kind) in zip(marks, marks[1:]):
        phase = _PHASE_AFTER.get(kind)
        if phase is None or end <= start:
            continue
        segments.append((start, end, phase))
    return segments


def choose_day_hour(spans: list[tuple[datetime, datetime]]) -> int:
    if not spans:
        return DEFAULT_DAY_HOUR
    if _split_cost(spans, DEFAULT_DAY_HOUR)[0] == 0:
        return DEFAULT_DAY_HOUR
    clock = _clock_intervals(spans)
    return min(range(24), key=lambda hour: _hour_sort_key(spans, clock, hour))


def build_sleep_strip(
    records: list[SleepRecord],
    tz_name: str,
    start: date,
    end: date,
    now: datetime,
    *,
    trim_empty_edges: bool = False,
) -> SleepStrip | None:
    built = _assemble_strip(records, tz_name, start, end, now)
    if built is None:
        return None
    rows = _trim_empty_edges(list(built.rows)) if trim_empty_edges else list(built.rows)
    if not rows:
        return None
    return SleepStrip(
        day_hour=built.day_hour,
        rows=tuple(rows),
        mean_onset_axis=built.mean_onset_axis,
        mean_wake_axis=built.mean_wake_axis,
        splits_sleep=built.splits_sleep,
    )


def _assemble_strip(
    records: list[SleepRecord],
    tz_name: str,
    start: date,
    end: date,
    now: datetime,
) -> SleepStrip | None:
    tzinfo = zone(tz_name)
    now_local = to_user(now, tz_name)
    nights = [segs for record in records if (segs := night_segments(record, tz_name, now_local))]
    if not nights:
        return None
    spans = [(segs[0][0], segs[-1][1]) for segs in nights]
    day_hour = choose_day_hour(spans)
    windows = _day_windows(start, end, day_hour, tzinfo, now_local)
    if not windows:
        return None
    flat = [seg for segs in nights for seg in segs]
    rows = tuple(_row_for_window(window, flat) for window in windows)
    if not any(row.segments for row in rows):
        return None
    onset_mins = [_minutes_of(start) for start, _end, phase in flat if phase == PHASE_ASLEEP]
    wake_mins = [_minutes_of(end) for _start, end, phase in flat if phase == PHASE_ASLEEP]
    return SleepStrip(
        day_hour=day_hour,
        rows=rows,
        mean_onset_axis=_mean_axis_hour(onset_mins, day_hour),
        mean_wake_axis=_mean_axis_hour(wake_mins, day_hour),
        splits_sleep=any(_split_cost([span], day_hour)[0] for span in spans),
    )


def _night_marks(
    record: SleepRecord,
    tz_name: str,
    now: datetime,
) -> list[tuple[datetime, str]]:
    now_local = to_user(now, tz_name)
    phone_in = _local(record.phone_in_bed_at, tz_name)
    phone_away = _local(record.phone_away_at, tz_name)
    bedtime = _local(record.bedtime, tz_name)
    if bedtime and phone_in is None and phone_away is None:
        phone_away = bedtime
    marks: list[tuple[datetime, str]] = []
    if phone_in:
        marks.append((phone_in, "phone_in"))
    if phone_away:
        marks.append((phone_away, "phone_away"))
    if record.sleep_onset_at:
        marks.append((_local(record.sleep_onset_at, tz_name), "onset"))
    if record.wake_time:
        marks.append((_local(record.wake_time, tz_name), "wake"))
    if record.out_of_bed_at:
        marks.append((_local(record.out_of_bed_at, tz_name), "up"))
    marks = [(moment, kind) for moment, kind in marks if moment is not None]
    if not marks:
        return []
    marks = _collapse_marks(marks)
    first = marks[0][0]
    last_kind = marks[-1][1]
    if last_kind in {"phone_in", "phone_away", "onset"}:
        if now_local - first > _OPEN_NIGHT_LIMIT:
            return []
        marks.append((now_local, "now"))
    return marks


def _collapse_marks(marks: list[tuple[datetime, str]]) -> list[tuple[datetime, str]]:
    by_time: dict[datetime, str] = {}
    for moment, kind in marks:
        prev = by_time.get(moment)
        if prev is None or _KIND_ORDER[kind] > _KIND_ORDER[prev]:
            by_time[moment] = kind
    return sorted(by_time.items(), key=lambda item: (item[0], _KIND_ORDER[item[1]]))


def _local(value: str | None, tz_name: str) -> datetime | None:
    if not value:
        return None
    return to_user(parse_iso(value), tz_name)


def _hour_sort_key(
    spans: list[tuple[datetime, datetime]],
    clock: list[tuple[int, int]],
    hour: int,
) -> tuple[int, int, int, int]:
    split_count, split_minutes = _split_cost(spans, hour)
    toward_noon = min((hour - DEFAULT_DAY_HOUR) % 24, (DEFAULT_DAY_HOUR - hour) % 24)
    return (split_count, split_minutes, -_clearance(clock, hour), toward_noon)


def _split_cost(spans: list[tuple[datetime, datetime]], hour: int) -> tuple[int, int]:
    count = 0
    minutes = 0
    for start, end in spans:
        if _hour_cuts_span(start, end, hour):
            count += 1
            minutes += max(0, int((end - start).total_seconds() // 60))
    return count, minutes


def _hour_cuts_span(start: datetime, end: datetime, hour: int) -> bool:
    day = start.date() - timedelta(days=1)
    last = end.date() + timedelta(days=1)
    while day <= last:
        mark = datetime(day.year, day.month, day.day, hour, 0, tzinfo=start.tzinfo)
        if start < mark < end:
            return True
        day += timedelta(days=1)
    return False


def _clock_intervals(spans: list[tuple[datetime, datetime]]) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for start, end in spans:
        duration = (end - start).total_seconds() / 60
        if duration >= _MINUTES_IN_DAY:
            return [(0, _MINUTES_IN_DAY)]
        start_m = _minutes_of(start)
        end_m = _minutes_of(end)
        if start_m == end_m:
            return [(0, _MINUTES_IN_DAY)]
        intervals.append((start_m, end_m))
    return intervals


def _clearance(clock: list[tuple[int, int]], hour: int) -> int:
    if not clock:
        return _MINUTES_IN_DAY // 2
    t = hour * 60
    return min(_distance_to_interval(t, start, end) for start, end in clock)


def _distance_to_interval(t: int, start: int, end: int) -> int:
    if start < end:
        if start <= t <= end:
            return 0
        return min((start - t) % _MINUTES_IN_DAY, (t - end) % _MINUTES_IN_DAY)
    if t >= start or t <= end:
        return 0
    return min(start - t, t - end)


def _minutes_of(local: datetime) -> int:
    return local.hour * 60 + local.minute


def _mean_axis_hour(values: list[int], day_hour: int) -> float | None:
    mean = circular_mean_minutes(values)
    if mean is None:
        return None
    return (mean / 60 - day_hour) % 24


def _day_windows(
    start: date,
    end: date,
    hour: int,
    tzinfo,
    now_local: datetime,
) -> list[tuple[datetime, datetime]]:
    period_start = datetime.combine(start, time.min, tzinfo=tzinfo)
    period_end = min(
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=tzinfo),
        now_local,
    )
    if period_end <= period_start:
        return []
    cursor = _latest_at_or_before(period_start, hour)
    limit = _earliest_at_or_after(period_end, hour)
    windows: list[tuple[datetime, datetime]] = []
    while cursor < limit:
        nxt = _at_hour(cursor.date() + timedelta(days=1), hour, cursor.tzinfo)
        if nxt <= cursor:
            nxt = cursor + timedelta(hours=24)
        if cursor >= now_local:
            break
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


def _at_hour(day: date, hour: int, tzinfo) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=tzinfo)


def _latest_at_or_before(moment: datetime, hour: int) -> datetime:
    today = _at_hour(moment.date(), hour, moment.tzinfo)
    if today <= moment:
        return today
    return _at_hour(moment.date() - timedelta(days=1), hour, moment.tzinfo)


def _earliest_at_or_after(moment: datetime, hour: int) -> datetime:
    today = _at_hour(moment.date(), hour, moment.tzinfo)
    if today >= moment:
        return today
    return _at_hour(moment.date() + timedelta(days=1), hour, moment.tzinfo)


def _trim_empty_edges(rows: list[SleepStripRow]) -> list[SleepStripRow]:
    while rows and not rows[0].segments:
        rows.pop(0)
    while rows and not rows[-1].segments:
        rows.pop()
    return rows


def _row_for_window(
    window: tuple[datetime, datetime],
    segments: list[tuple[datetime, datetime, str]],
) -> SleepStripRow:
    start, end = window
    clipped: list[SleepStripSeg] = []
    for span_start, span_end, phase in segments:
        lo = max(start, span_start)
        hi = min(end, span_end)
        if hi <= lo:
            continue
        clipped.append(
            SleepStripSeg(
                offset=(lo - start).total_seconds() / 3600,
                width=(hi - lo).total_seconds() / 3600,
                phase=phase,
            )
        )
    return SleepStripRow(start=start, end=end, label=_window_label(start, end), segments=tuple(clipped))


def _window_label(start: datetime, end: datetime) -> str:
    first = start.date()
    last = (end - timedelta(seconds=1)).date()
    if first == last:
        return format_date(first)
    if first.month == last.month and first.year == last.year:
        return f"{first.day}–{last.day} {MONTHS_RU[first.month]}"
    return f"{first.day} {MONTHS_RU[first.month]}–{last.day} {MONTHS_RU[last.month]}"

"""Timezone-aware time helpers. All stored timestamps are UTC ISO-8601."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

COMMON_TIMEZONES = [
    ("Europe/Kaliningrad", "Калининград (UTC+2)"),
    ("Europe/Moscow", "Москва (UTC+3)"),
    ("Europe/Samara", "Самара (UTC+4)"),
    ("Asia/Yekaterinburg", "Екатеринбург (UTC+5)"),
    ("Asia/Omsk", "Омск (UTC+6)"),
    ("Asia/Krasnoyarsk", "Красноярск (UTC+7)"),
    ("Asia/Irkutsk", "Иркутск (UTC+8)"),
    ("Asia/Yakutsk", "Якутск (UTC+9)"),
    ("Asia/Vladivostok", "Владивосток (UTC+10)"),
    ("Asia/Magadan", "Магадан (UTC+11)"),
    ("Asia/Kamchatka", "Камчатка (UTC+12)"),
    ("Europe/Kyiv", "Киев (UTC+2/+3)"),
    ("Asia/Almaty", "Алматы (UTC+5)"),
    ("Asia/Tashkent", "Ташкент (UTC+5)"),
    ("Europe/Minsk", "Минск (UTC+3)"),
    ("UTC", "UTC"),
]

MONTHS_RU = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except ZoneInfoNotFoundError:
        return False


def to_user(dt: datetime, tz_name: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(zone(tz_name))


def user_now(tz_name: str) -> datetime:
    return now_utc().astimezone(zone(tz_name))


def user_today(tz_name: str) -> date:
    return user_now(tz_name).date()


def combine_local(tz_name: str, day: date, hour: int, minute: int) -> datetime:
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone(tz_name))
    return local.astimezone(UTC)


def day_bounds_utc(tz_name: str, day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=zone(tz_name))
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def range_bounds_utc(tz_name: str, start_day: date, end_day: date) -> tuple[datetime, datetime]:
    start, _ = day_bounds_utc(tz_name, start_day)
    _, end = day_bounds_utc(tz_name, end_day)
    return start, end


def local_date_of(dt: datetime, tz_name: str) -> date:
    return to_user(dt, tz_name).date()


def format_dt(dt: datetime, tz_name: str) -> str:
    local = to_user(dt, tz_name)
    return f"{local.day} {MONTHS_RU[local.month]} {local.strftime('%H:%M')}"


def format_dt_full(dt: datetime, tz_name: str) -> str:
    local = to_user(dt, tz_name)
    return f"{local.day} {MONTHS_RU[local.month]} {local.year}, {local.strftime('%H:%M:%S')}"


def format_dt_compact(dt: datetime, tz_name: str) -> str:
    return to_user(dt, tz_name).strftime("%d.%m.%Y %H:%M:%S")


def format_time(dt: datetime, tz_name: str) -> str:
    return to_user(dt, tz_name).strftime("%H:%M")


def format_date(day: date) -> str:
    return f"{day.day} {MONTHS_RU[day.month]}"


def format_date_long(day: date) -> str:
    return f"{day.day} {MONTHS_RU[day.month]} {day.year}"


def parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().replace(".", ":").split(":")
    if len(parts) != 2:
        raise ValueError("time")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time")
    return hour, minute


def minutes_of_day(dt: datetime, tz_name: str) -> int:
    local = to_user(dt, tz_name)
    return local.hour * 60 + local.minute


def circular_mean_minutes(values: list[int]) -> int | None:
    """Mean of clock times, wrapping around midnight."""
    if not values:
        return None
    sins = 0.0
    cosines = 0.0
    full = 24 * 60
    for item in values:
        angle = (item / full) * 2 * math.pi
        sins += math.sin(angle)
        cosines += math.cos(angle)
    mean_angle = math.atan2(sins / len(values), cosines / len(values))
    minutes = int(round((mean_angle % (2 * math.pi)) / (2 * math.pi) * full)) % full
    return minutes


def minutes_to_hhmm(minutes: int) -> str:
    minutes = minutes % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hhmm_to_minutes(value: str) -> int:
    hour, minute = parse_hhmm(value)
    return hour * 60 + minute


def add_days(day: date, days: int) -> date:
    return day + timedelta(days=days)


def daterange(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def next_local_datetime(tz_name: str, hour: int, minute: int, after: datetime | None = None) -> datetime:
    local_now = to_user(after or now_utc(), tz_name)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)

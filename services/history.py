"""Timeline assembly for history view."""

from __future__ import annotations

from datetime import date, datetime

from database.models import SleepRecord, TimelineItem, User
from database.queries import Repo
from services.activities import history_activity_extra
from services.activities import spec_of as activity_spec
from services.daily_scores import spec_of
from services.metric_types import format_metric_value
from utils.formatting import (
    ACTIVITY_TYPES,
    ALCOHOL_TYPES,
    CAFFEINE_TYPES,
    duration_human,
    format_int_spaces,
    score_text,
    wake_kind_text,
)
from utils.quantity import format_quantity
from utils.time import (
    MONTHS_RU,
    format_date,
    format_date_long,
    format_time,
    parse_iso,
    range_bounds_utc,
    to_iso,
    to_user,
    user_now,
)

_SLEEP_STAMPS = (
    "phone_in_bed_at",
    "phone_away_at",
    "bedtime",
    "sleep_onset_at",
    "wake_time",
    "out_of_bed_at",
)


async def build_timeline(repo: Repo, user: User, start: date, end: date) -> list[TimelineItem]:
    a, b = range_bounds_utc(user.timezone, start, end)
    start_iso, end_iso = to_iso(a), to_iso(b)
    tid = user.telegram_id
    items: list[TimelineItem] = []

    for cig in await repo.list_cigarettes(tid, start_iso, end_iso):
        dt = parse_iso(cig.occurred_at)
        items.append(TimelineItem("cigarette", cig.id, dt, "🚬 Сигарета", "", {}))

    for rec in await repo.list_fooling(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        items.append(TimelineItem("fooling", rec.id, dt, "🤌 Валять дурака", "", {}))

    for rec in await repo.list_snus_packs(tid, start_iso, end_iso):
        if _in_window(rec.bought_at, a, b):
            items.append(TimelineItem("snus_buy", rec.id, parse_iso(rec.bought_at), "🟢 Купил шайбу", "", {"kind": "buy"}))
        if _in_window(rec.finished_at, a, b):
            items.append(
                TimelineItem(
                    "snus_end",
                    rec.id,
                    parse_iso(rec.finished_at),
                    "🟢 Шайба закончилась",
                    duration_human(rec.duration_minutes),
                    {"kind": "end"},
                )
            )

    now = user_now(user.timezone)
    for rec in await repo.list_sleep_overlapping(tid, start_iso, end_iso):
        items.extend(_sleep_timeline_items(rec, user, a, b, now))

    for rec in await repo.list_caffeine(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        label = CAFFEINE_TYPES.get(rec.drink_type, rec.drink_type)
        extra = format_quantity(rec.amount, rec.unit)
        items.append(TimelineItem("caffeine", rec.id, dt, f"☕ {label.capitalize()}", extra, {}))

    for rec in await repo.list_alcohol(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        label = ALCOHOL_TYPES.get(rec.drink_type, rec.drink_type)
        extra = format_quantity(rec.amount, rec.unit)
        items.append(TimelineItem("alcohol", rec.id, dt, f"🍺 {label.capitalize()}", extra, {}))

    for rec in await repo.list_activity(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        spec = activity_spec(rec.activity_type)
        label = spec.label if spec else ACTIVITY_TYPES.get(rec.activity_type, rec.activity_type)
        emoji = spec.emoji if spec else "🏃"
        items.append(
            TimelineItem(
                "activity",
                rec.id,
                dt,
                f"{emoji} {label.capitalize()}",
                history_activity_extra(rec, user.timezone),
                {},
            )
        )

    for rec in await repo.list_steps(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        items.append(
            TimelineItem(
                "steps",
                rec.id,
                dt,
                "🚶 Шаги",
                format_int_spaces(rec.steps),
                {"all_day": True},
            )
        )

    for rec in await repo.list_weight(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        kg = f"{rec.kilograms:g}".replace(".", ",")
        items.append(TimelineItem("weight", rec.id, dt, "⚖️ Вес", f"{kg} кг", {}))

    for rec in await repo.list_daily_scores(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        spec = spec_of(rec.kind)
        items.append(
            TimelineItem(
                "daily_score",
                rec.id,
                dt,
                f"{spec.emoji} {spec.label}",
                score_text(rec.score),
                {"all_day": True},
            )
        )

    for rec in await repo.list_metric_values(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        value = format_metric_value(rec, user.timezone)
        if rec.data_type == "pledge":
            items.append(TimelineItem("custom", rec.id, dt, f"📆 {rec.metric_name}", value, {"all_day": True}))
            continue
        items.append(TimelineItem("custom", rec.id, dt, f"📌 {rec.metric_name}", value, {}))

    for rec in await repo.list_markers(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        role = ""
        if rec.period_role == "start":
            role = "начало"
        elif rec.period_role == "end":
            role = "конец"
        detail = rec.comment or ""
        if role:
            detail = f"{role}" + (f" — {detail}" if detail else "")
        items.append(TimelineItem("marker", rec.id, dt, f"🔖 {rec.name}", detail, {}))

    items.sort(key=lambda x: x.occurred_at)
    return items


def _in_window(iso: str | None, start: datetime, end: datetime) -> bool:
    if not iso:
        return False
    dt = parse_iso(iso)
    return start <= dt < end


def _sleep_marks(rec: SleepRecord) -> list[datetime]:
    seen: set[datetime] = set()
    marks: list[datetime] = []
    for field in _SLEEP_STAMPS:
        value = getattr(rec, field)
        if not value:
            continue
        dt = parse_iso(value)
        if dt in seen:
            continue
        seen.add(dt)
        marks.append(dt)
    marks.sort()
    return marks


def _sleep_open(rec: SleepRecord) -> bool:
    return rec.wake_time is None and rec.out_of_bed_at is None


def _sleep_span_days(rec: SleepRecord, tz_name: str, now: datetime) -> tuple[date, date] | None:
    marks = _sleep_marks(rec)
    if not marks:
        return None
    last = now if _sleep_open(rec) else marks[-1]
    start_day = to_user(marks[0], tz_name).date()
    end_day = to_user(last, tz_name).date()
    if end_day < start_day:
        end_day = start_day
    return start_day, end_day


def _sleep_range_label(start: date, end: date) -> str:
    if start == end:
        return format_date(start)
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_RU[start.month]}"
    return f"{format_date(start)} – {format_date(end)}"


def _sleep_summary_item(rec: SleepRecord, user: User, range_start: datetime, now: datetime) -> TimelineItem | None:
    span = _sleep_span_days(rec, user.timezone, now)
    if span is None:
        return None
    start_day, end_day = span
    if _sleep_open(rec):
        title = f"😴 Сон с {format_date(start_day)}"
        detail = "ещё идёт"
    else:
        title = f"😴 Сон {_sleep_range_label(start_day, end_day)}"
        detail = duration_human(rec.duration_minutes)
        if rec.quality:
            detail = f"{detail}, {score_text(rec.quality)}" if detail != "—" else score_text(rec.quality)
    marks = _sleep_marks(rec)
    stamp = marks[0]
    if stamp < range_start:
        stamp = range_start
    return TimelineItem("sleep_night", rec.id, stamp, title, detail, {"all_day": True, "kind": "night"})


def _sleep_timeline_items(
    rec: SleepRecord,
    user: User,
    start: datetime,
    end: datetime,
    now: datetime,
) -> list[TimelineItem]:
    events: list[TimelineItem] = []

    def add(kind: str, iso: str | None, title: str, detail: str = "", extra: dict | None = None) -> None:
        if iso is None or not _in_window(iso, start, end):
            return
        events.append(TimelineItem(kind, rec.id, parse_iso(iso), title, detail, extra or {}))

    add("sleep_phone", rec.phone_in_bed_at, "📱 Лёг с телефоном", extra={"kind": "phone"})
    if rec.phone_away_at:
        title = "📵 Убрал телефон" if rec.phone_in_bed_at else "🌙 Лёг без телефона"
        add("sleep_away", rec.phone_away_at, title, extra={"kind": "away"})
    elif rec.bedtime and not rec.phone_in_bed_at:
        add("sleep_bed", rec.bedtime, "🌙 Лёг спать", extra={"kind": "bed"})
    add("sleep_onset", rec.sleep_onset_at, "💤 Заснул", extra={"kind": "onset"})
    if rec.wake_time:
        detail = duration_human(rec.duration_minutes)
        if rec.quality:
            detail += f", {score_text(rec.quality)}"
        kind_label = wake_kind_text(rec.wake_kind)
        if kind_label:
            detail = f"{detail}, {kind_label}" if detail else kind_label
        add("sleep_wake", rec.wake_time, "☀️ Проснулся", detail, extra={"kind": "wake"})
    add("sleep_up", rec.out_of_bed_at, "🛏 Встал", extra={"kind": "up"})

    span = _sleep_span_days(rec, user.timezone, now)
    span_days = (span[1] - span[0]).days + 1 if span else 1
    items: list[TimelineItem] = []
    if not events or span_days > 2:
        summary = _sleep_summary_item(rec, user, start, now)
        if summary is not None:
            items.append(summary)
    items.extend(events)
    return items


PAGE_SIZE = 8
BUTTON_LABEL_MAX = 64


def paginate(items: list, page: int, size: int = PAGE_SIZE) -> tuple[list, int, int]:
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    start = page * size
    return items[start : start + size], page, pages


def history_button_label(user: User, item: TimelineItem) -> str:
    extra = f" — {item.detail}" if item.detail else ""
    if item.extra.get("all_day"):
        text = f"{item.title}{extra}"
    else:
        text = f"{format_time(item.occurred_at, user.timezone)} {item.title}{extra}"
    return text[:BUTTON_LABEL_MAX]


def format_timeline(user: User, start: date, items: list[TimelineItem], end: date | None = None) -> str:
    view_end = end or start
    if view_end == start:
        lines = [f"📅 {format_date_long(start)}"]
    else:
        lines = [f"📅 {format_date_long(start)} — {format_date_long(view_end)}"]
    if not items:
        lines.append("\nЗаписей нет.")
        return "\n".join(lines)
    group = view_end != start
    last_day: date | None = None
    for item in items:
        day = to_user(item.occurred_at, user.timezone).date()
        if day < start:
            day = start
        elif day > view_end:
            day = view_end
        if group and day != last_day:
            lines.append(f"\n{format_date(day)}")
            last_day = day
        extra = f" — {item.detail}" if item.detail else ""
        if item.extra.get("all_day"):
            lines.append(f"{item.title}{extra}")
            continue
        time_s = format_time(item.occurred_at, user.timezone)
        lines.append(f"{time_s} {item.title}{extra}")
    return "\n".join(lines)

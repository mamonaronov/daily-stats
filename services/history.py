"""Timeline assembly for history view."""

from __future__ import annotations

from datetime import date

from database.models import TimelineItem, User
from database.queries import Repo
from utils.formatting import (
    ACTIVITY_TYPES,
    ALCOHOL_TYPES,
    CAFFEINE_TYPES,
    duration_human,
    score_text,
    truncate,
)
from utils.time import format_time, parse_iso, range_bounds_utc, to_iso, to_user


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
        items.append(TimelineItem("fooling", rec.id, dt, "🤡 Валять дурака", "", {}))

    for rec in await repo.list_snus_packs(tid, start_iso, end_iso):
        if rec.bought_at:
            dt = parse_iso(rec.bought_at)
            items.append(TimelineItem("snus_buy", rec.id, dt, "🟢 Купил шайбу", "", {"kind": "buy"}))
        if rec.finished_at:
            dt = parse_iso(rec.finished_at)
            items.append(
                TimelineItem(
                    "snus_end",
                    rec.id,
                    dt,
                    "🟢 Шайба закончилась",
                    duration_human(rec.duration_minutes),
                    {"kind": "end"},
                )
            )

    for rec in await repo.list_sleep(tid, start_iso, end_iso):
        if rec.bedtime:
            dt = parse_iso(rec.bedtime)
            items.append(TimelineItem("sleep_bed", rec.id, dt, "🌙 Лёг спать", "", {"kind": "bed"}))
        if rec.wake_time:
            dt = parse_iso(rec.wake_time)
            detail = duration_human(rec.duration_minutes)
            if rec.quality:
                detail += f", {score_text(rec.quality)}"
            items.append(TimelineItem("sleep_wake", rec.id, dt, "☀️ Проснулся", detail, {"kind": "wake"}))

    for rec in await repo.list_mood(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        items.append(TimelineItem("mood", rec.id, dt, "🙂 Настроение", score_text(rec.score), {}))

    for rec in await repo.list_wellbeing(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        detail = score_text(rec.score)
        if rec.comment:
            detail += f" — {truncate(rec.comment, 40)}"
        items.append(TimelineItem("wellbeing", rec.id, dt, "❤️ Самочувствие", detail, {}))

    for rec in await repo.list_caffeine(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        label = CAFFEINE_TYPES.get(rec.drink_type, rec.drink_type)
        extra = f"{rec.amount:g} {rec.unit}" if rec.amount is not None else ""
        items.append(TimelineItem("caffeine", rec.id, dt, f"☕ {label.capitalize()}", extra, {}))

    for rec in await repo.list_alcohol(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        label = ALCOHOL_TYPES.get(rec.drink_type, rec.drink_type)
        extra = f"{rec.amount:g} {rec.unit}" if rec.amount is not None else ""
        items.append(TimelineItem("alcohol", rec.id, dt, f"🍺 {label.capitalize()}", extra, {}))

    for rec in await repo.list_activity(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        label = ACTIVITY_TYPES.get(rec.activity_type, rec.activity_type)
        extra = duration_human(rec.duration_minutes)
        items.append(TimelineItem("activity", rec.id, dt, f"🏃 {label.capitalize()}", extra, {}))

    for rec in await repo.list_notes(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        items.append(TimelineItem("note", rec.id, dt, "📝 Заметка", truncate(rec.body), {}))

    for rec in await repo.list_metric_values(tid, start_iso, end_iso):
        dt = parse_iso(rec.occurred_at)
        value = rec.value_text
        if rec.value_number is not None:
            value = f"{rec.value_number:g}"
            if rec.unit:
                value += f" {rec.unit}"
        elif rec.value_bool is not None:
            value = "да" if rec.value_bool else "нет"
        items.append(TimelineItem("custom", rec.id, dt, f"📌 {rec.metric_name}", value or "", {}))

    items.sort(key=lambda x: x.occurred_at)
    return items


def format_timeline(user: User, start: date, items: list[TimelineItem]) -> str:
    from utils.time import format_date_long

    lines = [f"📅 {format_date_long(start)}"]
    if not items:
        lines.append("\nЗаписей нет.")
        return "\n".join(lines)
    for item in items:
        time_s = format_time(item.occurred_at, user.timezone)
        extra = f" — {item.detail}" if item.detail else ""
        lines.append(f"{time_s} {item.title}{extra}")
    return "\n".join(lines)

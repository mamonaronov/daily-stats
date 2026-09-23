"""Physical activity kinds: walk and run are intervals, the rest are durations."""

from __future__ import annotations

from dataclasses import dataclass

from utils.formatting import ACTIVITY_TYPES, duration_human
from utils.time import format_dt, format_time, parse_iso, to_user, user_today

MAX_ACTIVITY_MINUTES = 24 * 60


@dataclass(frozen=True, slots=True)
class ActivitySpec:
    key: str
    emoji: str
    mode: str

    @property
    def interval(self) -> bool:
        return self.mode == "interval"

    @property
    def label(self) -> str:
        return ACTIVITY_TYPES[self.key]

    @property
    def button(self) -> str:
        return f"{self.emoji} {self.label.capitalize()}"

    @property
    def settings_label(self) -> str:
        if self.key == "other":
            return f"{self.emoji} Другая активность"
        return self.button


ACTIVITIES: tuple[ActivitySpec, ...] = (
    ActivitySpec("walk", "🚶", "interval"),
    ActivitySpec("run", "🏃", "interval"),
    ActivitySpec("workout", "💪", "duration"),
    ActivitySpec("bike", "🚴", "duration"),
    ActivitySpec("other", "🤸", "duration"),
)

ACTIVITY_BY_KEY = {spec.key: spec for spec in ACTIVITIES}
ACTIVITY_KEYS = tuple(spec.key for spec in ACTIVITIES)
INTERVAL_KEYS = tuple(spec.key for spec in ACTIVITIES if spec.interval)


def spec_of(key: str) -> ActivitySpec | None:
    return ACTIVITY_BY_KEY.get(key)


def is_open_activity(rec) -> bool:
    spec = spec_of(getattr(rec, "activity_type", ""))
    return bool(
        spec
        and spec.interval
        and not getattr(rec, "ended_at", None)
        and rec.duration_minutes is None
    )


def activity_card_body(rec, tz: str) -> str:
    spec = spec_of(rec.activity_type)
    title = spec.button if spec else str(rec.activity_type)
    start = format_dt(parse_iso(rec.occurred_at), tz)
    lines = [title]
    if spec and spec.interval and rec.ended_at:
        lines.append(f"Начало: {start}")
        lines.append(f"Конец: {format_dt(parse_iso(rec.ended_at), tz)}")
        lines.append(f"Длительность: {duration_human(rec.duration_minutes)}")
    elif is_open_activity(rec):
        lines.append(f"Идёт с {start}")
    else:
        lines.append(f"Время: {start}")
        lines.append(f"Длительность: {duration_human(rec.duration_minutes)}")
    if rec.comment:
        lines.append(f"Комментарий: {rec.comment}")
    return "\n".join(lines)


def activity_screen_text(spec: ActivitySpec, open_rec, tz: str, *, later: bool = False) -> str:
    if spec.interval:
        if open_rec is not None:
            started = format_dt(parse_iso(open_rec.occurred_at), tz)
            return (
                f"{spec.button}\n\n"
                f"Идёт с {started}.\n"
                "Нажмите «Закончил», когда закончите."
            )
        return (
            f"{spec.button}\n\n"
            "Как сон: когда начали и когда закончили.\n\n"
            "«Начал» — старт. «Закончил» — финиш. "
            "Если старт не отмечали, спросим, когда начали."
        )
    lines = [
        spec.button,
        "",
        "Нажмите длительность — запишется, что закончили только что.",
        "Или напишите: 35, 1 час, 1ч 20м.",
        "",
        "«Было раньше» — если это было не сейчас.",
    ]
    if later:
        lines.append("Включено: после длительности спрошу, когда это было.")
    return "\n".join(lines)


def activity_today_lines(records, open_records, tz: str) -> dict[str, str]:
    grouped: dict[str, list] = {spec.key: [] for spec in ACTIVITIES}
    for rec in records:
        if rec.activity_type in grouped and not is_open_activity(rec):
            grouped[rec.activity_type].append(rec)
    open_by_type = {rec.activity_type: rec for rec in open_records}
    lines: dict[str, str] = {}
    for spec in ACTIVITIES:
        closed = grouped[spec.key]
        minutes = [item.duration_minutes for item in closed if item.duration_minutes]
        open_rec = open_by_type.get(spec.key)
        if not closed and open_rec is None:
            continue
        text = f"{spec.emoji} {spec.label}"
        if minutes:
            text += f" {duration_human(sum(minutes))}"
        elif len(closed) > 1:
            text += f" {len(closed)}"
        if open_rec is not None:
            started_at = parse_iso(open_rec.occurred_at)
            local = to_user(started_at, tz)
            started = format_time(started_at, tz) if local.date() == user_today(tz) else format_dt(started_at, tz)
            text += f" · идёт с {started}"
        lines[spec.key] = text
    return lines


def history_activity_extra(rec, tz: str) -> str:
    if is_open_activity(rec):
        return f"идёт с {format_time(parse_iso(rec.occurred_at), tz)}"
    if getattr(rec, "ended_at", None):
        end = format_time(parse_iso(rec.ended_at), tz)
        return f"до {end} · {duration_human(rec.duration_minutes)}"
    return duration_human(rec.duration_minutes)

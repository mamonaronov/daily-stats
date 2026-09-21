"""Last custom statistics ranges the user has actually opened."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from database.models import User
from database.queries import Repo
from utils.formatting import truncate
from utils.time import MONTHS_RU, format_date, format_date_long, user_today

RECENT_LIMIT = 3
_KINDS = frozenset({"since", "marker", "range"})


@dataclass(slots=True)
class RecentSpan:
    kind: str
    start: str | None = None
    end: str | None = None
    marker_id: int | None = None


def _load_obj(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _date_ok(raw: object) -> bool:
    if not isinstance(raw, str):
        return False
    try:
        date.fromisoformat(raw)
    except ValueError:
        return False
    return True


def _span_from_item(item: object) -> RecentSpan | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    if kind == "since" and _date_ok(item.get("start")):
        return RecentSpan(kind="since", start=str(item["start"]))
    if kind == "marker":
        try:
            marker_id = int(item["marker_id"])
        except (KeyError, TypeError, ValueError):
            return None
        return RecentSpan(kind="marker", marker_id=marker_id)
    if kind == "range" and _date_ok(item.get("start")) and _date_ok(item.get("end")):
        start, end = str(item["start"]), str(item["end"])
        if end < start:
            start, end = end, start
        return RecentSpan(kind="range", start=start, end=end)
    return None


def parse_recent(raw: str | None) -> list[RecentSpan]:
    items = _load_obj(raw).get("recent") or []
    if not isinstance(items, list):
        return []
    spans: list[RecentSpan] = []
    for item in items:
        span = _span_from_item(item)
        if span is not None:
            spans.append(span)
    return spans[:RECENT_LIMIT]


def _dump(raw: str | None, spans: list[RecentSpan]) -> str:
    data = _load_obj(raw)
    data["recent"] = [
        {
            "kind": span.kind,
            **({"start": span.start} if span.start else {}),
            **({"end": span.end} if span.end else {}),
            **({"marker_id": span.marker_id} if span.marker_id is not None else {}),
        }
        for span in spans[:RECENT_LIMIT]
        if span.kind in _KINDS
    ]
    return json.dumps(data, ensure_ascii=False)


def _key(span: RecentSpan) -> tuple:
    return (span.kind, span.start, span.end, span.marker_id)


def span_from_choice(period: str, data: dict) -> RecentSpan | None:
    if period == "since":
        start = data.get("since_start")
        if not _date_ok(start):
            return None
        return RecentSpan(kind="since", start=str(start))
    if period == "marker":
        try:
            marker_id = int(data["stats_marker_id"])
        except (KeyError, TypeError, ValueError):
            return None
        return RecentSpan(kind="marker", marker_id=marker_id)
    if period == "custom":
        start, end = data.get("range_start"), data.get("range_end")
        if not _date_ok(start) or not _date_ok(end):
            return None
        start, end = str(start), str(end)
        if end < start:
            start, end = end, start
        return RecentSpan(kind="range", start=start, end=end)
    return None


def push_recent(spans: list[RecentSpan], span: RecentSpan) -> list[RecentSpan]:
    key = _key(span)
    kept = [item for item in spans if _key(item) != key]
    return [span, *kept][:RECENT_LIMIT]


def span_state(span: RecentSpan) -> dict:
    if span.kind == "since":
        return {"period": "since", "since_start": span.start}
    if span.kind == "marker":
        return {"period": "marker", "stats_marker_id": span.marker_id}
    return {"period": "custom", "range_start": span.start, "range_end": span.end}


def _day_text(day: date, today: date) -> str:
    if day.year == today.year:
        return format_date(day)
    return format_date_long(day)


def _range_text(start: date, end: date, today: date) -> str:
    if start == end:
        return _day_text(start, today)
    if start.year == end.year == today.year and start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_RU[start.month]}"
    return f"{_day_text(start, today)} — {_day_text(end, today)}"


def recent_label(span: RecentSpan, *, marker_name: str | None, today: date) -> str | None:
    if span.kind == "since" and span.start:
        return truncate(f"С даты · {_day_text(date.fromisoformat(span.start), today)}", 40)
    if span.kind == "marker":
        if not marker_name:
            return None
        return truncate(f"С метки · {marker_name}", 40)
    if span.kind == "range" and span.start and span.end:
        start = date.fromisoformat(span.start)
        end = date.fromisoformat(span.end)
        if end < start:
            start, end = end, start
        return truncate(f"Период · {_range_text(start, end, today)}", 40)
    return None


async def save_recent_spans(repo: Repo, user: User, spans: list[RecentSpan]) -> User:
    await repo.update_settings(user.telegram_id, stats_prefs_json=_dump(user.stats_prefs_json, spans))
    updated = await repo.get_user(user.telegram_id)
    return updated or user


async def remember_stats_span(repo: Repo, user: User, period: str, data: dict) -> User:
    span = span_from_choice(period, data)
    if span is None:
        return user
    fresh = await repo.get_user(user.telegram_id)
    if fresh is not None:
        user = fresh
    spans = push_recent(parse_recent(user.stats_prefs_json), span)
    return await save_recent_spans(repo, user, spans)


async def recent_span_buttons(repo: Repo, user: User) -> list[tuple[str, str]]:
    spans = parse_recent(user.stats_prefs_json)
    today = user_today(user.timezone)
    kept: list[RecentSpan] = []
    buttons: list[tuple[str, str]] = []
    for span in spans:
        name = None
        if span.kind == "marker":
            if span.marker_id is None:
                continue
            marker = await repo.get_marker(span.marker_id, user.telegram_id)
            if marker is None:
                continue
            name = marker.name
        label = recent_label(span, marker_name=name, today=today)
        if label is None:
            continue
        kept.append(span)
        buttons.append((label, f"stre:{len(buttons)}"))
    if [_key(item) for item in kept] != [_key(item) for item in spans]:
        await save_recent_spans(repo, user, kept)
    return buttons

"""Today's snapshot for the main screen."""

from __future__ import annotations

from dataclasses import dataclass

from database.models import SleepRecord, SnusPack, User
from database.queries import Repo
from services.daily_scores import DAILY_SCORE_SPECS, format_score_compact
from services.metric_types import format_metric_value, is_period_open
from services.pledges import pledge_today_lines
from utils.formatting import (
    ACTIVITY_TYPES,
    ALCOHOL_TYPES,
    CAFFEINE_TYPES,
    duration_human,
    format_int_spaces,
    format_kg,
    score_text,
    wake_kind_text,
)
from utils.quantity import format_quantity, format_volume_ml, milliliters_of
from utils.time import day_bounds_utc, format_time, parse_iso, to_iso, user_today

SLEEP_IN_BED = {"with_phone", "no_phone", "asleep"}
SLEEP_OPEN = {"awake", "need_onset"}
_EMPTY_SLEEP = "нет записи"
_EMPTY_SNUS = "нет"


@dataclass(frozen=True, slots=True)
class DaySnapshot:
    cigarettes: int
    snus_line: str
    sleep_line: str
    steps: int | None = None
    weight_kg: float | None = None
    scores: dict[str, int] | None = None
    fooling: int = 0
    caffeine_line: str | None = None
    alcohol_line: str | None = None
    activity_line: str | None = None
    custom_lines: tuple[str, ...] = ()
    pledge_lines: tuple[str, ...] = ()
    marker_line: str | None = None

    def as_text(self, tracked: set[str] | None = None) -> str:
        def show(key: str) -> bool:
            return tracked is None or key in tracked

        lines: list[str] = []
        if show("cigarettes") and self.cigarettes:
            lines.append(f"🚬 {self.cigarettes}")
        if show("fooling") and self.fooling:
            lines.append(f"🤌 {self.fooling}")
        if show("snus") and self.snus_line and self.snus_line != _EMPTY_SNUS:
            lines.append(f"🟢 {self.snus_line}")
        if show("sleep") and self.sleep_line and self.sleep_line != _EMPTY_SLEEP:
            lines.append(f"😴 {self.sleep_line}")
        if show("caffeine") and self.caffeine_line:
            lines.append(f"☕ {self.caffeine_line}")
        if show("alcohol") and self.alcohol_line:
            lines.append(f"🍺 {self.alcohol_line}")
        if show("activity") and self.activity_line:
            lines.append(f"🏃 {self.activity_line}")
        if show("steps") and self.steps is not None:
            lines.append(f"🚶 {format_int_spaces(self.steps)}")
        if show("weight") and self.weight_kg is not None:
            lines.append(f"⚖️ {format_kg(self.weight_kg)}")
        recorded = self.scores or {}
        for spec in DAILY_SCORE_SPECS:
            if show(spec.key) and spec.key in recorded:
                lines.append(format_score_compact(spec, recorded[spec.key]))
        if show("custom"):
            lines.extend(self.custom_lines)
        if show("pledges"):
            lines.extend(self.pledge_lines)
        if show("markers") and self.marker_line:
            lines.append(f"🔖 {self.marker_line}")
        if not lines:
            return ""
        return "\n".join(["<b>Сегодня</b>", *lines])


def sleep_status_line(sleep: SleepRecord | None) -> str:
    if sleep is None:
        return _EMPTY_SLEEP
    phase = sleep.phase()
    if phase in {"with_phone", "no_phone"}:
        return "лёг"
    if phase == "asleep":
        return "спит"
    if sleep.duration_minutes:
        line = duration_human(sleep.duration_minutes)
        if sleep.quality:
            line += f", {score_text(sleep.quality)}"
        kind_label = wake_kind_text(sleep.wake_kind)
        if kind_label:
            line += f", {kind_label}"
        return line
    if phase in SLEEP_OPEN:
        return "не закрыт"
    if sleep.wake_time:
        return "проснулся"
    return _EMPTY_SLEEP


def snus_status_line(pack: SnusPack | None, tz: str) -> str:
    if pack is None or not pack.bought_at:
        return _EMPTY_SNUS
    return f"открыта с {format_time(parse_iso(pack.bought_at), tz)}"


def _group_in_order(items, key) -> list[tuple[object, list]]:
    groups: list[tuple[object, list]] = []
    index: dict[object, int] = {}
    for item in items:
        kind = key(item)
        pos = index.get(kind)
        if pos is None:
            index[kind] = len(groups)
            groups.append((kind, [item]))
        else:
            groups[pos][1].append(item)
    return groups


def _combined_quantity(items) -> str:
    milliliters = [milliliters_of(item.amount, item.unit) for item in items]
    if milliliters and all(value is not None for value in milliliters):
        return format_volume_ml(sum(milliliters))
    counts: list[float] = []
    for item in items:
        if item.amount is None or milliliters_of(item.amount, item.unit) is not None:
            counts = []
            break
        counts.append(item.amount)
    if counts:
        return format_quantity(sum(counts), items[0].unit)
    if len(items) > 1:
        return str(len(items))
    return format_quantity(items[0].amount, items[0].unit)


def _drink_line(records, labels: dict[str, str]) -> str | None:
    if not records:
        return None
    parts: list[str] = []
    for kind, items in _group_in_order(records, lambda rec: rec.drink_type):
        label = labels.get(kind, str(kind))
        quantity = _combined_quantity(items)
        parts.append(f"{label} {quantity}".strip() if quantity else label)
    return " · ".join(parts)


def _activity_summary(records) -> str | None:
    if not records:
        return None
    parts: list[str] = []
    for kind, items in _group_in_order(records, lambda rec: rec.activity_type):
        label = ACTIVITY_TYPES.get(kind, str(kind))
        minutes = [item.duration_minutes for item in items if item.duration_minutes]
        if minutes:
            parts.append(f"{label} {duration_human(sum(minutes))}")
        elif len(items) > 1:
            parts.append(f"{label} {len(items)}")
        else:
            parts.append(label)
    return " · ".join(parts)


def _custom_value_summary(items, tz: str) -> str:
    data_type = items[0].data_type
    if data_type == "pledge":
        return ""
    if data_type == "period":
        return " · ".join(format_metric_value(rec, tz) for rec in items)
    if data_type in {"boolean", "choice", "text", "time"}:
        return format_metric_value(items[-1], tz)
    if data_type in {"number", "duration"} and all(rec.value_number is not None for rec in items):
        total = sum(rec.value_number for rec in items)
        if data_type == "duration":
            return duration_human(int(total))
        text = f"{total:g}"
        unit = items[0].unit
        if unit:
            text += f" {unit}"
        return text
    parts = [format_metric_value(rec, tz) for rec in items]
    return " · ".join(part for part in parts if part)


def _custom_lines(values, tz: str) -> tuple[str, ...]:
    if not values:
        return ()
    lines: list[str] = []
    for _metric_id, items in _group_in_order(values, lambda rec: rec.metric_id):
        if items[0].data_type == "pledge":
            continue
        name = items[0].metric_name or "Метрика"
        summary = _custom_value_summary(items, tz)
        lines.append(f"📌 {name} {summary}".rstrip())
    return tuple(lines)


def _marker_summary(markers) -> str | None:
    if not markers:
        return None
    names: list[str] = []
    seen: set[str] = set()
    for rec in markers:
        if rec.name in seen:
            continue
        seen.add(rec.name)
        names.append(rec.name)
    return " · ".join(names)


def _snus_today_line(open_pack: SnusPack | None, today_packs: list[SnusPack], tz: str) -> str:
    if open_pack is not None:
        return snus_status_line(open_pack, tz)
    finished = [pack for pack in today_packs if pack.finished_at]
    if not finished:
        return _EMPTY_SNUS
    last = finished[-1]
    if last.duration_minutes:
        return duration_human(last.duration_minutes)
    return "закончилась"


async def _sleep_for_today(repo: Repo, user: User, start_iso: str, end_iso: str) -> SleepRecord | None:
    latest = await repo.latest_sleep(user.telegram_id)
    if latest is not None and latest.wake_time is None and latest.phase() in SLEEP_IN_BED:
        return latest
    today_sleeps = await repo.list_sleep(user.telegram_id, start_iso, end_iso)
    if today_sleeps:
        return today_sleeps[-1]
    return None


def _merge_open_custom(today_values: list, open_values: list) -> list:
    seen = {rec.id for rec in today_values}
    merged = list(today_values)
    for rec in open_values:
        if rec.id not in seen and is_period_open(rec):
            merged.append(rec)
            seen.add(rec.id)
    return merged


async def day_snapshot(repo: Repo, user: User) -> DaySnapshot:
    today = user_today(user.timezone)
    start, end = day_bounds_utc(user.timezone, today)
    start_iso, end_iso = to_iso(start), to_iso(end)
    tid = user.telegram_id
    cigarettes = await repo.list_cigarettes(tid, start_iso, end_iso)
    fooling = await repo.list_fooling(tid, start_iso, end_iso)
    open_pack = await repo.oldest_open_snus(tid)
    today_packs = await repo.list_snus_packs(tid, start_iso, end_iso)
    sleep = await _sleep_for_today(repo, user, start_iso, end_iso)
    caffeine = await repo.list_caffeine(tid, start_iso, end_iso)
    alcohol = await repo.list_alcohol(tid, start_iso, end_iso)
    activity = await repo.list_activity(tid, start_iso, end_iso)
    steps_rec = await repo.get_steps_by_day(tid, today.isoformat())
    weights = await repo.list_weight(tid, start_iso, end_iso)
    latest_kg = weights[-1].kilograms if weights else None
    score_rows = await repo.list_daily_scores_for_day(tid, today.isoformat())
    custom_today = await repo.list_metric_values(tid, start_iso, end_iso)
    custom_open = await repo.list_open_metric_values(tid)
    pledge_lines = await pledge_today_lines(repo, user)
    markers = await repo.list_markers(tid, start_iso, end_iso)
    return DaySnapshot(
        cigarettes=len(cigarettes),
        snus_line=_snus_today_line(open_pack, today_packs, user.timezone),
        sleep_line=sleep_status_line(sleep),
        steps=steps_rec.steps if steps_rec else None,
        weight_kg=latest_kg,
        scores={row.kind: row.score for row in score_rows},
        fooling=len(fooling),
        caffeine_line=_drink_line(caffeine, CAFFEINE_TYPES),
        alcohol_line=_drink_line(alcohol, ALCOHOL_TYPES),
        activity_line=_activity_summary(activity),
        custom_lines=_custom_lines(_merge_open_custom(custom_today, custom_open), user.timezone),
        pledge_lines=pledge_lines,
        marker_line=_marker_summary(markers),
    )


EMPTY_TRACKED_HINT = "Пока нет выбранных метрик — отметьте их в Настройках."


async def today_block(repo: Repo, user: User) -> str:
    from services.ui_prefs import prefs_of

    prefs = prefs_of(user)
    text = (await day_snapshot(repo, user)).as_text(prefs.tracked)
    if text:
        return text
    if not prefs.tracked:
        return EMPTY_TRACKED_HINT
    return ""

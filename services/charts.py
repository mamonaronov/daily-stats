"""Server-side matplotlib charts for a single user."""

from __future__ import annotations

import io
from collections import Counter, defaultdict
from datetime import date
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from database.models import EventMarker, EventPeriod, User
from database.queries import Repo
from services.markers import period_title
from services.statistics import daily_event_counts, daily_volume_ml, load_period
from utils.quantity import milliliters_of
from utils.time import daterange, format_date, parse_iso, to_user

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False

_PERIOD_COLORS = (
    "#4C78A8",
    "#F58518",
    "#E45756",
    "#72B7B2",
    "#54A24B",
    "#EECA3B",
    "#B279A2",
    "#FF9DA6",
)
_MARK_COLOR = "#5B5B5B"


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _day_index(days: list[date], day: date) -> int | None:
    if not days:
        return None
    if day <= days[0]:
        return 0
    if day >= days[-1]:
        return len(days) - 1
    try:
        return days.index(day)
    except ValueError:
        return None


def _paint_events(ax, days: list[date], user: User, markers: list[EventMarker], periods: list[EventPeriod]) -> None:
    if not days or (not markers and not periods):
        return
    color_of = {period.id: _PERIOD_COLORS[i % len(_PERIOD_COLORS)] for i, period in enumerate(periods)}
    for period in periods:
        if not period.start_at:
            continue
        start_day = to_user(parse_iso(period.start_at), user.timezone).date()
        end_day = to_user(parse_iso(period.end_at), user.timezone).date() if period.end_at else days[-1]
        if end_day < days[0] or start_day > days[-1]:
            continue
        x0 = _day_index(days, start_day)
        x1 = _day_index(days, end_day)
        if x0 is None or x1 is None:
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        color = color_of[period.id]
        ax.axvspan(x0 - 0.4, x1 + 0.4, color=color, alpha=0.12, zorder=0)
        mid = (x0 + x1) / 2
        ax.annotate(
            period_title(period)[:18],
            xy=(mid, 1.0),
            xycoords=("data", "axes fraction"),
            ha="center",
            va="top",
            fontsize=7,
            color=color,
        )
    used: dict[int, int] = {}
    for marker in markers:
        day = to_user(parse_iso(marker.occurred_at), user.timezone).date()
        if day < days[0] or day > days[-1]:
            continue
        x = _day_index(days, day)
        if x is None:
            continue
        color = color_of.get(marker.period_id or -1, _MARK_COLOR)
        ax.axvline(x, color=color, linestyle="--", linewidth=1, alpha=0.8, zorder=1)
        slot = used.get(x, 0)
        used[x] = slot + 1
        ax.annotate(
            marker.name[:16],
            xy=(x, 0.92 - slot * 0.08),
            xycoords=("data", "axes fraction"),
            rotation=90,
            ha="right",
            va="top",
            fontsize=7,
            color=color,
        )


def _apply_day_axis(ax, xs: list[str]) -> list[int]:
    idx = list(range(len(xs)))
    ax.set_xticks(idx)
    ax.set_xticklabels(xs)
    return idx


def _line(
    title: str,
    xs: list[str],
    ys: list[float],
    ylabel: str,
    *,
    days: list[date] | None = None,
    user: User | None = None,
    markers: list[EventMarker] | None = None,
    periods: list[EventPeriod] | None = None,
) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    idx = _apply_day_axis(ax, xs)
    ax.plot(idx, ys, marker="o", linewidth=2)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    if days and user:
        _paint_events(ax, days, user, markers or [], periods or [])
    fig.autofmt_xdate(rotation=45)
    return _png(fig)


def _bar(
    title: str,
    xs: list[str],
    ys: list[float],
    ylabel: str,
    *,
    days: list[date] | None = None,
    user: User | None = None,
    markers: list[EventMarker] | None = None,
    periods: list[EventPeriod] | None = None,
) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    idx = _apply_day_axis(ax, xs)
    ax.bar(idx, ys)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", alpha=0.3)
    if days and user:
        _paint_events(ax, days, user, markers or [], periods or [])
    fig.autofmt_xdate(rotation=45)
    return _png(fig)


async def build_charts(repo: Repo, user: User, start: date, end: date, selected: list[str]) -> list[tuple[str, bytes]]:
    data = await load_period(repo, user, start, end)
    days = daterange(start, end)
    labels = [format_date(d) for d in days]
    overlay = {
        "days": days,
        "user": user,
        "markers": data["markers"],
        "periods": data["periods"],
    }
    charts: list[tuple[str, bytes]] = []

    if "cigarettes" in selected:
        counts = daily_event_counts(user, data["cigarettes"], start, end)
        charts.append(
            ("Сигареты по дням", _line("Сигареты по дням", labels, [counts[d] for d in days], "шт.", **overlay))
        )
        hours = Counter()
        for item in data["cigarettes"]:
            hours[to_user(parse_iso(item.occurred_at), user.timezone).hour] += 1
        hour_labels = [f"{h:02d}" for h in range(24)]
        charts.append(
            (
                "Сигареты по часам",
                _bar("Сигареты по времени суток", hour_labels, [hours[h] for h in range(24)], "шт."),
            )
        )

    if "fooling" in selected:
        counts = daily_event_counts(user, data["fooling"], start, end)
        charts.append(
            (
                "Валять дурака по дням",
                _line("Валять дурака по дням", labels, [counts[d] for d in days], "раз", **overlay),
            )
        )
        hours = Counter()
        for item in data["fooling"]:
            hours[to_user(parse_iso(item.occurred_at), user.timezone).hour] += 1
        hour_labels = [f"{h:02d}" for h in range(24)]
        charts.append(
            (
                "Валять дурака по часам",
                _bar("Валять дурака по времени суток", hour_labels, [hours[h] for h in range(24)], "раз"),
            )
        )

    if "sleep" in selected:
        dur = {d: None for d in days}
        beds = {d: None for d in days}
        wakes = {d: None for d in days}
        for item in data["sleep"]:
            if item.wake_time and item.duration_minutes is not None:
                day = to_user(parse_iso(item.wake_time), user.timezone).date()
                if day in dur:
                    dur[day] = item.duration_minutes / 60
                    marker = item.sleep_onset_at or item.phone_away_at or item.bedtime
                    if marker:
                        local_bed = to_user(parse_iso(marker), user.timezone)
                        beds[day] = local_bed.hour + local_bed.minute / 60
                    local_wake = to_user(parse_iso(item.wake_time), user.timezone)
                    wakes[day] = local_wake.hour + local_wake.minute / 60
        charts.append(
            (
                "Длительность сна",
                _line("Длительность сна, ч", labels, [dur[d] or 0 for d in days], "часы", **overlay),
            )
        )
        charts.append(
            (
                "Засыпание",
                _line("Время засыпания", labels, [beds[d] or 0 for d in days], "час суток", **overlay),
            )
        )
        charts.append(
            (
                "Пробуждение",
                _line("Время пробуждения", labels, [wakes[d] or 0 for d in days], "час суток", **overlay),
            )
        )

    if "snus" in selected:
        day_set = set(days)
        buckets: dict[date, list[float]] = defaultdict(list)
        for item in data["snus"]:
            if item.finished_at and item.duration_minutes is not None:
                day = to_user(parse_iso(item.finished_at), user.timezone).date()
                if day in day_set:
                    buckets[day].append(item.duration_minutes / (24 * 60))
        ys = [mean(buckets[d]) if buckets[d] else 0.0 for d in days]
        charts.append(
            (
                "Шайба снюса",
                _line("На сколько хватило шайбы, дни", labels, ys, "дни", **overlay),
            )
        )

    if "activity" in selected:
        mins = {d: 0 for d in days}
        for item in data["activity"]:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in mins:
                mins[day] += item.duration_minutes or 0
        charts.append(
            (
                "Активность",
                _bar("Физическая активность, мин", labels, [mins[d] for d in days], "мин", **overlay),
            )
        )
    if "caffeine" in selected:
        charts.append(_drink_chart("Кофеин", "Кофеин по дням", user, data["caffeine"], days, labels, overlay))
    if "alcohol" in selected:
        charts.append(_drink_chart("Алкоголь", "Алкоголь по дням", user, data["alcohol"], days, labels, overlay))

    numeric_custom = [v for v in data["custom"] if v.value_number is not None]
    grouped: dict[str, list] = defaultdict(list)
    for item in numeric_custom:
        grouped[item.metric_name or "метрика"].append(item)
    for name, items in grouped.items():
        series = {d: 0.0 for d in days}
        buckets: dict[date, list[float]] = defaultdict(list)
        for item in items:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in series and item.value_number is not None:
                buckets[day].append(item.value_number)
        for day in days:
            series[day] = mean(buckets[day]) if buckets[day] else 0.0
        charts.append((name, _line(name, labels, [series[d] for d in days], "значение", **overlay)))
    return charts


def _drink_chart(name: str, title: str, user: User, items, days, labels, overlay: dict | None = None) -> tuple[str, bytes]:
    extra = overlay or {}
    volumes = daily_volume_ml(user, items, days[0], days[-1]) if days else {}
    has_volume = any(milliliters_of(item.amount, item.unit) for item in items)
    if has_volume:
        return (name, _line(title, labels, [volumes.get(d, 0.0) / 1000 for d in days], "л", **extra))
    counts = daily_event_counts(user, items, days[0], days[-1]) if days else {}
    return (name, _line(title, labels, [counts.get(d, 0) for d in days], "раз", **extra))

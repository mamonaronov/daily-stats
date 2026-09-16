"""Server-side matplotlib charts for a single user."""

from __future__ import annotations

import io
from collections import Counter, defaultdict
from datetime import date
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from matplotlib.patches import Patch

from database.models import EventMarker, EventPeriod, User
from database.queries import Repo
from services.daily_scores import DAILY_SCORE_KEYS, spec_of
from services.markers import period_title
from services.sleep_strips import (
    PHASE_COLORS,
    PHASE_LABELS,
    SleepStrip,
    build_sleep_strip,
    strip_title,
)
from services.statistics import daily_event_counts, daily_volume_ml, load_period
from services.ui_prefs import prefs_of
from utils.quantity import milliliters_of
from utils.time import daterange, format_date, parse_iso, to_user, user_now

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


def _png(fig, *, tight: bool = True) -> bytes:
    buf = io.BytesIO()
    if tight:
        fig.tight_layout()
    fig.savefig(buf, format="png", dpi=140, bbox_inches=None if tight else "tight")
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


_AWAKE_COLOR = "#B4B4B4"
_SLEEP_COLOR = "#1D4ED8"
_STRIP_EDGE = "#5A6F8F"


def _sleep_strip_png(strip: SleepStrip) -> bytes:
    rows = strip.rows
    n = len(rows)
    bar_h = 0.42 if n <= 31 else 0.28 if n <= 90 else 0.16
    fig_h = min(22.0, max(3.4, bar_h * n + 2.2))
    fig, ax = plt.subplots(figsize=(11, fig_h))
    ys = list(range(n))
    for y, row in zip(ys, rows):
        duration = max((row.end - row.start).total_seconds() / 3600, 1e-6)
        ax.barh(y, duration, left=0, height=0.62, color=_AWAKE_COLOR, edgecolor=_STRIP_EDGE, linewidth=0.6, zorder=1)
        for seg in row.segments:
            ax.barh(
                y,
                seg.width,
                left=seg.offset,
                height=0.62,
                color=PHASE_COLORS.get(seg.phase, _SLEEP_COLOR),
                edgecolor=_STRIP_EDGE,
                linewidth=0.4,
                zorder=2,
            )
    step = 1 if n <= 40 else 2 if n <= 80 else max(1, n // 25)
    ax.set_yticks(ys[::step])
    ax.set_yticklabels([rows[i].label for i in ys[::step]], fontsize=9 if n <= 40 else 8)
    ax.invert_yaxis()
    ax.set_xlim(0, 24)
    ax.set_ylim(n - 0.45, -1.35)
    ticks = [0, 6, 12, 18, 24]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{(strip.day_hour + t) % 24:02d}:00" for t in ticks])
    ax.tick_params(axis="x", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _draw_strip_headers(ax, strip)
    _draw_strip_legend(ax, strip)
    ax.set_xlabel("")
    fig.subplots_adjust(left=0.18, right=0.98, top=0.86, bottom=0.2)
    return _png(fig, tight=False)


def _draw_strip_headers(ax, strip: SleepStrip) -> None:
    ax.text(0, -1.05, "Начало дня", ha="left", va="center", fontsize=10, clip_on=False)
    ax.text(24, -1.05, "конец дня", ha="right", va="center", fontsize=10, clip_on=False)
    onset = strip.mean_onset_axis
    wake = strip.mean_wake_axis
    if onset is not None and 1.5 < onset < 22.5:
        ax.text(onset, -1.05, "заснул", ha="center", va="center", fontsize=10, clip_on=False)
    if wake is not None and 1.5 < wake < 22.5:
        if onset is None or abs(wake - onset) >= 2.2:
            ax.text(wake, -1.05, "проснулся", ha="center", va="center", fontsize=10, clip_on=False)


def _draw_strip_legend(ax, strip: SleepStrip) -> None:
    used = []
    for row in strip.rows:
        for seg in row.segments:
            if seg.phase not in used:
                used.append(seg.phase)
    if not used:
        return
    handles = [
        Patch(facecolor=PHASE_COLORS[phase], edgecolor=_STRIP_EDGE, label=PHASE_LABELS[phase])
        for phase in used
        if phase in PHASE_LABELS
    ]
    if not handles:
        return
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=min(4, len(handles)),
        frameon=False,
        fontsize=9,
    )


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
        strip = build_sleep_strip(
            data["sleep"],
            user.timezone,
            start,
            end,
            user_now(user.timezone),
            trim_empty_edges=prefs_of(user).hide_sleep_empty_edges,
        )
        if strip is not None:
            charts.insert(0, (strip_title(strip), _sleep_strip_png(strip)))
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
    if "steps" in selected:
        counts = {d: 0 for d in days}
        for item in data["steps"]:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in counts:
                counts[day] = item.steps
        charts.append(
            (
                "Шаги",
                _bar("Шаги по дням", labels, [counts[d] for d in days], "шаги", **overlay),
            )
        )
    if "weight" in selected:
        series = {d: float("nan") for d in days}
        buckets: dict[date, list[float]] = defaultdict(list)
        for item in data["weight"]:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in series:
                buckets[day].append(item.kilograms)
        for day, values in buckets.items():
            series[day] = values[-1]
        charts.append(
            (
                "Вес",
                _line("Вес, кг", labels, [series[d] for d in days], "кг", **overlay),
            )
        )
    for key in DAILY_SCORE_KEYS:
        if key not in selected:
            continue
        series = {d: float("nan") for d in days}
        for item in data["daily_scores"]:
            if item.kind != key:
                continue
            day = date.fromisoformat(item.day)
            if day in series:
                series[day] = item.score
        spec = spec_of(key)
        charts.append(
            (
                spec.label,
                _line(f"{spec.label}, 1–5", labels, [series[d] for d in days], "оценка", **overlay),
            )
        )
    if "caffeine" in selected:
        charts.append(_drink_chart("Кофеин", "Кофеин по дням", user, data["caffeine"], days, labels, overlay))
    if "alcohol" in selected:
        charts.append(_drink_chart("Алкоголь", "Алкоголь по дням", user, data["alcohol"], days, labels, overlay))

    wanted = {int(key[1:]) for key in selected if key.startswith("m") and key[1:].isdigit()}
    numeric_custom = [
        v for v in data["custom"] if v.value_number is not None and v.metric_id in wanted
    ]
    grouped: dict[int, list] = defaultdict(list)
    for item in numeric_custom:
        grouped[item.metric_id].append(item)
    for metric_id, items in grouped.items():
        name = items[0].metric_name or "метрика"
        series = {d: 0.0 for d in days}
        buckets: dict[date, list[float]] = defaultdict(list)
        for item in items:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in series and item.value_number is not None:
                buckets[day].append(item.value_number)
        for day in days:
            if items[0].data_type == "period":
                series[day] = sum(buckets[day]) if buckets[day] else 0.0
            else:
                series[day] = mean(buckets[day]) if buckets[day] else 0.0
        unit = "мин" if items[0].data_type == "period" else "значение"
        charts.append((name, _line(name, labels, [series[d] for d in days], unit, **overlay)))
    return charts


def _drink_chart(name: str, title: str, user: User, items, days, labels, overlay: dict | None = None) -> tuple[str, bytes]:
    extra = overlay or {}
    volumes = daily_volume_ml(user, items, days[0], days[-1]) if days else {}
    has_volume = any(milliliters_of(item.amount, item.unit) for item in items)
    if has_volume:
        return (name, _line(title, labels, [volumes.get(d, 0.0) / 1000 for d in days], "л", **extra))
    counts = daily_event_counts(user, items, days[0], days[-1]) if days else {}
    return (name, _line(title, labels, [counts.get(d, 0) for d in days], "раз", **extra))

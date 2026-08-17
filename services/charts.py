"""Server-side matplotlib charts for a single user."""

from __future__ import annotations

import io
from collections import Counter, defaultdict
from datetime import date
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from database.models import User
from database.queries import Repo
from services.statistics import daily_cigarette_counts, load_period
from utils.time import daterange, format_date, parse_iso, to_user

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _line(title: str, xs: list[str], ys: list[float], ylabel: str) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(xs, ys, marker="o", linewidth=2)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate(rotation=45)
    return _png(fig)


def _bar(title: str, xs: list[str], ys: list[float], ylabel: str) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(xs, ys)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", alpha=0.3)
    fig.autofmt_xdate(rotation=45)
    return _png(fig)


async def build_charts(repo: Repo, user: User, start: date, end: date, selected: list[str]) -> list[tuple[str, bytes]]:
    data = await load_period(repo, user, start, end)
    days = daterange(start, end)
    labels = [format_date(d) for d in days]
    charts: list[tuple[str, bytes]] = []

    if "cigarettes" in selected:
        counts = daily_cigarette_counts(user, data["cigarettes"], start, end)
        charts.append(("Сигареты по дням", _line("Сигареты по дням", labels, [counts[d] for d in days], "шт.")))
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

    if "sleep" in selected:
        dur = {d: None for d in days}
        beds = {d: None for d in days}
        wakes = {d: None for d in days}
        for item in data["sleep"]:
            if item.wake_time and item.duration_minutes is not None:
                day = to_user(parse_iso(item.wake_time), user.timezone).date()
                if day in dur:
                    dur[day] = item.duration_minutes / 60
                    if item.bedtime:
                        local_bed = to_user(parse_iso(item.bedtime), user.timezone)
                        beds[day] = local_bed.hour + local_bed.minute / 60
                    local_wake = to_user(parse_iso(item.wake_time), user.timezone)
                    wakes[day] = local_wake.hour + local_wake.minute / 60
        charts.append(
            (
                "Длительность сна",
                _line("Длительность сна, ч", labels, [dur[d] or 0 for d in days], "часы"),
            )
        )
        charts.append(
            (
                "Отход ко сну",
                _line("Время отхода ко сну", labels, [beds[d] or 0 for d in days], "час суток"),
            )
        )
        charts.append(
            (
                "Пробуждение",
                _line("Время пробуждения", labels, [wakes[d] or 0 for d in days], "час суток"),
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
                _line("На сколько хватило шайбы, дни", labels, ys, "дни"),
            )
        )

    if "mood" in selected:
        charts.append(("Настроение", _score_chart("Настроение", user, data["mood"], days, labels)))
    if "wellbeing" in selected:
        charts.append(("Самочувствие", _score_chart("Самочувствие", user, data["wellbeing"], days, labels)))
    if "activity" in selected:
        mins = {d: 0 for d in days}
        for item in data["activity"]:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in mins:
                mins[day] += item.duration_minutes or 0
        charts.append(
            (
                "Активность",
                _bar("Физическая активность, мин", labels, [mins[d] for d in days], "мин"),
            )
        )
    if "caffeine" in selected:
        counts = {d: 0 for d in days}
        for item in data["caffeine"]:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in counts:
                counts[day] += 1
        charts.append(("Кофеин", _line("Кофеин по дням", labels, [counts[d] for d in days], "раз")))
    if "alcohol" in selected:
        counts = {d: 0 for d in days}
        for item in data["alcohol"]:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in counts:
                counts[day] += 1
        charts.append(("Алкоголь", _line("Алкоголь по дням", labels, [counts[d] for d in days], "раз")))

    numeric_custom = [v for v in data["custom"] if v.value_number is not None]
    grouped: dict[str, list] = defaultdict(list)
    for item in numeric_custom:
        grouped[item.metric_name or "показатель"].append(item)
    for name, items in grouped.items():
        series = {d: 0.0 for d in days}
        buckets: dict[date, list[float]] = defaultdict(list)
        for item in items:
            day = to_user(parse_iso(item.occurred_at), user.timezone).date()
            if day in series and item.value_number is not None:
                buckets[day].append(item.value_number)
        for day in days:
            series[day] = mean(buckets[day]) if buckets[day] else 0.0
        charts.append((name, _line(name, labels, [series[d] for d in days], "значение")))
    return charts


def _score_chart(title: str, user: User, items, days, labels) -> bytes:
    buckets: dict[date, list[int]] = defaultdict(list)
    for item in items:
        day = to_user(parse_iso(item.occurred_at), user.timezone).date()
        if day in {d for d in days}:
            buckets[day].append(item.score)
    ys = [mean(buckets[d]) if buckets[d] else 0 for d in days]
    return _line(title, labels, ys, "оценка 1–5")

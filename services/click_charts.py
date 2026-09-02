"""Admin charts of user button taps (owner excluded)."""

from __future__ import annotations

import io
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from database.clicks_database import ClicksDatabase
from services.click_stats import (
    bucket_clicks_by_day,
    bucket_clicks_by_hour,
    day_axis_label,
    kind_label,
)
from utils.time import to_iso

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _kind_chart(rows: list[tuple[str, int]], title: str) -> bytes:
    labels = [kind_label(kind) for kind, _ in reversed(rows)]
    values = [count for _, count in reversed(rows)]
    height = max(3.6, 0.42 * len(labels) + 1.4)
    fig, ax = plt.subplots(figsize=(8, height))
    ax.barh(labels, values, color="#4C78A8")
    ax.set_title(title)
    ax.set_xlabel("нажатий")
    ax.grid(True, axis="x", alpha=0.3)
    return _png(fig)


def _daily_chart(days: list[tuple[date, int]], title: str) -> bytes:
    xs = [day_axis_label(day) for day, _ in days]
    ys = [count for _, count in days]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    idx = list(range(len(xs)))
    ax.plot(idx, ys, marker="o", linewidth=2, color="#4C78A8")
    ax.set_title(title)
    ax.set_ylabel("нажатий")
    ax.set_xticks(idx)
    ax.set_xticklabels(xs)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate(rotation=45)
    return _png(fig)


def _hourly_chart(hours: list[int], title: str) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4.2))
    xs = [f"{hour:02d}" for hour in range(24)]
    ax.bar(xs, hours, color="#72B7B2")
    ax.set_title(title)
    ax.set_xlabel("час")
    ax.set_ylabel("нажатий")
    ax.grid(True, axis="y", alpha=0.3)
    return _png(fig)


async def build_click_charts(
    clicks: ClicksDatabase,
    start,
    end,
    title: str,
    tz_name: str,
) -> list[tuple[str, bytes]]:
    start_iso, end_iso = to_iso(start), to_iso(end)
    stamps = await clicks.user_clicked_at(start_iso, end_iso)
    kinds = await clicks.kind_counts(start_iso, end_iso, limit=12)
    if not stamps and not kinds:
        return []
    charts: list[tuple[str, bytes]] = []
    if kinds:
        charts.append((f"Типы кнопок за {title}", _kind_chart(kinds, f"Какие кнопки нажимали за {title}")))
    days = bucket_clicks_by_day(stamps, tz_name)
    if len(days) > 1:
        charts.append((f"Нажатия по дням за {title}", _daily_chart(days, f"Нажатия пользователей за {title}")))
    hours = bucket_clicks_by_hour(stamps, tz_name)
    if any(hours):
        charts.append(
            (f"Нажатия по часам за {title}", _hourly_chart(hours, f"В какие часы нажимали за {title}"))
        )
    return charts

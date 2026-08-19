"""VPN latency charts: central tendency and ping-over-time with node spans."""

from __future__ import annotations

import io
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.dates import DateFormatter
from matplotlib.patches import Patch

from database.models import VpnLatencySample
from database.queries import Repo
from services.vpn_monitor import subscription_label
from utils.time import parse_iso

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False

_MAX_TIMELINE_OK = 1800
_MAX_TIMELINE_DOWN = 1500
_MAX_LEGEND_NODES = 16
_DOWN_COLOR = (0.82, 0.82, 0.82, 0.55)


@dataclass(slots=True)
class CentralTendency:
    mean: float
    median: float
    mode: float
    mode_count: int
    mode_binned: bool


@dataclass(slots=True)
class TimelinePoint:
    time: datetime
    ping_ms: float
    node: str
    down: bool
    color_key: str


def short_node_name(name: str | None) -> str:
    if not name or not str(name).strip():
        return "нет ноды"
    parts = [part.strip() for part in str(name).split("|")]
    if len(parts) >= 2:
        tail = parts[-1]
        if len(tail) > 28:
            tail = tail[:27] + "…"
        return f"{parts[0]} · {tail}"
    text = parts[0]
    return text if len(text) <= 32 else text[:31] + "…"


def latency_central_tendency(values: list[int]) -> CentralTendency | None:
    if not values:
        return None
    counts = Counter(values)
    mode_val, mode_count = counts.most_common(1)[0]
    binned = False
    if mode_count == 1 and len(values) > 1:
        rounded = [int(round(v / 10) * 10) for v in values]
        mode_val, mode_count = Counter(rounded).most_common(1)[0]
        binned = True
    return CentralTendency(
        mean=float(mean(values)),
        median=float(median(values)),
        mode=float(mode_val),
        mode_count=mode_count,
        mode_binned=binned,
    )


def samples_to_timeline(samples: list[VpnLatencySample], *, color_by_sub: bool) -> list[TimelinePoint]:
    points: list[TimelinePoint] = []
    for sample in samples:
        down = not bool(sample.ok) or sample.latency_ms is None
        ping = float("nan") if down else float(sample.latency_ms)
        node = short_node_name(sample.node_name)
        if color_by_sub:
            color_key = subscription_label(sample.subscription)
        else:
            color_key = node
        if down and not sample.node_name:
            color_key = "выкл"
        points.append(
            TimelinePoint(time=parse_iso(sample.measured_at), ping_ms=ping, node=node, down=down, color_key=color_key)
        )
    return points


def downsample_timeline(points: list[TimelinePoint], max_ok: int = _MAX_TIMELINE_OK, max_down: int = _MAX_TIMELINE_DOWN) -> list[TimelinePoint]:
    if len(points) <= max_ok + max_down:
        return points
    ok_idx = [i for i, point in enumerate(points) if not point.down]
    down_idx = [i for i, point in enumerate(points) if point.down]
    if len(ok_idx) > max_ok:
        step = math.ceil(len(ok_idx) / max_ok)
        ok_idx = ok_idx[::step]
    if len(down_idx) > max_down:
        step = math.ceil(len(down_idx) / max_down)
        down_idx = down_idx[::step]
    keep = sorted(set(ok_idx) | set(down_idx) | {0, len(points) - 1})
    return [points[i] for i in keep]


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _palette(keys: list[str]) -> dict[str, tuple]:
    cmap = matplotlib.colormaps["tab20"]
    n = 20
    colors: dict[str, tuple] = {}
    for i, key in enumerate(keys):
        colors[key] = cmap((i % n) / (n - 1))
    colors["выкл"] = _DOWN_COLOR
    return colors


def _merged_spans(points: list[TimelinePoint]) -> list[tuple[datetime, datetime, str, bool]]:
    if not points:
        return []
    if len(points) >= 2:
        step = points[1].time - points[0].time
        if step.total_seconds() <= 0:
            step = timedelta(seconds=10)
    else:
        step = timedelta(seconds=10)
    spans: list[tuple[datetime, datetime, str, bool]] = []
    start = points[0].time
    key = points[0].color_key
    down = points[0].down
    for _prev, current in zip(points, points[1:]):
        if current.color_key != key or current.down != down:
            spans.append((start, current.time, key, down))
            start = current.time
            key = current.color_key
            down = current.down
    spans.append((start, points[-1].time + step, key, down))
    return spans


def _time_formatter(span_seconds: float) -> DateFormatter:
    if span_seconds <= 15 * 60:
        return DateFormatter("%H:%M:%S")
    if span_seconds <= 36 * 3600:
        return DateFormatter("%H:%M")
    return DateFormatter("%d.%m %H:%M")


def render_central_chart(values: list[int], period_title: str) -> bytes:
    stats = latency_central_tendency(values)
    if stats is None:
        raise ValueError("no latencies")
    mode_note = " (~10 мс)" if stats.mode_binned else ""
    fig, (ax_bar, ax_hist) = plt.subplots(2, 1, figsize=(8.5, 7.2), height_ratios=[1, 1.35])
    labels = ["Среднее", "Медиана", f"Мода{mode_note}"]
    heights = [stats.mean, stats.median, stats.mode]
    bar_colors = ["#3b82f6", "#16a34a", "#ea580c"]
    bars = ax_bar.bar(labels, heights, color=bar_colors, width=0.55)
    ax_bar.set_ylabel("мс")
    ax_bar.set_title(f"Среднее / медиана / мода · {period_title}")
    ax_bar.grid(True, axis="y", alpha=0.3)
    for bar, value in zip(bars, heights):
        ax_bar.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{value:.0f} мс",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    bins = min(40, max(8, len(set(values))))
    ax_hist.hist(values, bins=bins, color="#93c5fd", edgecolor="#1e3a5f", alpha=0.9)
    ax_hist.axvline(stats.mean, color="#3b82f6", linewidth=2, label=f"среднее {stats.mean:.0f}")
    ax_hist.axvline(stats.median, color="#16a34a", linewidth=2, linestyle="--", label=f"медиана {stats.median:.0f}")
    ax_hist.axvline(
        stats.mode,
        color="#ea580c",
        linewidth=2,
        linestyle=":",
        label=f"мода {stats.mode:.0f} ({stats.mode_count}×)",
    )
    ax_hist.set_xlabel("Пинг, мс")
    ax_hist.set_ylabel("замеров")
    ax_hist.set_title("Распределение пинга")
    ax_hist.grid(True, axis="y", alpha=0.3)
    ax_hist.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return _png(fig)


def render_timeline_chart(points: list[TimelinePoint], period_title: str, *, color_by_sub: bool) -> bytes:
    fig, ax = plt.subplots(figsize=(10.5, 5.4))
    keys = []
    for point in points:
        if point.color_key not in keys:
            keys.append(point.color_key)
    colors = _palette(keys)
    for start, end, key, down in _merged_spans(points):
        color = _DOWN_COLOR if down and key == "выкл" else colors.get(key, _DOWN_COLOR)
        alpha = 0.18 if down else 0.10
        ax.axvspan(start, end, color=color, alpha=alpha, linewidth=0)
        if down:
            ax.axvspan(start, end, color="#ef4444", alpha=0.08, linewidth=0)

    xs = [point.time for point in points]
    ys = [point.ping_ms for point in points]
    ax.plot(xs, ys, color="#1f2937", linewidth=1.1, alpha=0.85, zorder=3)

    down_x = [point.time for point in points if point.down]
    if down_x:
        ax.scatter(down_x, [0] * len(down_x), marker="|", s=70, color="#b91c1c", zorder=4, label="выкл (без пинга)")

    ping_values = [point.ping_ms for point in points if not math.isnan(point.ping_ms)]
    ymax = max(ping_values) * 1.12 if ping_values else 100.0
    ax.set_ylim(bottom=0, top=max(ymax, 50))
    span = (points[-1].time - points[0].time).total_seconds() if len(points) > 1 else 0
    ax.xaxis.set_major_formatter(_time_formatter(span))
    ax.set_ylabel("Пинг, мс")
    ax.set_xlabel("Время (UTC)")
    color_note = "цвет фона — подписка" if color_by_sub else "цвет фона — сервер"
    ax.set_title(f"Пинг по времени · {period_title}\n{color_note}; выкл — время есть, пинга нет")
    ax.grid(True, axis="y", alpha=0.3)
    legend_keys = [key for key in keys if key != "выкл"][:_MAX_LEGEND_NODES]
    handles = [Patch(facecolor=colors[key], edgecolor="none", alpha=0.45, label=key) for key in legend_keys]
    if down_x:
        handles.append(Patch(facecolor="#ef4444", edgecolor="none", alpha=0.35, label="выкл (без пинга)"))
    if handles:
        ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=min(3, len(handles)), fontsize=8)
    fig.autofmt_xdate(rotation=35)
    return _png(fig)


def render_vpn_charts(samples: list[VpnLatencySample], period_title: str) -> list[tuple[str, bytes]]:
    if not samples:
        return []
    ok_latencies = [int(sample.latency_ms) for sample in samples if sample.ok and sample.latency_ms is not None]
    unique_nodes = {short_node_name(sample.node_name) for sample in samples if sample.node_name}
    color_by_sub = len(unique_nodes) > _MAX_LEGEND_NODES
    points = downsample_timeline(samples_to_timeline(samples, color_by_sub=color_by_sub))
    charts: list[tuple[str, bytes]] = []
    if ok_latencies:
        stats = latency_central_tendency(ok_latencies)
        caption = (
            f"Среднее {stats.mean:.0f} мс · медиана {stats.median:.0f} · "
            f"мода {stats.mode:.0f} ({stats.mode_count}×) · {period_title}"
        )
        charts.append((caption, render_central_chart(ok_latencies, period_title)))
    if points:
        charts.append((f"Пинг по времени · {period_title}", render_timeline_chart(points, period_title, color_by_sub=color_by_sub)))
    return charts


async def build_vpn_charts(repo: Repo, start: str, end: str, period_title: str) -> list[tuple[str, bytes]]:
    samples = await repo.list_vpn_samples(start, end)
    return render_vpn_charts(samples, period_title)

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
from matplotlib.colors import hsv_to_rgb
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
_DOWN_COLOR = (0.95, 0.16, 0.22, 1.0)
_BG = "#111318"
_FG = "#e8eaed"
_GRID = "#3a3f4b"
_AXIS = "#8b919a"
_CHART_DPI = 220


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
    fig.savefig(buf, format="png", dpi=_CHART_DPI, bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _apply_dark(fig, *axes) -> None:
    fig.patch.set_facecolor(_BG)
    for ax in axes:
        ax.set_facecolor(_BG)
        ax.tick_params(colors=_FG, labelsize=9)
        ax.xaxis.label.set_color(_FG)
        ax.yaxis.label.set_color(_FG)
        ax.title.set_color(_FG)
        for spine in ax.spines.values():
            spine.set_color(_AXIS)
        ax.grid(True, axis="y", color=_GRID, alpha=0.75)


def _style_legend(legend) -> None:
    if legend is None:
        return
    frame = legend.get_frame()
    frame.set_facecolor("#1c1f26")
    frame.set_edgecolor(_GRID)
    for text in legend.get_texts():
        text.set_color(_FG)


def _palette(keys: list[str]) -> dict[str, tuple]:
    colors: dict[str, tuple] = {}
    i = 0
    for key in keys:
        if key == "выкл":
            continue
        hue = (i * 0.618033988749895) % 1.0
        rgb = hsv_to_rgb((hue, 0.90, 0.98))
        colors[key] = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
        i += 1
    colors["выкл"] = _DOWN_COLOR
    return colors


def _smooth_density_curve(values: list[int]) -> tuple[list[float], list[float]]:
    lo = float(min(values))
    hi = float(max(values))
    n = len(values)
    if hi <= lo:
        return [lo - 2.0, lo, lo + 2.0], [0.0, float(n), 0.0]
    unique = len(set(values))
    n_bins = min(72, max(16, unique * 2 if unique < 40 else int(math.sqrt(n) * 2)))
    span = hi - lo
    pad = max(span * 0.12, 1.0)
    xmin, xmax = lo - pad, hi + pad
    n_full = max(n_bins + 8, 24)
    width = (xmax - xmin) / n_full
    counts = [0.0] * n_full
    for value in values:
        idx = min(n_full - 1, max(0, int((float(value) - xmin) / width)))
        counts[idx] += 1.0
    sigma = 1.6
    radius = 6
    kernel = [math.exp(-0.5 * (i / sigma) ** 2) for i in range(-radius, radius + 1)]
    ksum = sum(kernel) or 1.0
    kernel = [k / ksum for k in kernel]
    smooth = [0.0] * n_full
    for i in range(n_full):
        acc = 0.0
        for j, weight in enumerate(kernel):
            src = i + j - radius
            if 0 <= src < n_full:
                acc += counts[src] * weight
        smooth[i] = acc
    xs = [xmin + width * (i + 0.5) for i in range(n_full)]
    return xs, smooth


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
    xs, ys = _smooth_density_curve(values)
    fig, ax = plt.subplots(figsize=(11.2, 6.2))
    _apply_dark(fig, ax)
    ax.fill_between(xs, ys, color="#3b82f6", alpha=0.32, linewidth=0, zorder=1)
    ax.plot(xs, ys, color="#7dd3fc", linewidth=2.6, zorder=2)
    markers = (
        (stats.mean, "#60a5fa", "-", f"среднее {stats.mean:.0f} мс"),
        (stats.median, "#4ade80", "--", f"медиана {stats.median:.0f} мс"),
        (stats.mode, "#fb923c", ":", f"мода {stats.mode:.0f} мс ({stats.mode_count}×){mode_note}"),
    )
    for x, color, style, label in markers:
        ax.axvline(x, color=color, linewidth=2.2, linestyle=style, label=label, zorder=3)
    ax.set_xlabel("Пинг, мс")
    ax.set_ylabel("замеров")
    ax.set_title(f"Распределение пинга · среднее / медиана / мода · {period_title}")
    ax.set_ylim(bottom=0)
    _style_legend(ax.legend(loc="upper right", fontsize=9))
    return _png(fig)


def render_timeline_chart(points: list[TimelinePoint], period_title: str, *, color_by_sub: bool) -> bytes:
    fig, ax = plt.subplots(figsize=(12.4, 6.0))
    _apply_dark(fig, ax)
    keys = []
    for point in points:
        if point.color_key not in keys:
            keys.append(point.color_key)
    colors = _palette(keys)
    for start, end, key, down in _merged_spans(points):
        if down:
            color = _DOWN_COLOR
            alpha = 0.62
        else:
            color = colors.get(key, _DOWN_COLOR)
            alpha = 0.50
        ax.axvspan(start, end, color=color, alpha=alpha, linewidth=0, zorder=1)

    xs = [point.time for point in points]
    ys = [point.ping_ms for point in points]
    ax.plot(xs, ys, color="#e2e8f0", linewidth=1.45, alpha=0.95, zorder=3)

    down_x = [point.time for point in points if point.down]
    if down_x:
        ax.scatter(down_x, [0] * len(down_x), marker="|", s=90, color="#fb7185", zorder=4, label="выкл (без пинга)")

    ping_values = [point.ping_ms for point in points if not math.isnan(point.ping_ms)]
    ymax = max(ping_values) * 1.12 if ping_values else 100.0
    ax.set_ylim(bottom=0, top=max(ymax, 50))
    span = (points[-1].time - points[0].time).total_seconds() if len(points) > 1 else 0
    ax.xaxis.set_major_formatter(_time_formatter(span))
    ax.set_ylabel("Пинг, мс")
    ax.set_xlabel("Время (UTC)")
    color_note = "цвет фона — подписка" if color_by_sub else "цвет фона — сервер"
    ax.set_title(f"Пинг по времени · {period_title}\n{color_note}; выкл — время есть, пинга нет")
    legend_keys = [key for key in keys if key != "выкл"][:_MAX_LEGEND_NODES]
    handles = [Patch(facecolor=colors[key], edgecolor="none", alpha=0.85, label=key) for key in legend_keys]
    if down_x:
        handles.append(Patch(facecolor=_DOWN_COLOR, edgecolor="none", alpha=0.90, label="выкл (без пинга)"))
    if handles:
        _style_legend(
            ax.legend(
                handles=handles,
                loc="upper center",
                bbox_to_anchor=(0.5, -0.22),
                ncol=min(3, len(handles)),
                fontsize=8,
            )
        )
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

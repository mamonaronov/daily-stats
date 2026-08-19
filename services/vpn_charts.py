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
_BG = "#111318"
_FG = "#e8eaed"
_GRID = "#3a3f4b"
_AXIS = "#8b919a"
_CHART_DPI = 360
_DEFAULT_STEP = timedelta(seconds=10)
_GAP_FACTOR = 3.0
# Safe hue arc: green → cyan → blue → violet. Avoids red / orange / yellow signals.
_HUE_LO = 0.36
_HUE_HI = 0.80

SIGNAL_SERVER_OFF = "server_off"
SIGNAL_SERVICE_DOWN = "service_down"
SIGNAL_NO_PING = "no_ping"
_SIGNAL_KEYS = frozenset({SIGNAL_SERVER_OFF, SIGNAL_SERVICE_DOWN, SIGNAL_NO_PING})
_SIGNAL_ORDER = (SIGNAL_SERVER_OFF, SIGNAL_SERVICE_DOWN, SIGNAL_NO_PING)
_SIGNAL_COLORS = {
    SIGNAL_SERVER_OFF: (0.98, 0.84, 0.12),
    SIGNAL_SERVICE_DOWN: (0.96, 0.50, 0.10),
    SIGNAL_NO_PING: (0.90, 0.16, 0.20),
}
_SIGNAL_LABELS = {
    SIGNAL_SERVER_OFF: "сервер выключен",
    SIGNAL_SERVICE_DOWN: "сервис не запущен",
    SIGNAL_NO_PING: "нет пинга",
}
_SERVICE_DOWN_MARKERS = (
    "mihomo_unreachable",
    "mihomo_timeout",
    "mihomo_http_",
    "mihomo_no_now",
)


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
    signal: str | None
    color_key: str

    @property
    def down(self) -> bool:
        return self.signal is not None


def classify_vpn_signal(*, ok: bool, latency_ms: int | None, error: str | None) -> str | None:
    err = error or ""
    if any(marker in err for marker in _SERVICE_DOWN_MARKERS):
        return SIGNAL_SERVICE_DOWN
    if not ok or latency_ms is None:
        return SIGNAL_NO_PING
    return None


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
        signal = classify_vpn_signal(ok=bool(sample.ok), latency_ms=sample.latency_ms, error=sample.error)
        no_ping = not bool(sample.ok) or sample.latency_ms is None
        ping = float("nan") if no_ping else float(sample.latency_ms)
        node = short_node_name(sample.node_name)
        if color_by_sub:
            color_key = subscription_label(sample.subscription)
        else:
            color_key = node
        if signal is not None:
            color_key = signal
        points.append(TimelinePoint(time=parse_iso(sample.measured_at), ping_ms=ping, node=node, signal=signal, color_key=color_key))
    return points


def sample_step(points: list[TimelinePoint]) -> timedelta:
    deltas = [(b.time - a.time).total_seconds() for a, b in zip(points, points[1:]) if b.time > a.time]
    if not deltas:
        return _DEFAULT_STEP
    deltas.sort()
    typical = median(deltas[: max(1, (len(deltas) + 1) // 2)])
    typical = min(max(float(typical), 5.0), 30.0)
    return timedelta(seconds=typical)


def fill_server_off_gaps(
    points: list[TimelinePoint],
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    step: timedelta | None = None,
) -> list[TimelinePoint]:
    if not points:
        return points
    step = step or sample_step(points)
    limit = step * _GAP_FACTOR
    out: list[TimelinePoint] = []

    def off_point(when: datetime) -> TimelinePoint:
        return TimelinePoint(
            time=when,
            ping_ms=float("nan"),
            node="",
            signal=SIGNAL_SERVER_OFF,
            color_key=SIGNAL_SERVER_OFF,
        )

    def maybe_gap(left: datetime, right: datetime) -> None:
        if right - left <= limit:
            return
        start = left + step if out else left
        end = right
        if end - start <= timedelta(0):
            return
        out.append(off_point(start))
        if end - start > step:
            marker = end - timedelta(microseconds=1)
            if marker > start:
                out.append(off_point(marker))

    if window_start is not None:
        maybe_gap(window_start, points[0].time)
    prev = points[0]
    out.append(prev)
    for point in points[1:]:
        maybe_gap(prev.time, point.time)
        out.append(point)
        prev = point
    if window_end is not None:
        maybe_gap(prev.time, window_end)
    return out


def downsample_timeline(points: list[TimelinePoint], max_ok: int = _MAX_TIMELINE_OK, max_down: int = _MAX_TIMELINE_DOWN) -> list[TimelinePoint]:
    if len(points) <= max_ok + max_down:
        return points
    ok_idx = [i for i, point in enumerate(points) if point.signal is None]
    down_idx = [i for i, point in enumerate(points) if point.signal is not None]
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
    fig.savefig(
        buf,
        format="png",
        dpi=_CHART_DPI,
        bbox_inches="tight",
        pad_inches=0.28,
        facecolor=fig.get_facecolor(),
        edgecolor="none",
    )
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _apply_dark(fig, *axes) -> None:
    fig.patch.set_facecolor(_BG)
    for ax in axes:
        ax.set_facecolor(_BG)
        ax.tick_params(colors=_FG, labelsize=10)
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


def _server_colors(count: int) -> list[tuple[float, float, float]]:
    """Spread `count` colors on the safe hue arc.

    Few colors sit on opposite ends (strongly different). More colors pack
    closer along the same arc; saturation/value also stagger so neighbours
    still separate when hues get close.
    """
    if count <= 0:
        return []
    span = _HUE_HI - _HUE_LO
    out: list[tuple[float, float, float]] = []
    for index in range(count):
        if count == 1:
            hue = _HUE_LO + span * 0.5
        else:
            hue = _HUE_LO + span * index / (count - 1)
        if count <= 4:
            sat, val = 0.80, 0.96
        else:
            band = index % 3
            sat = 0.58 + 0.14 * band
            val = 0.98 - 0.10 * band
        rgb = hsv_to_rgb((hue, sat, val))
        out.append((float(rgb[0]), float(rgb[1]), float(rgb[2])))
    return out


def _palette(keys: list[str]) -> dict[str, tuple]:
    server_keys = [key for key in keys if key not in _SIGNAL_KEYS]
    assigned = _server_colors(len(server_keys))
    colors: dict[str, tuple] = {key: assigned[i] for i, key in enumerate(server_keys)}
    colors.update(_SIGNAL_COLORS)
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


def _merged_spans(points: list[TimelinePoint], step: timedelta) -> list[tuple[datetime, datetime, str, str | None]]:
    if not points:
        return []
    gap_limit = step * _GAP_FACTOR
    spans: list[tuple[datetime, datetime, str, str | None]] = []
    start = points[0].time
    key = points[0].color_key
    signal = points[0].signal
    for prev, current in zip(points, points[1:]):
        jumped = current.time - prev.time > gap_limit
        if current.color_key != key or current.signal != signal or jumped:
            end = current.time if not jumped else prev.time + step
            spans.append((start, end, key, signal))
            start = current.time
            key = current.color_key
            signal = current.signal
    spans.append((start, points[-1].time + step, key, signal))
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
    fig, ax = plt.subplots(figsize=(16.0, 8.0))
    _apply_dark(fig, ax)
    ax.fill_between(xs, ys, color="#3b82f6", alpha=0.32, linewidth=0, zorder=1)
    ax.plot(xs, ys, color="#7dd3fc", linewidth=2.8, zorder=2)
    markers = (
        (stats.mean, "#60a5fa", "-", f"среднее {stats.mean:.0f} мс"),
        (stats.median, "#4ade80", "--", f"медиана {stats.median:.0f} мс"),
        (stats.mode, "#c084fc", ":", f"мода {stats.mode:.0f} мс ({stats.mode_count}×){mode_note}"),
    )
    for x, color, style, label in markers:
        ax.axvline(x, color=color, linewidth=2.4, linestyle=style, label=label, zorder=3)
    ax.set_xlabel("Пинг, мс")
    ax.set_ylabel("замеров")
    ax.set_title(f"Распределение пинга · среднее / медиана / мода · {period_title}")
    ax.set_ylim(bottom=0)
    _style_legend(ax.legend(loc="upper right", fontsize=10))
    return _png(fig)


def render_timeline_chart(points: list[TimelinePoint], period_title: str, *, color_by_sub: bool) -> bytes:
    fig, ax = plt.subplots(figsize=(18.5, 8.2))
    _apply_dark(fig, ax)
    keys = []
    for point in points:
        if point.color_key not in keys:
            keys.append(point.color_key)
    colors = _palette(keys)
    step = sample_step([point for point in points if point.signal != SIGNAL_SERVER_OFF] or points)
    for start, end, key, signal in _merged_spans(points, step):
        if signal:
            color = _SIGNAL_COLORS[signal]
            alpha = 0.62
        else:
            color = colors.get(key, _SIGNAL_COLORS[SIGNAL_NO_PING])
            alpha = 0.50
        ax.axvspan(start, end, color=color, alpha=alpha, linewidth=0, zorder=1)

    xs = [point.time for point in points]
    ys = [point.ping_ms for point in points]
    ax.plot(xs, ys, color="#e2e8f0", linewidth=1.55, alpha=0.95, zorder=3)

    ping_fail_x = [point.time for point in points if point.signal == SIGNAL_NO_PING]
    if ping_fail_x:
        ax.scatter(ping_fail_x, [0] * len(ping_fail_x), marker="|", s=110, color=_SIGNAL_COLORS[SIGNAL_NO_PING], zorder=4)

    ping_values = [point.ping_ms for point in points if not math.isnan(point.ping_ms)]
    ymax = max(ping_values) * 1.12 if ping_values else 100.0
    ax.set_ylim(bottom=0, top=max(ymax, 50))
    span = (points[-1].time - points[0].time).total_seconds() if len(points) > 1 else 0
    ax.xaxis.set_major_formatter(_time_formatter(span))
    ax.set_ylabel("Пинг, мс")
    ax.set_xlabel("Время (UTC)")
    color_note = "цвет фона — подписка" if color_by_sub else "цвет фона — сервер"
    ax.set_title(f"Пинг по времени · {period_title}\n{color_note}")
    legend_keys = [key for key in keys if key not in _SIGNAL_KEYS][:_MAX_LEGEND_NODES]
    handles = [Patch(facecolor=colors[key], edgecolor="none", alpha=0.85, label=key) for key in legend_keys]
    present_signals = {point.signal for point in points if point.signal}
    for signal in _SIGNAL_ORDER:
        if signal in present_signals:
            handles.append(
                Patch(
                    facecolor=_SIGNAL_COLORS[signal],
                    edgecolor="none",
                    alpha=0.90,
                    label=_SIGNAL_LABELS[signal],
                )
            )
    if handles:
        _style_legend(
            ax.legend(
                handles=handles,
                loc="upper center",
                bbox_to_anchor=(0.5, -0.18),
                ncol=min(4, len(handles)),
                fontsize=9,
            )
        )
    fig.autofmt_xdate(rotation=35)
    return _png(fig)


def render_vpn_charts(
    samples: list[VpnLatencySample],
    period_title: str,
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[tuple[str, bytes]]:
    if not samples:
        return []
    ok_latencies = [int(sample.latency_ms) for sample in samples if sample.ok and sample.latency_ms is not None]
    unique_nodes = {short_node_name(sample.node_name) for sample in samples if sample.node_name}
    color_by_sub = len(unique_nodes) > _MAX_LEGEND_NODES
    points = samples_to_timeline(samples, color_by_sub=color_by_sub)
    points = fill_server_off_gaps(points, window_start=window_start, window_end=window_end)
    points = downsample_timeline(points)
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
    return render_vpn_charts(samples, period_title, window_start=parse_iso(start), window_end=parse_iso(end))

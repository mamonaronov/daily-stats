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
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FixedLocator

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
_CHART_DPI = 620
_DIST_DPI = 800
_MAX_PNG_BYTES = 1_000_000
_DEFAULT_STEP = timedelta(seconds=10)
_GAP_FACTOR = 3.0
_CURVE_STEPS = 20
_AVG_LINE = "#e879f9"
_PING_CEILINGS = (50.0, 100.0, 200.0, 500.0, 1000.0)
_PING_MAJORS = (
    0.0,
    10.0,
    25.0,
    50.0,
    75.0,
    100.0,
    150.0,
    200.0,
    250.0,
    300.0,
    400.0,
    500.0,
    750.0,
    1000.0,
)
_PING_MINOR_STEPS = (
    (50.0, 5.0),
    (100.0, 10.0),
    (200.0, 25.0),
    (500.0, 50.0),
    (1000.0, 100.0),
    (float("inf"), 1000.0),
)
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


def _densify_curve(
    xs: list[float],
    ys: list[float],
    steps: int = _CURVE_STEPS,
    *,
    lerp_x: bool = False,
) -> tuple[list[float], list[float]]:
    """Catmull-Rom interpolation so polylines read as round curves, not corners."""
    n = len(xs)
    if n < 3 or steps < 2:
        return xs, ys
    out_x: list[float] = []
    out_y: list[float] = []
    for i in range(n - 1):
        p0x, p0y = (xs[i - 1], ys[i - 1]) if i > 0 else (xs[i], ys[i])
        p1x, p1y = xs[i], ys[i]
        p2x, p2y = xs[i + 1], ys[i + 1]
        p3x, p3y = (xs[i + 2], ys[i + 2]) if i + 2 < n else (xs[i + 1], ys[i + 1])
        count = steps + 1 if i == n - 2 else steps
        for s in range(count):
            t = s / steps
            t2 = t * t
            t3 = t2 * t
            if lerp_x:
                x = p1x + (p2x - p1x) * t
            else:
                x = 0.5 * (
                    (2.0 * p1x)
                    + (-p0x + p2x) * t
                    + (2.0 * p0x - 5.0 * p1x + 4.0 * p2x - p3x) * t2
                    + (-p0x + 3.0 * p1x - 3.0 * p2x + p3x) * t3
                )
            y = 0.5 * (
                (2.0 * p1y)
                + (-p0y + p2y) * t
                + (2.0 * p0y - 5.0 * p1y + 4.0 * p2y - p3y) * t2
                + (-p0y + 3.0 * p1y - 3.0 * p2y + p3y) * t3
            )
            out_x.append(x)
            out_y.append(max(0.0, y))
    return out_x, out_y


def _chaikin(xs: list[float], ys: list[float], iterations: int = 4) -> tuple[list[float], list[float]]:
    """Corner-cutting: turns a polyline into a strongly rounded curve."""
    if len(xs) < 3 or iterations <= 0:
        return xs, ys
    for _ in range(iterations):
        nx = [xs[0]]
        ny = [ys[0]]
        for i in range(len(xs) - 1):
            nx.append(0.75 * xs[i] + 0.25 * xs[i + 1])
            ny.append(0.75 * ys[i] + 0.25 * ys[i + 1])
            nx.append(0.25 * xs[i] + 0.75 * xs[i + 1])
            ny.append(0.25 * ys[i] + 0.75 * ys[i + 1])
        nx.append(xs[-1])
        ny.append(ys[-1])
        xs, ys = nx, ny
    return xs, [max(0.0, y) for y in ys]


def _curve_times(times: list[datetime], values: list[float], steps: int = _CURVE_STEPS) -> tuple[list[datetime], list[float]]:
    if len(times) < 3:
        return times, values
    tz = times[0].tzinfo
    xs, ys = _chaikin([item.timestamp() for item in times], values, iterations=4)
    return [datetime.fromtimestamp(x, tz=tz) for x in xs], ys


def _finite_ping_segments(points: list[TimelinePoint], gap: timedelta) -> list[list[TimelinePoint]]:
    segments: list[list[TimelinePoint]] = []
    current: list[TimelinePoint] = []
    for point in points:
        if math.isnan(point.ping_ms):
            if current:
                segments.append(current)
                current = []
            continue
        if current and point.time - current[-1].time > gap:
            segments.append(current)
            current = []
        current.append(point)
    if current:
        segments.append(current)
    return segments


def smooth_ping_series(times: list[datetime], values: list[float]) -> list[float]:
    """Median then Gaussian-in-time: follows typical ping, not spikes."""
    n = len(values)
    if n == 0:
        return []
    if n < 5:
        return list(values)
    span = max((times[-1] - times[0]).total_seconds(), 1.0)
    sigma = min(20 * 60.0, max(30.0, span * 0.045))
    ts = [item.timestamp() for item in times]
    med_half = sigma * 0.7
    robust: list[float] = []
    start = 0
    for i, t0 in enumerate(ts):
        while start < n and ts[start] < t0 - med_half:
            start += 1
        end = i
        while end < n and ts[end] <= t0 + med_half:
            end += 1
        window = values[start:end]
        window.sort()
        robust.append(window[len(window) // 2] if window else values[i])
    out: list[float] = []
    start = 0
    limit = 3.0 * sigma
    for i, t0 in enumerate(ts):
        while start < n and ts[start] < t0 - limit:
            start += 1
        acc = 0.0
        wsum = 0.0
        for j in range(start, n):
            delta = ts[j] - t0
            if delta > limit:
                break
            weight = math.exp(-0.5 * (delta / sigma) ** 2)
            acc += robust[j] * weight
            wsum += weight
        out.append(acc / wsum if wsum else robust[i])
    return out


def nice_ping_ymax(raw: float) -> float:
    """Snap the Y top to 50/100/200/500/1000, then every 1000 ms above that."""
    raw = max(float(raw), 50.0)
    for cap in _PING_CEILINGS:
        if raw <= cap + 1e-6:
            return cap
    return float(math.ceil(raw / 1000.0) * 1000.0)


def ping_y_ticks(ymax: float) -> tuple[list[float], list[float]]:
    """Labeled familiar ping values; above 1000 ms majors are every 1000."""
    ymax = nice_ping_ymax(ymax)
    skip: set[float] = set()
    if ymax > 200:
        skip.update({10.0, 75.0, 150.0, 250.0, 300.0, 400.0, 750.0})
    if ymax > 500:
        skip.add(25.0)
    majors = [tick for tick in _PING_MAJORS if tick <= min(ymax, 1000.0) + 1e-6 and tick not in skip]
    extra = 2000.0
    while extra <= ymax + 1e-6:
        majors.append(extra)
        extra += 1000.0
    if ymax not in {round(v, 6) for v in majors}:
        majors.append(ymax)
        majors.sort()
    major_set = {round(v, 6) for v in majors}

    minors = [0.0]
    y = 0.0
    idx = 0
    while y < ymax - 1e-9:
        while idx < len(_PING_MINOR_STEPS) - 1 and y >= _PING_MINOR_STEPS[idx][0]:
            idx += 1
        y = round(y + _PING_MINOR_STEPS[idx][1], 6)
        if y <= ymax + 1e-6 and round(y, 6) not in major_set:
            minors.append(y)
        elif y > ymax + 1e-6:
            break
    minors = [tick for tick in minors if round(tick, 6) not in major_set]
    return majors, minors


def _fit_png(data: bytes, max_bytes: int = _MAX_PNG_BYTES) -> bytes:
    if len(data) <= max_bytes:
        return data
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True, compress_level=9)
    blob = buf.getvalue()
    if len(blob) <= max_bytes:
        return blob
    scale = min(0.98, math.sqrt(max_bytes / max(len(blob), 1)) * 0.99)
    for _ in range(8):
        width = max(64, int(image.width * scale))
        height = max(64, int(image.height * scale))
        buf = io.BytesIO()
        image.resize((width, height), Image.Resampling.LANCZOS).save(
            buf, format="PNG", optimize=True, compress_level=9
        )
        blob = buf.getvalue()
        if len(blob) <= max_bytes:
            return blob
        scale *= math.sqrt(max_bytes / max(len(blob), 1)) * 0.98
    return blob


def _png(fig, *, dpi: int = _CHART_DPI) -> bytes:
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=dpi,
        bbox_inches="tight",
        pad_inches=0.28,
        facecolor=fig.get_facecolor(),
        edgecolor="none",
        pil_kwargs={"compress_level": 9},
    )
    plt.close(fig)
    return _fit_png(buf.getvalue())


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
    n_bins = min(96, max(20, unique * 2 if unique < 40 else int(math.sqrt(n) * 2)))
    span = hi - lo
    pad = max(span * 0.12, 1.0)
    xmin, xmax = lo - pad, hi + pad
    n_full = max(n_bins * 12, 420)
    width = (xmax - xmin) / n_full
    counts = [0.0] * n_full
    for value in values:
        idx = min(n_full - 1, max(0, int((float(value) - xmin) / width)))
        counts[idx] += 1.0
    sigma = 5.2
    radius = 18
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
    xs, smooth = _densify_curve(xs, smooth, steps=8)
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
    ax.plot(xs, ys, color="#7dd3fc", linewidth=3.0, solid_capstyle="round", zorder=2)
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
    return _png(fig, dpi=_DIST_DPI)


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

    gap = step * _GAP_FACTOR
    ping_handles: list = []
    for segment in _finite_ping_segments(points, gap):
        xs = [point.time for point in segment]
        ys = [point.ping_ms for point in segment]
        if len(segment) >= 3:
            xs, ys = _curve_times(xs, ys)
        line = ax.plot(
            xs,
            ys,
            color="#e2e8f0",
            linewidth=1.85,
            alpha=0.92,
            zorder=3,
            solid_capstyle="round",
            solid_joinstyle="round",
        )[0]
        if not ping_handles:
            ping_handles.append(line)

    finite = [point for point in points if not math.isnan(point.ping_ms)]
    avg_handle = None
    if len(finite) >= 2:
        avg_times = [point.time for point in finite]
        avg_values = smooth_ping_series(avg_times, [point.ping_ms for point in finite])

        def _plot_avg(seg_t: list[datetime], seg_y: list[float]):
            nonlocal avg_handle
            if len(seg_t) < 2:
                return
            if len(seg_t) >= 3:
                seg_t, seg_y = _curve_times(seg_t, seg_y, steps=12)
            handle = ax.plot(
                seg_t,
                seg_y,
                color=_AVG_LINE,
                linewidth=2.7,
                alpha=0.95,
                zorder=5,
                solid_capstyle="round",
            )[0]
            if avg_handle is None:
                avg_handle = handle

        split_t: list[datetime] = []
        split_y: list[float] = []
        prev_t: datetime | None = None
        for time, value in zip(avg_times, avg_values):
            if prev_t is not None and time - prev_t > gap:
                _plot_avg(split_t, split_y)
                split_t, split_y = [], []
            split_t.append(time)
            split_y.append(value)
            prev_t = time
        _plot_avg(split_t, split_y)

    ping_fail_x = [point.time for point in points if point.signal == SIGNAL_NO_PING]
    if ping_fail_x:
        ax.scatter(ping_fail_x, [0] * len(ping_fail_x), marker="|", s=110, color=_SIGNAL_COLORS[SIGNAL_NO_PING], zorder=4)

    ping_values = [point.ping_ms for point in points if not math.isnan(point.ping_ms)]
    ymax = nice_ping_ymax(max(ping_values) if ping_values else 80.0)
    majors, minors = ping_y_ticks(ymax)
    ax.set_ylim(bottom=0, top=ymax)
    ax.yaxis.set_major_locator(FixedLocator(majors))
    ax.yaxis.set_minor_locator(FixedLocator(minors))
    ax.tick_params(axis="y", which="major", length=6)
    ax.tick_params(axis="y", which="minor", length=3.4, colors=_AXIS)
    ax.grid(True, axis="y", which="major", color=_GRID, alpha=0.75)
    ax.grid(True, axis="y", which="minor", color=_GRID, alpha=0.28, linewidth=0.55)
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
    if ping_handles:
        ping_handles[0].set_label("пинг")
        handles.append(ping_handles[0])
    if avg_handle is not None:
        avg_handle.set_label("сглаженный пинг")
        handles.append(avg_handle)
    elif len(finite) >= 2:
        handles.append(Line2D([0], [0], color=_AVG_LINE, linewidth=2.7, label="сглаженный пинг"))
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

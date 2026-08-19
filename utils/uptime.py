"""Process (bot) and host (server) uptime."""

from __future__ import annotations

import os
import time
from pathlib import Path

from utils.formatting import seconds_human

PROC_UPTIME = Path("/proc/uptime")
PROC_SELF_STAT = Path("/proc/self/stat")

_started_monotonic: float | None = None


def mark_bot_started() -> None:
    global _started_monotonic
    _started_monotonic = time.monotonic()


def host_uptime_seconds(path: Path = PROC_UPTIME) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").split()[0])
    except (OSError, IndexError, ValueError):
        return None


def process_uptime_from_stat(stat_text: str, host_uptime: float, clk_tck: int) -> float:
    comm_end = stat_text.rfind(")")
    if comm_end < 0:
        raise ValueError("invalid /proc/stat")
    fields = stat_text[comm_end + 2 :].split()
    start_ticks = int(fields[19])
    if clk_tck <= 0:
        raise ValueError("clk_tck")
    return max(0.0, host_uptime - start_ticks / clk_tck)


def bot_uptime_seconds() -> float | None:
    if _started_monotonic is not None:
        return max(0.0, time.monotonic() - _started_monotonic)
    host = host_uptime_seconds()
    if host is None:
        return None
    try:
        stat = PROC_SELF_STAT.read_text(encoding="utf-8")
        clk_tck = int(os.sysconf("SC_CLK_TCK"))
        return process_uptime_from_stat(stat, host, clk_tck)
    except (OSError, IndexError, ValueError, TypeError, OverflowError):
        return None


def uptime_report_lines() -> list[str]:
    return [
        f"Аптайм бота: {seconds_human(bot_uptime_seconds())}",
        f"Аптайм сервера: {seconds_human(host_uptime_seconds())}",
    ]

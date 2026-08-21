"""Host and bot share files in ./data so deploy waits until the process is idle."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from utils.runtime import RuntimeControl

logger = logging.getLogger(__name__)

DRAIN_REQUEST_NAME = ".deploy-drain"
DRAIN_IDLE_NAME = ".deploy-idle"
DRAIN_STATUS_NAME = ".deploy-status"

UPDATING_TEXT = "Бот обновляется, подождите минуту и нажмите ещё раз."


def drain_paths(data_dir: Path) -> tuple[Path, Path, Path]:
    return (
        data_dir / DRAIN_REQUEST_NAME,
        data_dir / DRAIN_IDLE_NAME,
        data_dir / DRAIN_STATUS_NAME,
    )


def clear_drain_files(data_dir: Path) -> None:
    for path in drain_paths(data_dir):
        path.unlink(missing_ok=True)


def format_drain_status(runtime: RuntimeControl) -> str:
    blockers = runtime.blockers()
    if not blockers:
        return "idle\n"
    parts = [f"{key}={count}" for key, count in sorted(blockers.items())]
    return "busy " + " ".join(parts) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def watch_deploy_drain(
    runtime: RuntimeControl,
    data_dir: Path,
    stop: asyncio.Event,
) -> None:
    """When the host asks to drain, reject new work and signal idle for deploy."""
    request, idle, status = drain_paths(data_dir)
    clear_drain_files(data_dir)
    try:
        while not stop.is_set():
            if request.is_file():
                if not runtime.draining:
                    runtime.begin_drain()
                    logger.info("Deploy drain started")
                _write(status, format_drain_status(runtime))
                if await runtime.wait_idle(timeout=2.0, quiet=1.5):
                    _write(idle, "ok\n")
                    _write(status, "idle\n")
                else:
                    idle.unlink(missing_ok=True)
            else:
                if runtime.draining:
                    runtime.end_drain()
                    logger.info("Deploy drain cancelled")
                idle.unlink(missing_ok=True)
            try:
                await asyncio.wait_for(stop.wait(), 0.4)
            except TimeoutError:
                pass
    finally:
        runtime.end_drain()
        clear_drain_files(data_dir)

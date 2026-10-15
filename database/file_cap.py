"""Keep one SQLite file from growing past a byte cap.

Clicks and VPN samples append forever. A day limit is not a size limit, so
each of those files also has a hard ceiling:

- ``PRAGMA max_page_count`` stops the main file at the cap even if cleanup is late.
- At 90% of the cap the oldest rows are deleted until live data is about 75%.
  That leaves headroom, so the next writes do not immediately vacuum again.
- Free pages are then returned to the OS. New files use incremental auto_vacuum,
  so reclaim truncates free pages instead of rewriting a multi-gigabyte copy.
  An older file is converted with one VACUUM the first time it actually has to shrink.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

TELEMETRY_DB_MAX_BYTES = 500 * 1024 * 1024
HIGH_WATER_RATIO = 0.90
TARGET_RATIO = 0.75
FORCE_TARGET_RATIO = 0.50
# Convert a legacy auto_vacuum=NONE file on startup only while it is still cheap.
EAGER_CONVERT_BYTES = 256 * 1024 * 1024
_DELETE_BATCH = 50_000
_VACUUM_PAGE_CHUNK = 8_000
_ALLOWED_TABLES = frozenset({"button_clicks", "vpn_latency_samples"})


def on_disk_bytes(path: Path) -> int:
    """Main file plus WAL and shared-memory sidecar."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = path if not suffix else Path(str(path) + suffix)
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
    return total


def high_water_bytes(max_bytes: int) -> int:
    return int(max_bytes * HIGH_WATER_RATIO)


def is_sqlite_full(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "full" in str(exc).lower()


def _check_table(table: str) -> str:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"unsupported table {table!r}")
    return table


async def _pragma_int(conn: aiosqlite.Connection, name: str) -> int:
    async with conn.execute(f"PRAGMA {name}") as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


async def _page_stats(conn: aiosqlite.Connection) -> tuple[int, int]:
    """Return (logical_bytes, used_bytes). Used bytes exclude the freelist."""
    page_size = max(1, await _pragma_int(conn, "page_size"))
    pages = await _pragma_int(conn, "page_count")
    freelist = await _pragma_int(conn, "freelist_count")
    logical = pages * page_size
    used = max(0, pages - freelist) * page_size
    return logical, used


async def apply_page_ceiling(conn: aiosqlite.Connection, max_bytes: int) -> None:
    """Hard-cap the main file. Not persisted: set it again on every connection."""
    if max_bytes <= 0:
        return
    page_size = max(1, await _pragma_int(conn, "page_size"))
    pages = max(1, max_bytes // page_size)
    await conn.execute(f"PRAGMA max_page_count={pages}")


async def prefer_incremental_autovacuum(conn: aiosqlite.Connection, path: Path) -> None:
    """Turn on incremental auto_vacuum while the file is still small.

    The pragma has to be set before ``journal_mode=WAL`` on a brand-new file.
    A database created the old way stays ``NONE`` until ``VACUUM``.
    """
    if await _pragma_int(conn, "auto_vacuum") == 2:
        return
    size = on_disk_bytes(path)
    if size > EAGER_CONVERT_BYTES:
        logger.info(
            "Deferring incremental auto_vacuum for %s until it needs to shrink (%s bytes)",
            path,
            size,
        )
        return
    logger.info("Converting %s to incremental auto_vacuum (%s bytes)", path, size)
    try:
        await conn.commit()
        await conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await conn.execute("VACUUM")
    except Exception:
        logger.exception("Could not convert %s to incremental auto_vacuum", path)


async def _id_bounds(conn: aiosqlite.Connection, table: str) -> tuple[int, int] | None:
    async with conn.execute(f"SELECT MIN(id), MAX(id) FROM {table}") as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    return int(row[0]), int(row[1])


async def _delete_oldest(conn: aiosqlite.Connection, table: str, *, fraction: float) -> int:
    bounds = await _id_bounds(conn, table)
    if bounds is None:
        return 0
    lo, hi = bounds
    span = hi - lo + 1
    count = int(span * fraction)
    if count < 1:
        count = 1
    # Leave the newest row for the next pass so a large first cut cannot
    # wipe data that still fits under the target.
    if count >= span and span > 1:
        count = span - 1
    count = min(count, _DELETE_BATCH)
    cut = lo + count - 1
    cur = await conn.execute(f"DELETE FROM {table} WHERE id <= ?", (cut,))
    await conn.commit()
    deleted = int(cur.rowcount or 0)
    if deleted < 0:
        return 0
    return deleted


async def _trim_used(
    conn: aiosqlite.Connection,
    table: str,
    target: int,
) -> int:
    deleted = 0
    previous_used: int | None = None
    stalled = 0
    for _ in range(20_000):
        _logical, used = await _page_stats(conn)
        if used <= target:
            return deleted
        overflow = (used - target) / max(used, 1)
        removed = await _delete_oldest(conn, table, fraction=min(1.0, overflow))
        deleted += removed
        if removed <= 0:
            return deleted
        _logical, used_after = await _page_stats(conn)
        if previous_used is not None and used_after >= previous_used:
            stalled += 1
            if stalled >= 2:
                return deleted
        else:
            stalled = 0
        previous_used = used_after
    return deleted


async def _reclaim(conn: aiosqlite.Connection, path: Path) -> None:
    """Give free pages back to the OS. VACUUM once if the file is still legacy."""
    await conn.commit()
    try:
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        logger.exception("WAL checkpoint failed while capping %s", path)
    if await _pragma_int(conn, "auto_vacuum") != 2:
        logger.warning(
            "Rewriting %s to enforce the size cap; writes to this file wait for VACUUM",
            path,
        )
        try:
            await conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            await conn.commit()
            await conn.execute("VACUUM")
        except Exception:
            logger.exception(
                "VACUUM failed while capping %s; deleted rows stay reusable but the file may not shrink",
                path,
            )
        return
    stalled = 0
    while True:
        freelist = await _pragma_int(conn, "freelist_count")
        if freelist <= 0:
            break
        chunk = min(freelist, _VACUUM_PAGE_CHUNK)
        try:
            async with conn.execute(f"PRAGMA incremental_vacuum({chunk})") as cur:
                await cur.fetchall()
        except Exception:
            logger.exception("incremental_vacuum failed for %s", path)
            break
        left = await _pragma_int(conn, "freelist_count")
        if left >= freelist:
            stalled += 1
            if stalled >= 2:
                break
        else:
            stalled = 0
    try:
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        logger.exception("WAL checkpoint failed after capping %s", path)


async def enforce_table_cap(
    conn: aiosqlite.Connection,
    path: Path,
    *,
    table: str,
    label: str,
    max_bytes: int,
    force: bool = False,
) -> int:
    """Drop oldest rows and shrink the file once it nears ``max_bytes``.

    Returns how many rows were deleted. ``max_bytes <= 0`` disables the cap.
    ``force`` is the insert path that already hit "database or disk is full":
    aim lower so the retried write has room.
    """
    if max_bytes <= 0:
        return 0
    table = _check_table(table)
    await apply_page_ceiling(conn, max_bytes)
    target_ratio = FORCE_TARGET_RATIO if force else TARGET_RATIO
    target = max(1, int(max_bytes * target_ratio))
    high = high_water_bytes(max_bytes)
    logical, used = await _page_stats(conn)
    observed = max(on_disk_bytes(path), logical)
    if not force and observed <= high and used <= high:
        return 0

    deleted = 0
    if used > target:
        deleted = await _trim_used(conn, table, target)
    elif force:
        deleted = await _delete_oldest(conn, table, fraction=0.01)
    await _reclaim(conn, path)
    await apply_page_ceiling(conn, max_bytes)

    logical_after, _used_after = await _page_stats(conn)
    after = max(on_disk_bytes(path), logical_after)
    if deleted or after > max_bytes:
        logger.info(
            "%s size cap deleted=%s before=%s after=%s limit=%s path=%s",
            label,
            deleted,
            observed,
            after,
            max_bytes,
            path,
        )
    if after > max_bytes:
        logger.warning(
            "%s database is still %s bytes, above the %s byte cap; further growth is blocked",
            label,
            after,
            max_bytes,
        )
    return deleted

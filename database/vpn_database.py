"""Separate SQLite file for VPN latency samples. Not included in backups."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import aiosqlite

from config import Config
from utils.time import now_utc, to_iso

logger = logging.getLogger(__name__)

VPN_DB_VERSION = 1
_VPN_COPY_COLS = (
    "measured_at",
    "ok",
    "latency_ms",
    "node_name",
    "subscription",
    "error",
    "host_uptime_s",
)
_VPN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vpn_latency_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    measured_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    latency_ms INTEGER,
    node_name TEXT,
    subscription TEXT,
    error TEXT,
    host_uptime_s REAL
);

CREATE INDEX IF NOT EXISTS idx_vpn_latency_measured_at
    ON vpn_latency_samples(measured_at);

CREATE INDEX IF NOT EXISTS idx_vpn_latency_sub_time
    ON vpn_latency_samples(subscription, measured_at);
"""


def resolve_vpn_db_path(config: Config) -> Path:
    path = getattr(config, "vpn_db_path", None)
    if path:
        return Path(path)
    return Path(config.db_path).with_name("vpn.sqlite3")


def vpn_retention_cutoff(keep_days: int) -> str | None:
    if keep_days <= 0:
        return None
    return to_iso(now_utc() - timedelta(days=keep_days))


class VpnDatabase:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = resolve_vpn_db_path(config)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            from database.database import DatabaseError

            raise DatabaseError("VPN database is not connected")
        return self._conn

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._apply_pragmas()
        await self.conn.executescript(_VPN_SCHEMA_SQL)
        await self.conn.execute(f"PRAGMA user_version={VPN_DB_VERSION}")
        await self.conn.commit()
        logger.info("VPN database ready path=%s", self.path)

    async def close(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            logger.exception("VPN WAL checkpoint failed during close")
        await self._conn.close()
        self._conn = None

    async def _apply_pragmas(self) -> None:
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA temp_store=MEMORY")
        await self.conn.execute("PRAGMA wal_autocheckpoint=1000")
        await self.conn.commit()

    async def vacuum(self) -> None:
        await vacuum_sqlite(self.conn)

    async def prune_older_than(self, cutoff_iso: str) -> int:
        cur = await self.conn.execute(
            "DELETE FROM vpn_latency_samples WHERE measured_at < ?",
            (cutoff_iso,),
        )
        deleted = int(cur.rowcount or 0)
        await self.conn.commit()
        return deleted

    async def prune_retained(self, keep_days: int, *, vacuum: bool = True) -> int:
        cutoff = vpn_retention_cutoff(keep_days)
        if cutoff is None:
            return 0
        deleted = await self.prune_older_than(cutoff)
        if deleted and vacuum:
            await self.vacuum()
            logger.info("VPN samples pruned=%s vacuumed path=%s", deleted, self.path)
        elif deleted:
            logger.info("VPN samples pruned=%s path=%s", deleted, self.path)
        return deleted


async def vacuum_sqlite(conn: aiosqlite.Connection) -> None:
    """Rewrite the file so DELETE/DROP actually shrinks it. Not a transaction."""
    await conn.commit()
    await conn.execute("VACUUM")


async def diary_vpn_rowcount(diary_conn: aiosqlite.Connection) -> int:
    async with diary_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vpn_latency_samples'"
    ) as cur:
        if await cur.fetchone() is None:
            return 0
    async with diary_conn.execute("SELECT COUNT(*) AS c FROM vpn_latency_samples") as cur:
        row = await cur.fetchone()
    return int(row["c"]) if row else 0


async def export_legacy_vpn_samples(
    diary_conn: aiosqlite.Connection,
    vpn_db: VpnDatabase,
    keep_days: int,
) -> int:
    """Copy retained samples from the diary DB before that table is dropped."""
    async with diary_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vpn_latency_samples'"
    ) as cur:
        if await cur.fetchone() is None:
            return 0
    async with diary_conn.execute("PRAGMA table_info(vpn_latency_samples)") as cur:
        present = {str(row[1]) for row in await cur.fetchall()}
    select_parts = [
        name if name in present else f"NULL AS {name}" for name in _VPN_COPY_COLS
    ]
    sql = f"SELECT {', '.join(select_parts)} FROM vpn_latency_samples"
    params: tuple[str, ...] = ()
    cutoff = vpn_retention_cutoff(keep_days)
    if cutoff is not None:
        sql += " WHERE measured_at >= ?"
        params = (cutoff,)
    placeholders = ", ".join("?" for _ in _VPN_COPY_COLS)
    insert_sql = (
        f"INSERT INTO vpn_latency_samples ({', '.join(_VPN_COPY_COLS)}) "
        f"VALUES ({placeholders})"
    )
    copied = 0
    async with diary_conn.execute(sql, params) as cur:
        while True:
            batch = await cur.fetchmany(500)
            if not batch:
                break
            await vpn_db.conn.executemany(insert_sql, [tuple(row) for row in batch])
            copied += len(batch)
    await vpn_db.conn.commit()
    if copied:
        logger.info("Copied %s VPN samples from diary DB to %s", copied, vpn_db.path)
    return copied

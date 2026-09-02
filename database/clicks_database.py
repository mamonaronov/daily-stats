"""Separate SQLite file for button-click analytics. Not included in backups."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

from config import Config

logger = logging.getLogger(__name__)

CLICKS_DB_VERSION = 1
_CLICKS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS button_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    clicked_at TEXT NOT NULL,
    button_kind TEXT NOT NULL,
    callback_data TEXT NOT NULL,
    button_text TEXT,
    is_owner INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_clicks_at
    ON button_clicks(clicked_at);

CREATE INDEX IF NOT EXISTS idx_clicks_owner_at
    ON button_clicks(is_owner, clicked_at);

CREATE INDEX IF NOT EXISTS idx_clicks_kind_at
    ON button_clicks(button_kind, clicked_at);

CREATE INDEX IF NOT EXISTS idx_clicks_user_at
    ON button_clicks(telegram_id, clicked_at);
"""


def resolve_clicks_db_path(config: Config) -> Path:
    path = getattr(config, "clicks_db_path", None)
    if path:
        return Path(path)
    return Path(config.db_path).with_name("clicks.sqlite3")


class ClicksDatabase:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = resolve_clicks_db_path(config)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            from database.database import DatabaseError

            raise DatabaseError("Clicks database is not connected")
        return self._conn

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._apply_pragmas()
        await self.conn.executescript(_CLICKS_SCHEMA_SQL)
        await self.conn.execute(f"PRAGMA user_version={CLICKS_DB_VERSION}")
        await self.conn.commit()
        logger.info("Clicks database ready path=%s", self.path)

    async def close(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            logger.exception("Clicks WAL checkpoint failed during close")
        await self._conn.close()
        self._conn = None

    async def _apply_pragmas(self) -> None:
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA temp_store=MEMORY")
        await self.conn.execute("PRAGMA wal_autocheckpoint=1000")
        await self.conn.commit()

    async def record(
        self,
        *,
        telegram_id: int,
        clicked_at: str,
        button_kind: str,
        callback_data: str,
        button_text: str | None,
        is_owner: bool,
    ) -> int:
        cur = await self.conn.execute(
            """
            INSERT INTO button_clicks
                (telegram_id, clicked_at, button_kind, callback_data, button_text, is_owner)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                clicked_at,
                button_kind,
                callback_data,
                button_text,
                1 if is_owner else 0,
            ),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def _count(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    async def overview(self, owner_day_start_iso: str) -> dict[str, Any]:
        users_total = await self._count(
            "SELECT COUNT(*) FROM button_clicks WHERE is_owner = 0"
        )
        owner_total = await self._count(
            "SELECT COUNT(*) FROM button_clicks WHERE is_owner = 1"
        )
        owner_today = await self._count(
            "SELECT COUNT(*) FROM button_clicks WHERE is_owner = 1 AND clicked_at >= ?",
            (owner_day_start_iso,),
        )
        async with self.conn.execute(
            """
            SELECT telegram_id, clicked_at, button_kind, callback_data, button_text
            FROM button_clicks
            WHERE is_owner = 0
            ORDER BY clicked_at DESC, id DESC
            LIMIT 1
            """
        ) as cur:
            row = await cur.fetchone()
        last_user = dict(row) if row else None
        return {
            "users_total": users_total,
            "owner_total": owner_total,
            "owner_today": owner_today,
            "last_user": last_user,
        }

    async def period_user_summary(self, start_iso: str, end_iso: str) -> dict[str, int]:
        taps = await self._count(
            """
            SELECT COUNT(*) FROM button_clicks
            WHERE is_owner = 0 AND clicked_at >= ? AND clicked_at < ?
            """,
            (start_iso, end_iso),
        )
        people = await self._count(
            """
            SELECT COUNT(DISTINCT telegram_id) FROM button_clicks
            WHERE is_owner = 0 AND clicked_at >= ? AND clicked_at < ?
            """,
            (start_iso, end_iso),
        )
        return {"taps": taps, "people": people}

    async def kind_counts(
        self, start_iso: str, end_iso: str, *, limit: int = 20
    ) -> list[tuple[str, int]]:
        async with self.conn.execute(
            """
            SELECT button_kind, COUNT(*) AS c
            FROM button_clicks
            WHERE is_owner = 0 AND clicked_at >= ? AND clicked_at < ?
            GROUP BY button_kind
            ORDER BY c DESC, button_kind ASC
            LIMIT ?
            """,
            (start_iso, end_iso, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [(str(row["button_kind"]), int(row["c"])) for row in rows]

    async def top_callbacks(
        self, start_iso: str, end_iso: str, *, limit: int = 12
    ) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """
            SELECT
                callback_data,
                button_kind,
                MAX(button_text) AS button_text,
                COUNT(*) AS c
            FROM button_clicks
            WHERE is_owner = 0 AND clicked_at >= ? AND clicked_at < ?
            GROUP BY callback_data, button_kind
            ORDER BY c DESC, callback_data ASC
            LIMIT ?
            """,
            (start_iso, end_iso, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def user_clicked_at(self, start_iso: str, end_iso: str) -> list[str]:
        """Raw UTC ISO timestamps of user taps — for charts and later UX work."""
        async with self.conn.execute(
            """
            SELECT clicked_at FROM button_clicks
            WHERE is_owner = 0 AND clicked_at >= ? AND clicked_at < ?
            ORDER BY clicked_at ASC
            """,
            (start_iso, end_iso),
        ) as cur:
            rows = await cur.fetchall()
        return [str(row["clicked_at"]) for row in rows]

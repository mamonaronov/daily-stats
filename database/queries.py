"""Parameterized data-access layer. Every user query is scoped by telegram_id."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

import aiosqlite

from database.database import Database
from database.models import (
    ActivityRecord,
    AlcoholRecord,
    BalanceOp,
    CaffeineRecord,
    Cigarette,
    CustomMetric,
    CustomValue,
    EventMarker,
    EventPeriod,
    Fooling,
    SleepRecord,
    SnusPack,
    User,
    VpnLatencySample,
)
from utils.time import now_utc, to_iso


def _latency_percentile(sorted_ms: list[int], p: float) -> int | None:
    if not sorted_ms:
        return None
    n = len(sorted_ms)
    idx = min(n - 1, max(0, math.ceil(p * n) - 1))
    return sorted_ms[idx]


# Diary event tables counted for admin cards and abuse alerts.
_DIARY_COUNT_SPECS: tuple[tuple[str, str], ...] = (
    ("cigarettes", "occurred_at"),
    ("fooling", "occurred_at"),
    ("snus_packs", "COALESCE(bought_at, created_at)"),
    ("sleep_records", "COALESCE(out_of_bed_at, wake_time, sleep_onset_at, phone_away_at, phone_in_bed_at, bedtime, created_at)"),
    ("caffeine_records", "occurred_at"),
    ("alcohol_records", "occurred_at"),
    ("activity_records", "occurred_at"),
    ("custom_metric_values", "occurred_at"),
    ("event_markers", "occurred_at"),
)


def _count_ge(values: list[int], threshold: int | None) -> int:
    if threshold is None:
        return 0
    return sum(1 for value in values if value >= threshold)


def _apply_vpn_tail_stats(items: list[dict[str, Any]], grouped: dict[Any, list[int]], key) -> None:
    for item in items:
        values = sorted(grouped.get(key(item), []))
        p95_ms = _latency_percentile(values, 0.95)
        p99_ms = _latency_percentile(values, 0.99)
        p99_9_ms = _latency_percentile(values, 0.999)
        item["p95_ms"] = p95_ms
        item["p99_ms"] = p99_ms
        item["p99_9_ms"] = p99_9_ms
        item["p95_count"] = _count_ge(values, p95_ms)
        item["p99_count"] = _count_ge(values, p99_ms)
        item["p99_9_count"] = _count_ge(values, p99_9_ms)


logger = logging.getLogger(__name__)

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)

KEEP_TABLES = frozenset({"system_info"})
OWNER_SCOPED_TABLES = frozenset({"users", "user_settings"})
PURGE_CONFIRM_PHRASE = "ОЧИСТИТЬ БАЗУ"
# Keep in sync with services.vpn_charts._VPN_ISSUE_MARKERS (counted as no_ping).
_VPN_ISSUE_SQL = (
    "COALESCE(error, '') LIKE '%mihomo_unreachable%'"
    " OR COALESCE(error, '') LIKE '%mihomo_timeout%'"
    " OR COALESCE(error, '') LIKE '%mihomo_http_%'"
    " OR COALESCE(error, '') LIKE '%mihomo_no_now%'"
)
_VPN_OK_PING_SQL = f"NOT ({_VPN_ISSUE_SQL}) AND ok = 1 AND latency_ms IS NOT NULL"
_VPN_SAMPLE_COLS = (
    "id, measured_at, ok, latency_ms, node_name, subscription, error, host_uptime_s"
)


class SqlError(ValueError):
    """Invalid admin SQL or table identifier."""


def quote_ident(name: str) -> str:
    if not IDENT_RE.fullmatch(name):
        raise SqlError("Некорректное имя таблицы")
    return f'"{name}"'


def sql_leading_keyword(sql: str) -> str:
    text = _SQL_COMMENT_RE.sub(" ", sql).strip()
    if not text:
        return ""
    return text.split(None, 1)[0].upper().rstrip(";")


def assert_sql_allowed(sql: str) -> str:
    keyword = sql_leading_keyword(sql)
    if not keyword:
        raise SqlError("Пустой запрос")
    if keyword in {"ATTACH", "DETACH"}:
        raise SqlError("ATTACH и DETACH запрещены")
    stripped = _SQL_COMMENT_RE.sub(" ", sql).strip()
    if keyword == "VACUUM" and re.search(r"\bINTO\b", stripped, re.IGNORECASE):
        raise SqlError("VACUUM INTO запрещён")
    return keyword


USER_SELECT = """
SELECT u.telegram_id, u.username, u.first_name, u.last_name, u.registered_at,
       u.timezone, u.status, u.last_activity_at, u.balance, u.daily_price,
       u.paid_until_date, u.last_charge_date, u.deleted_at, u.bot_blocked_at,
       u.created_at, u.updated_at,
       COALESCE(s.default_sleep_time, '23:00') AS default_sleep_time,
       s.stats_prefs_json
FROM users u
LEFT JOIN user_settings s ON s.telegram_id = u.telegram_id
"""


def _user(row: aiosqlite.Row) -> User:
    return User(**dict(row))


def _opt(factory, row: aiosqlite.Row | None):
    return factory(**dict(row)) if row else None


class Repo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @property
    def conn(self) -> aiosqlite.Connection:
        return self.db.conn

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, tuple(params))

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchall()

    # --- users ---

    async def get_user(self, telegram_id: int) -> User | None:
        row = await self.fetchone(USER_SELECT + " WHERE u.telegram_id = ?", (telegram_id,))
        return _user(row) if row else None

    async def create_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        timezone: str,
        daily_price: float,
        default_sleep_time: str,
    ) -> User:
        ts = to_iso(now_utc())
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            await self.conn.execute(
                """
                INSERT INTO users (
                    telegram_id, username, first_name, last_name, registered_at,
                    timezone, status, last_activity_at, balance, daily_price,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?, ?)
                """,
                (
                    telegram_id,
                    username,
                    first_name,
                    last_name,
                    ts,
                    timezone,
                    ts,
                    daily_price,
                    ts,
                    ts,
                ),
            )
            await self.conn.execute(
                """
                INSERT INTO user_settings (telegram_id, default_sleep_time)
                VALUES (?, ?)
                """,
                (telegram_id, default_sleep_time),
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
        user = await self.get_user(telegram_id)
        assert user is not None
        return user

    async def restore_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> User:
        ts = to_iso(now_utc())
        await self.conn.execute(
            """
            UPDATE users
            SET status = 'active',
                deleted_at = NULL,
                bot_blocked_at = NULL,
                username = ?,
                first_name = ?,
                last_name = ?,
                last_activity_at = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (username, first_name, last_name, ts, ts, telegram_id),
        )
        await self.conn.commit()
        user = await self.get_user(telegram_id)
        assert user is not None
        return user

    async def touch_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        ts = to_iso(now_utc())
        await self.conn.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?, last_name = ?,
                last_activity_at = ?, updated_at = ?,
                bot_blocked_at = CASE WHEN status = 'bot_blocked' THEN bot_blocked_at ELSE bot_blocked_at END,
                status = CASE WHEN status = 'bot_blocked' THEN 'active' ELSE status END
            WHERE telegram_id = ? AND deleted_at IS NULL
            """,
            (username, first_name, last_name, ts, ts, telegram_id),
        )
        await self.conn.commit()

    async def set_timezone(self, telegram_id: int, timezone: str) -> None:
        ts = to_iso(now_utc())
        await self.conn.execute(
            "UPDATE users SET timezone = ?, updated_at = ? WHERE telegram_id = ?",
            (timezone, ts, telegram_id),
        )
        await self.conn.commit()

    async def set_status(self, telegram_id: int, status: str) -> None:
        ts = to_iso(now_utc())
        await self.conn.execute(
            "UPDATE users SET status = ?, updated_at = ? WHERE telegram_id = ?",
            (status, ts, telegram_id),
        )
        await self.conn.commit()

    async def mark_deleted(self, user: User, reason: str = "user_request") -> None:
        ts = to_iso(now_utc())
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            await self.conn.execute(
                """
                UPDATE users
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE telegram_id = ?
                """,
                (ts, ts, user.telegram_id),
            )
            await self.conn.execute(
                """
                INSERT INTO deleted_accounts (telegram_id, username, first_name, deleted_at, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user.telegram_id, user.username, user.first_name, ts, reason),
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def mark_bot_blocked(self, telegram_id: int) -> None:
        ts = to_iso(now_utc())
        await self.conn.execute(
            """
            UPDATE users
            SET status = CASE WHEN status IN ('deleted', 'banned') THEN status ELSE 'bot_blocked' END,
                bot_blocked_at = ?, updated_at = ?
            WHERE telegram_id = ?
            """,
            (ts, ts, telegram_id),
        )
        await self.conn.commit()

    async def list_active_billable(self) -> list[User]:
        rows = await self.fetchall(
            USER_SELECT + " WHERE u.deleted_at IS NULL AND u.status IN ('active', 'bot_blocked')"
        )
        return [_user(r) for r in rows]

    async def list_broadcast_users(self) -> list[User]:
        rows = await self.fetchall(
            USER_SELECT
            + """
            WHERE u.deleted_at IS NULL
              AND u.status = 'active'
            ORDER BY u.telegram_id
            """
        )
        return [_user(r) for r in rows]

    async def search_users(self, query: str, limit: int = 20) -> list[User]:
        like = f"%{query.strip().lstrip('@')}%"
        if query.strip().isdigit():
            rows = await self.fetchall(
                USER_SELECT + " WHERE u.telegram_id = ? LIMIT ?",
                (int(query.strip()), limit),
            )
            if rows:
                return [_user(r) for r in rows]
        rows = await self.fetchall(
            USER_SELECT
            + """
            WHERE u.username LIKE ? OR u.first_name LIKE ? OR u.last_name LIKE ?
            ORDER BY u.last_activity_at DESC
            LIMIT ?
            """,
            (like, like, like, limit),
        )
        return [_user(r) for r in rows]

    async def list_users_page(self, offset: int, limit: int = 10) -> list[User]:
        rows = await self.fetchall(
            USER_SELECT + " ORDER BY u.registered_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [_user(r) for r in rows]

    async def users_count(self) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS c FROM users")
        return int(row["c"]) if row else 0

    async def user_stats_counts(self) -> dict[str, int]:
        row = await self.fetchone(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status = 'active' AND deleted_at IS NULL THEN 1 ELSE 0 END) AS active,
              SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted,
              SUM(CASE WHEN status = 'banned' THEN 1 ELSE 0 END) AS banned,
              SUM(CASE WHEN status = 'bot_blocked' THEN 1 ELSE 0 END) AS bot_blocked,
              SUM(CASE WHEN deleted_at IS NULL AND status = 'active'
                        AND daily_price > 0
                        AND (paid_until_date IS NULL OR paid_until_date < date('now'))
                        AND balance < daily_price THEN 1 ELSE 0 END) AS unpaid
            FROM users
            """
        )
        return {k: int(row[k] or 0) for k in row.keys()} if row else {}

    async def service_started_at(self) -> str | None:
        row = await self.fetchone("SELECT MIN(registered_at) AS ts FROM users")
        return str(row["ts"]) if row and row["ts"] else None

    async def update_settings(
        self,
        telegram_id: int,
        default_sleep_time: str | None = None,
        stats_prefs_json: str | None = None,
    ) -> None:
        current = await self.fetchone(
            "SELECT * FROM user_settings WHERE telegram_id = ?", (telegram_id,)
        )
        if current is None:
            await self.conn.execute(
                """
                INSERT INTO user_settings (telegram_id, default_sleep_time, stats_prefs_json)
                VALUES (?, ?, ?)
                """,
                (
                    telegram_id,
                    default_sleep_time or "23:00",
                    stats_prefs_json,
                ),
            )
        else:
            await self.conn.execute(
                """
                UPDATE user_settings
                SET default_sleep_time = COALESCE(?, default_sleep_time),
                    stats_prefs_json = COALESCE(?, stats_prefs_json)
                WHERE telegram_id = ?
                """,
                (default_sleep_time, stats_prefs_json, telegram_id),
            )
        await self.conn.commit()

    async def set_daily_price(self, telegram_id: int, price: float) -> None:
        ts = to_iso(now_utc())
        await self.conn.execute(
            "UPDATE users SET daily_price = ?, updated_at = ? WHERE telegram_id = ?",
            (price, ts, telegram_id),
        )
        await self.conn.commit()

    async def set_charge_progress(self, telegram_id: int, last_charge_date: str, paid_until: str | None) -> None:
        ts = to_iso(now_utc())
        await self.conn.execute(
            """
            UPDATE users
            SET last_charge_date = ?, paid_until_date = COALESCE(?, paid_until_date), updated_at = ?
            WHERE telegram_id = ?
            """,
            (last_charge_date, paid_until, ts, telegram_id),
        )
        await self.conn.commit()

    # --- finance ---

    async def get_operation_by_key(self, key: str) -> BalanceOp | None:
        row = await self.fetchone(
            "SELECT * FROM balance_operations WHERE idempotency_key = ?", (key,)
        )
        return _opt(BalanceOp, row)

    async def apply_balance_change(
        self,
        telegram_id: int,
        operation_type: str,
        *,
        delta: float | None = None,
        set_to: float | None = None,
        comment: str | None = None,
        performed_by: int | None = None,
        idempotency_key: str | None = None,
        paid_until_date: str | None = None,
        last_charge_date: str | None = None,
    ) -> tuple[bool, float, float]:
        """Atomic balance mutation. Returns (applied, before, after)."""
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            if idempotency_key:
                existing = await self.fetchone(
                    "SELECT * FROM balance_operations WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
                if existing:
                    await self.conn.commit()
                    return False, float(existing["balance_before"]), float(existing["balance_after"])

            row = await self.fetchone(
                "SELECT balance FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            if row is None:
                await self.conn.rollback()
                raise ValueError("user not found")
            before = float(row["balance"])
            if set_to is not None:
                after = float(set_to)
                amount = after - before
            else:
                amount = float(delta or 0)
                after = before + amount
            ts = to_iso(now_utc())
            await self.conn.execute(
                """
                INSERT INTO balance_operations (
                    telegram_id, amount, operation_type, balance_before, balance_after,
                    created_at, comment, performed_by, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_id,
                    amount,
                    operation_type,
                    before,
                    after,
                    ts,
                    comment,
                    performed_by,
                    idempotency_key,
                ),
            )
            await self.conn.execute(
                """
                UPDATE users
                SET balance = ?,
                    paid_until_date = COALESCE(?, paid_until_date),
                    last_charge_date = COALESCE(?, last_charge_date),
                    updated_at = ?
                WHERE telegram_id = ?
                """,
                (after, paid_until_date, last_charge_date, ts, telegram_id),
            )
            await self.conn.commit()
            return True, before, after
        except Exception:
            await self.conn.rollback()
            raise

    async def list_operations(self, telegram_id: int | None, limit: int = 20, offset: int = 0) -> list[BalanceOp]:
        if telegram_id is None:
            rows = await self.fetchall(
                "SELECT * FROM balance_operations ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        else:
            rows = await self.fetchall(
                """
                SELECT * FROM balance_operations
                WHERE telegram_id = ?
                ORDER BY id DESC LIMIT ? OFFSET ?
                """,
                (telegram_id, limit, offset),
            )
        return [BalanceOp(**dict(r)) for r in rows]

    async def finance_totals(self, start_iso: str | None = None, end_iso: str | None = None) -> dict[str, float]:
        where = "1=1"
        params: list[Any] = []
        if start_iso:
            where += " AND created_at >= ?"
            params.append(start_iso)
        if end_iso:
            where += " AND created_at < ?"
            params.append(end_iso)
        row = await self.fetchone(
            f"""
            SELECT
              COALESCE(SUM(CASE WHEN operation_type IN ('credit', 'refund') AND amount > 0 THEN amount ELSE 0 END), 0) AS credits,
              COALESCE(SUM(CASE WHEN operation_type = 'debit' AND amount < 0 THEN -amount
                                WHEN operation_type = 'debit' AND amount > 0 THEN amount ELSE 0 END), 0) AS debits
            FROM balance_operations
            WHERE {where}
            """,
            params,
        )
        credits = float(row["credits"]) if row else 0.0
        debits = float(row["debits"]) if row else 0.0
        return {"credits": credits, "debits": debits, "income": credits}

    # --- diary helpers ---

    async def _insert(self, sql: str, params: tuple[Any, ...]) -> int:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return int(cur.lastrowid)

    async def _get(self, table: str, factory, item_id: int, telegram_id: int):
        row = await self.fetchone(
            f"SELECT * FROM {table} WHERE id = ? AND telegram_id = ?",
            (item_id, telegram_id),
        )
        return _opt(factory, row)

    async def _delete(self, table: str, item_id: int, telegram_id: int) -> bool:
        cur = await self.conn.execute(
            f"DELETE FROM {table} WHERE id = ? AND telegram_id = ?",
            (item_id, telegram_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def _list_range(self, table: str, factory, telegram_id: int, start: str, end: str, time_col: str = "occurred_at"):
        rows = await self.fetchall(
            f"""
            SELECT * FROM {table}
            WHERE telegram_id = ? AND {time_col} >= ? AND {time_col} < ?
            ORDER BY {time_col} ASC
            """,
            (telegram_id, start, end),
        )
        return [factory(**dict(r)) for r in rows]

    # cigarettes
    async def add_cigarette(self, telegram_id: int, occurred_at: str) -> int:
        return await self._insert(
            "INSERT INTO cigarettes (telegram_id, occurred_at, created_at) VALUES (?, ?, ?)",
            (telegram_id, occurred_at, to_iso(now_utc())),
        )

    async def get_cigarette(self, item_id: int, telegram_id: int) -> Cigarette | None:
        return await self._get("cigarettes", Cigarette, item_id, telegram_id)

    async def update_cigarette_time(self, item_id: int, telegram_id: int, occurred_at: str) -> None:
        await self.conn.execute(
            "UPDATE cigarettes SET occurred_at = ? WHERE id = ? AND telegram_id = ?",
            (occurred_at, item_id, telegram_id),
        )
        await self.conn.commit()

    async def delete_cigarette(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("cigarettes", item_id, telegram_id)

    async def list_cigarettes(self, telegram_id: int, start: str, end: str) -> list[Cigarette]:
        return await self._list_range("cigarettes", Cigarette, telegram_id, start, end)

    # fooling
    async def add_fooling(self, telegram_id: int, occurred_at: str) -> int:
        return await self._insert(
            "INSERT INTO fooling (telegram_id, occurred_at, created_at) VALUES (?, ?, ?)",
            (telegram_id, occurred_at, to_iso(now_utc())),
        )

    async def get_fooling(self, item_id: int, telegram_id: int) -> Fooling | None:
        return await self._get("fooling", Fooling, item_id, telegram_id)

    async def update_fooling_time(self, item_id: int, telegram_id: int, occurred_at: str) -> None:
        await self.conn.execute(
            "UPDATE fooling SET occurred_at = ? WHERE id = ? AND telegram_id = ?",
            (occurred_at, item_id, telegram_id),
        )
        await self.conn.commit()

    async def delete_fooling(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("fooling", item_id, telegram_id)

    async def list_fooling(self, telegram_id: int, start: str, end: str) -> list[Fooling]:
        return await self._list_range("fooling", Fooling, telegram_id, start, end)

    # sleep
    async def add_sleep(
        self,
        telegram_id: int,
        bedtime: str | None = None,
        wake_time: str | None = None,
        duration_minutes: int | None = None,
        quality: int | None = None,
        *,
        phone_in_bed_at: str | None = None,
        phone_away_at: str | None = None,
        sleep_onset_at: str | None = None,
        out_of_bed_at: str | None = None,
    ) -> int:
        ts = to_iso(now_utc())
        return await self._insert(
            """
            INSERT INTO sleep_records (
                telegram_id, bedtime, wake_time, duration_minutes, quality,
                phone_in_bed_at, phone_away_at, sleep_onset_at, out_of_bed_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                bedtime,
                wake_time,
                duration_minutes,
                quality,
                phone_in_bed_at,
                phone_away_at,
                sleep_onset_at,
                out_of_bed_at,
                ts,
                ts,
            ),
        )

    async def get_sleep(self, item_id: int, telegram_id: int) -> SleepRecord | None:
        return await self._get("sleep_records", SleepRecord, item_id, telegram_id)

    async def latest_sleep(self, telegram_id: int) -> SleepRecord | None:
        row = await self.fetchone(
            """
            SELECT * FROM sleep_records
            WHERE telegram_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (telegram_id,),
        )
        return _opt(SleepRecord, row)

    async def latest_open_sleep(self, telegram_id: int) -> SleepRecord | None:
        row = await self.fetchone(
            """
            SELECT * FROM sleep_records
            WHERE telegram_id = ? AND wake_time IS NULL
              AND (
                    phone_in_bed_at IS NOT NULL
                 OR phone_away_at IS NOT NULL
                 OR bedtime IS NOT NULL
              )
            ORDER BY id DESC LIMIT 1
            """,
            (telegram_id,),
        )
        return _opt(SleepRecord, row)

    async def update_sleep(
        self,
        item_id: int,
        telegram_id: int,
        **fields: Any,
    ) -> None:
        allowed = {
            "bedtime",
            "wake_time",
            "duration_minutes",
            "quality",
            "phone_in_bed_at",
            "phone_away_at",
            "sleep_onset_at",
            "out_of_bed_at",
        }
        sets = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(key)
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(to_iso(now_utc()))
        params.extend([item_id, telegram_id])
        await self.conn.execute(
            f"UPDATE sleep_records SET {', '.join(sets)} WHERE id = ? AND telegram_id = ?",
            params,
        )
        await self.conn.commit()

    async def delete_sleep(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("sleep_records", item_id, telegram_id)

    async def list_sleep(self, telegram_id: int, start: str, end: str) -> list[SleepRecord]:
        rows = await self.fetchall(
            """
            SELECT * FROM sleep_records
            WHERE telegram_id = ?
              AND (
                    (bedtime IS NOT NULL AND bedtime >= ? AND bedtime < ?)
                 OR (wake_time IS NOT NULL AND wake_time >= ? AND wake_time < ?)
                 OR (phone_in_bed_at IS NOT NULL AND phone_in_bed_at >= ? AND phone_in_bed_at < ?)
                 OR (phone_away_at IS NOT NULL AND phone_away_at >= ? AND phone_away_at < ?)
                 OR (sleep_onset_at IS NOT NULL AND sleep_onset_at >= ? AND sleep_onset_at < ?)
                 OR (out_of_bed_at IS NOT NULL AND out_of_bed_at >= ? AND out_of_bed_at < ?)
              )
            ORDER BY COALESCE(
                sleep_onset_at, bedtime, phone_in_bed_at, phone_away_at, wake_time, out_of_bed_at
            ) ASC
            """,
            (telegram_id, start, end, start, end, start, end, start, end, start, end, start, end),
        )
        return [SleepRecord(**dict(r)) for r in rows]

    # snus packs
    async def add_snus_pack(
        self,
        telegram_id: int,
        bought_at: str | None,
        finished_at: str | None,
        duration_minutes: int | None,
    ) -> int:
        ts = to_iso(now_utc())
        return await self._insert(
            """
            INSERT INTO snus_packs (
                telegram_id, bought_at, finished_at, duration_minutes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, bought_at, finished_at, duration_minutes, ts, ts),
        )

    async def get_snus_pack(self, item_id: int, telegram_id: int) -> SnusPack | None:
        return await self._get("snus_packs", SnusPack, item_id, telegram_id)

    async def oldest_open_snus(self, telegram_id: int) -> SnusPack | None:
        row = await self.fetchone(
            """
            SELECT * FROM snus_packs
            WHERE telegram_id = ? AND bought_at IS NOT NULL AND finished_at IS NULL
            ORDER BY bought_at ASC LIMIT 1
            """,
            (telegram_id,),
        )
        return _opt(SnusPack, row)

    async def count_open_snus(self, telegram_id: int) -> int:
        row = await self.fetchone(
            """
            SELECT COUNT(*) AS n FROM snus_packs
            WHERE telegram_id = ? AND bought_at IS NOT NULL AND finished_at IS NULL
            """,
            (telegram_id,),
        )
        return int(row["n"]) if row else 0

    async def update_snus_pack(self, item_id: int, telegram_id: int, **fields: Any) -> None:
        allowed = {"bought_at", "finished_at", "duration_minutes"}
        sets = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(key)
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(to_iso(now_utc()))
        params.extend([item_id, telegram_id])
        await self.conn.execute(
            f"UPDATE snus_packs SET {', '.join(sets)} WHERE id = ? AND telegram_id = ?",
            params,
        )
        await self.conn.commit()

    async def delete_snus_pack(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("snus_packs", item_id, telegram_id)

    async def list_snus_packs(self, telegram_id: int, start: str, end: str) -> list[SnusPack]:
        rows = await self.fetchall(
            """
            SELECT * FROM snus_packs
            WHERE telegram_id = ?
              AND (
                    (bought_at IS NOT NULL AND bought_at >= ? AND bought_at < ?)
                 OR (finished_at IS NOT NULL AND finished_at >= ? AND finished_at < ?)
              )
            ORDER BY COALESCE(bought_at, finished_at) ASC
            """,
            (telegram_id, start, end, start, end),
        )
        return [SnusPack(**dict(r)) for r in rows]

    # caffeine / alcohol / activity
    async def add_caffeine(self, telegram_id: int, drink_type: str, amount: float | None, unit: str | None, occurred_at: str) -> int:
        return await self._insert(
            """
            INSERT INTO caffeine_records (telegram_id, drink_type, amount, unit, occurred_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, drink_type, amount, unit, occurred_at, to_iso(now_utc())),
        )

    async def get_caffeine(self, item_id: int, telegram_id: int) -> CaffeineRecord | None:
        return await self._get("caffeine_records", CaffeineRecord, item_id, telegram_id)

    async def update_caffeine(self, item_id: int, telegram_id: int, **fields: Any) -> None:
        allowed = {"drink_type", "amount", "unit", "occurred_at", "extra_json"}
        await self._update_fields("caffeine_records", allowed, item_id, telegram_id, fields)

    async def delete_caffeine(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("caffeine_records", item_id, telegram_id)

    async def list_caffeine(self, telegram_id: int, start: str, end: str) -> list[CaffeineRecord]:
        return await self._list_range("caffeine_records", CaffeineRecord, telegram_id, start, end)

    async def add_alcohol(self, telegram_id: int, drink_type: str, amount: float | None, unit: str | None, occurred_at: str) -> int:
        return await self._insert(
            """
            INSERT INTO alcohol_records (telegram_id, drink_type, amount, unit, occurred_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, drink_type, amount, unit, occurred_at, to_iso(now_utc())),
        )

    async def get_alcohol(self, item_id: int, telegram_id: int) -> AlcoholRecord | None:
        return await self._get("alcohol_records", AlcoholRecord, item_id, telegram_id)

    async def update_alcohol(self, item_id: int, telegram_id: int, **fields: Any) -> None:
        allowed = {"drink_type", "amount", "unit", "occurred_at", "extra_json"}
        await self._update_fields("alcohol_records", allowed, item_id, telegram_id, fields)

    async def delete_alcohol(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("alcohol_records", item_id, telegram_id)

    async def list_alcohol(self, telegram_id: int, start: str, end: str) -> list[AlcoholRecord]:
        return await self._list_range("alcohol_records", AlcoholRecord, telegram_id, start, end)

    async def add_activity(
        self,
        telegram_id: int,
        activity_type: str,
        duration_minutes: int | None,
        comment: str | None,
        occurred_at: str,
    ) -> int:
        return await self._insert(
            """
            INSERT INTO activity_records (
                telegram_id, activity_type, duration_minutes, comment, occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, activity_type, duration_minutes, comment, occurred_at, to_iso(now_utc())),
        )

    async def get_activity(self, item_id: int, telegram_id: int) -> ActivityRecord | None:
        return await self._get("activity_records", ActivityRecord, item_id, telegram_id)

    async def update_activity(self, item_id: int, telegram_id: int, **fields: Any) -> None:
        allowed = {"activity_type", "duration_minutes", "comment", "occurred_at", "extra_json"}
        await self._update_fields("activity_records", allowed, item_id, telegram_id, fields)

    async def delete_activity(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("activity_records", item_id, telegram_id)

    async def list_activity(self, telegram_id: int, start: str, end: str) -> list[ActivityRecord]:
        return await self._list_range("activity_records", ActivityRecord, telegram_id, start, end)

    async def _update_fields(self, table: str, allowed: set[str], item_id: int, telegram_id: int, fields: dict[str, Any]) -> None:
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(key)
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        params.extend([item_id, telegram_id])
        await self.conn.execute(
            f"UPDATE {table} SET {', '.join(sets)} WHERE id = ? AND telegram_id = ?",
            params,
        )
        await self.conn.commit()

    # custom metrics
    async def add_metric(self, telegram_id: int, name: str, data_type: str, unit: str | None, choices: list[str] | None) -> int:
        return await self._insert(
            """
            INSERT INTO custom_metrics (telegram_id, name, data_type, unit, choices_json, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (
                telegram_id,
                name,
                data_type,
                unit,
                json.dumps(choices, ensure_ascii=False) if choices else None,
                to_iso(now_utc()),
            ),
        )

    async def get_metric(self, metric_id: int, telegram_id: int) -> CustomMetric | None:
        return await self._get("custom_metrics", CustomMetric, metric_id, telegram_id)

    async def list_metrics(self, telegram_id: int, enabled_only: bool = False) -> list[CustomMetric]:
        sql = "SELECT * FROM custom_metrics WHERE telegram_id = ?"
        params: list[Any] = [telegram_id]
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY id"
        rows = await self.fetchall(sql, params)
        return [CustomMetric(**dict(r)) for r in rows]

    async def update_metric(self, metric_id: int, telegram_id: int, **fields: Any) -> None:
        allowed = {"name", "data_type", "unit", "choices_json", "enabled"}
        await self._update_fields("custom_metrics", allowed, metric_id, telegram_id, fields)

    async def add_metric_value(
        self,
        telegram_id: int,
        metric_id: int,
        occurred_at: str,
        value_number: float | None = None,
        value_text: str | None = None,
        value_bool: int | None = None,
    ) -> int:
        metric = await self.get_metric(metric_id, telegram_id)
        if metric is None:
            raise ValueError("metric not found")
        return await self._insert(
            """
            INSERT INTO custom_metric_values (
                telegram_id, metric_id, value_number, value_text, value_bool, occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, metric_id, value_number, value_text, value_bool, occurred_at, to_iso(now_utc())),
        )

    async def get_metric_value(self, item_id: int, telegram_id: int) -> CustomValue | None:
        row = await self.fetchone(
            """
            SELECT v.*, m.name AS metric_name, m.data_type, m.unit
            FROM custom_metric_values v
            JOIN custom_metrics m ON m.id = v.metric_id AND m.telegram_id = v.telegram_id
            WHERE v.id = ? AND v.telegram_id = ?
            """,
            (item_id, telegram_id),
        )
        return _opt(CustomValue, row)

    async def delete_metric_value(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("custom_metric_values", item_id, telegram_id)

    async def list_metric_values(self, telegram_id: int, start: str, end: str, metric_id: int | None = None) -> list[CustomValue]:
        sql = """
            SELECT v.*, m.name AS metric_name, m.data_type, m.unit
            FROM custom_metric_values v
            JOIN custom_metrics m ON m.id = v.metric_id AND m.telegram_id = v.telegram_id
            WHERE v.telegram_id = ? AND v.occurred_at >= ? AND v.occurred_at < ?
        """
        params: list[Any] = [telegram_id, start, end]
        if metric_id is not None:
            sql += " AND v.metric_id = ?"
            params.append(metric_id)
        sql += " ORDER BY v.occurred_at ASC"
        rows = await self.fetchall(sql, params)
        return [CustomValue(**dict(r)) for r in rows]

    # event markers / periods
    _MARKER_SELECT = """
SELECT m.*,
       p.id AS period_id,
       CASE
         WHEN p.start_marker_id = m.id THEN 'start'
         WHEN p.end_marker_id = m.id THEN 'end'
       END AS period_role
FROM event_markers m
LEFT JOIN event_periods p
  ON p.telegram_id = m.telegram_id
 AND (p.start_marker_id = m.id OR p.end_marker_id = m.id)
"""

    _PERIOD_SELECT = """
SELECT p.id, p.telegram_id, p.start_marker_id, p.end_marker_id, p.created_at, p.updated_at,
       s.name AS start_name, s.occurred_at AS start_at, s.comment AS start_comment,
       e.name AS end_name, e.occurred_at AS end_at, e.comment AS end_comment
FROM event_periods p
JOIN event_markers s ON s.id = p.start_marker_id AND s.telegram_id = p.telegram_id
LEFT JOIN event_markers e ON e.id = p.end_marker_id AND e.telegram_id = p.telegram_id
"""

    async def add_marker(
        self,
        telegram_id: int,
        occurred_at: str,
        name: str,
        comment: str | None,
    ) -> int:
        ts = to_iso(now_utc())
        return await self._insert(
            """
            INSERT INTO event_markers (telegram_id, occurred_at, name, comment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, occurred_at, name, comment, ts, ts),
        )

    async def get_marker(self, item_id: int, telegram_id: int) -> EventMarker | None:
        row = await self.fetchone(
            self._MARKER_SELECT + " WHERE m.id = ? AND m.telegram_id = ?",
            (item_id, telegram_id),
        )
        return _opt(EventMarker, row)

    async def update_marker(self, item_id: int, telegram_id: int, **fields: Any) -> None:
        allowed = {"occurred_at", "name", "comment", "updated_at"}
        payload = dict(fields)
        payload.setdefault("updated_at", to_iso(now_utc()))
        await self._update_fields("event_markers", allowed, item_id, telegram_id, payload)

    async def delete_marker(self, item_id: int, telegram_id: int) -> bool:
        return await self._delete("event_markers", item_id, telegram_id)

    async def list_markers(self, telegram_id: int, start: str, end: str) -> list[EventMarker]:
        rows = await self.fetchall(
            self._MARKER_SELECT
            + " WHERE m.telegram_id = ? AND m.occurred_at >= ? AND m.occurred_at < ?"
            + " ORDER BY m.occurred_at ASC",
            (telegram_id, start, end),
        )
        return [EventMarker(**dict(r)) for r in rows]

    async def list_recent_markers(self, telegram_id: int, limit: int = 20) -> list[EventMarker]:
        rows = await self.fetchall(
            self._MARKER_SELECT
            + " WHERE m.telegram_id = ? ORDER BY m.occurred_at DESC LIMIT ?",
            (telegram_id, limit),
        )
        return [EventMarker(**dict(r)) for r in rows]

    async def list_unlinked_markers(self, telegram_id: int, limit: int = 30) -> list[EventMarker]:
        rows = await self.fetchall(
            self._MARKER_SELECT
            + " WHERE m.telegram_id = ? AND p.id IS NULL ORDER BY m.occurred_at DESC LIMIT ?",
            (telegram_id, limit),
        )
        return [EventMarker(**dict(r)) for r in rows]

    async def add_period(
        self,
        telegram_id: int,
        start_marker_id: int,
        end_marker_id: int | None,
    ) -> int:
        ts = to_iso(now_utc())
        return await self._insert(
            """
            INSERT INTO event_periods (
                telegram_id, start_marker_id, end_marker_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_id, start_marker_id, end_marker_id, ts, ts),
        )

    async def get_period(self, item_id: int, telegram_id: int) -> EventPeriod | None:
        row = await self.fetchone(
            self._PERIOD_SELECT + " WHERE p.id = ? AND p.telegram_id = ?",
            (item_id, telegram_id),
        )
        return _opt(EventPeriod, row)

    async def get_period_for_marker(self, marker_id: int, telegram_id: int) -> EventPeriod | None:
        row = await self.fetchone(
            self._PERIOD_SELECT
            + " WHERE p.telegram_id = ? AND (p.start_marker_id = ? OR p.end_marker_id = ?)",
            (telegram_id, marker_id, marker_id),
        )
        return _opt(EventPeriod, row)

    async def set_period_end(self, period_id: int, telegram_id: int, end_marker_id: int) -> None:
        await self.conn.execute(
            """
            UPDATE event_periods
            SET end_marker_id = ?, updated_at = ?
            WHERE id = ? AND telegram_id = ?
            """,
            (end_marker_id, to_iso(now_utc()), period_id, telegram_id),
        )
        await self.conn.commit()

    async def unlink_period(self, period_id: int, telegram_id: int) -> bool:
        return await self._delete("event_periods", period_id, telegram_id)

    async def list_open_periods(self, telegram_id: int) -> list[EventPeriod]:
        rows = await self.fetchall(
            self._PERIOD_SELECT
            + " WHERE p.telegram_id = ? AND p.end_marker_id IS NULL"
            + " ORDER BY s.occurred_at DESC",
            (telegram_id,),
        )
        return [EventPeriod(**dict(r)) for r in rows]

    async def list_periods_overlapping(
        self, telegram_id: int, start: str, end: str
    ) -> list[EventPeriod]:
        rows = await self.fetchall(
            self._PERIOD_SELECT
            + """
            WHERE p.telegram_id = ?
              AND s.occurred_at < ?
              AND (e.occurred_at IS NULL OR e.occurred_at >= ?)
            ORDER BY s.occurred_at ASC
            """,
            (telegram_id, end, start),
        )
        return [EventPeriod(**dict(r)) for r in rows]

    # callbacks / counts
    async def claim_callback(self, callback_id: str, telegram_id: int) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO processed_callbacks (callback_id, telegram_id, processed_at) VALUES (?, ?, ?)",
                (callback_id, telegram_id, to_iso(now_utc())),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            await self.conn.rollback()
            return False

    async def cleanup_callbacks(self, older_than_iso: str) -> None:
        await self.conn.execute(
            "DELETE FROM processed_callbacks WHERE processed_at < ?",
            (older_than_iso,),
        )
        await self.conn.commit()

    async def count_user_entries(self, telegram_id: int) -> int:
        total = 0
        for table, _col in _DIARY_COUNT_SPECS:
            row = await self.fetchone(
                f"SELECT COUNT(*) AS c FROM {table} WHERE telegram_id = ?",
                (telegram_id,),
            )
            total += int(row["c"]) if row else 0
        return total

    async def count_user_entries_between(self, telegram_id: int, start: str, end: str) -> int:
        total = 0
        for table, col in _DIARY_COUNT_SPECS:
            row = await self.fetchone(
                f"SELECT COUNT(*) AS c FROM {table} WHERE telegram_id = ? AND {col} >= ? AND {col} < ?",
                (telegram_id, start, end),
            )
            total += int(row["c"]) if row else 0
        return total

    async def last_entry_at(self, telegram_id: int) -> str | None:
        parts = [
            "SELECT occurred_at AS ts FROM cigarettes WHERE telegram_id = ?",
            "SELECT occurred_at AS ts FROM fooling WHERE telegram_id = ?",
            "SELECT COALESCE(out_of_bed_at, wake_time, sleep_onset_at, phone_away_at, phone_in_bed_at, bedtime) AS ts FROM sleep_records WHERE telegram_id = ?",
            "SELECT occurred_at AS ts FROM caffeine_records WHERE telegram_id = ?",
            "SELECT occurred_at AS ts FROM alcohol_records WHERE telegram_id = ?",
            "SELECT occurred_at AS ts FROM activity_records WHERE telegram_id = ?",
            "SELECT occurred_at AS ts FROM custom_metric_values WHERE telegram_id = ?",
            "SELECT occurred_at AS ts FROM event_markers WHERE telegram_id = ?",
        ]
        sql = " UNION ALL ".join(parts)
        row = await self.fetchone(
            f"SELECT MAX(ts) AS ts FROM ({sql})",
            tuple([telegram_id] * 8),
        )
        return row["ts"] if row and row["ts"] else None

    async def count_entries_between(self, start: str, end: str) -> int:
        total = 0
        for table, col in _DIARY_COUNT_SPECS:
            row = await self.fetchone(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {col} >= ? AND {col} < ?",
                (start, end),
            )
            total += int(row["c"]) if row else 0
        return total

    # --- vpn latency ---

    async def insert_vpn_sample(
        self,
        measured_at: str,
        ok: bool,
        latency_ms: int | None,
        node_name: str | None,
        subscription: str | None,
        error: str | None,
        host_uptime_s: float | None = None,
    ) -> int:
        cur = await self.conn.execute(
            """
            INSERT INTO vpn_latency_samples
                (measured_at, ok, latency_ms, node_name, subscription, error, host_uptime_s)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (measured_at, 1 if ok else 0, latency_ms, node_name, subscription, error, host_uptime_s),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def list_vpn_samples(self, start: str, end: str) -> list[VpnLatencySample]:
        rows = await self.fetchall(
            f"""
            SELECT {_VPN_SAMPLE_COLS}
            FROM vpn_latency_samples
            WHERE measured_at >= ? AND measured_at < ?
            ORDER BY measured_at ASC, id ASC
            """,
            (start, end),
        )
        return [VpnLatencySample(**dict(row)) for row in rows]

    async def list_vpn_heartbeats(self, start: str, end: str) -> list[tuple[str, float | None]]:
        rows = await self.fetchall(
            """
            SELECT measured_at, host_uptime_s
            FROM vpn_latency_samples
            WHERE measured_at >= ? AND measured_at < ?
            ORDER BY measured_at ASC, id ASC
            """,
            (start, end),
        )
        out: list[tuple[str, float | None]] = []
        for row in rows:
            raw = row["host_uptime_s"]
            host_up = float(raw) if raw is not None else None
            out.append((str(row["measured_at"]), host_up))
        return out

    async def earliest_vpn_measured_at(self) -> str | None:
        row = await self.fetchone("SELECT MIN(measured_at) AS ts FROM vpn_latency_samples")
        return str(row["ts"]) if row and row["ts"] else None

    async def latest_vpn_sample(self) -> VpnLatencySample | None:
        row = await self.fetchone(
            f"""
            SELECT {_VPN_SAMPLE_COLS}
            FROM vpn_latency_samples
            ORDER BY measured_at DESC, id DESC
            LIMIT 1
            """
        )
        return VpnLatencySample(**dict(row)) if row else None

    async def vpn_latency_summary(self, start: str, end: str) -> dict[str, Any]:
        row = await self.fetchone(
            f"""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END), 0) AS ok_count,
                COALESCE(SUM(CASE WHEN latency_ms IS NOT NULL THEN 1 ELSE 0 END), 0) AS measured,
                AVG(latency_ms) AS avg_ms,
                MIN(latency_ms) AS min_ms,
                MAX(latency_ms) AS max_ms,
                COALESCE(SUM(CASE WHEN {_VPN_OK_PING_SQL} AND latency_ms < 100 THEN 1 ELSE 0 END), 0) AS bucket_0_100,
                COALESCE(SUM(CASE WHEN {_VPN_OK_PING_SQL} AND latency_ms >= 100 AND latency_ms < 500 THEN 1 ELSE 0 END), 0) AS bucket_100_500,
                COALESCE(SUM(CASE WHEN {_VPN_OK_PING_SQL} AND latency_ms >= 500 AND latency_ms < 1000 THEN 1 ELSE 0 END), 0) AS bucket_500_1000,
                COALESCE(SUM(CASE WHEN {_VPN_OK_PING_SQL} AND latency_ms >= 1000 THEN 1 ELSE 0 END), 0) AS bucket_1000,
                COALESCE(SUM(CASE WHEN NOT ({_VPN_OK_PING_SQL}) THEN 1 ELSE 0 END), 0) AS no_ping
            FROM vpn_latency_samples
            WHERE measured_at >= ? AND measured_at < ?
            """,
            (start, end),
        )
        total = int(row["total"]) if row else 0
        ok_count = int(row["ok_count"]) if row else 0
        measured = int(row["measured"]) if row else 0
        p95_ms = None
        p99_ms = None
        p99_9_ms = None
        p95_count = 0
        p99_count = 0
        p99_9_count = 0
        if measured > 0:
            p95_row = await self.fetchone(
                """
                SELECT latency_ms FROM vpn_latency_samples
                WHERE latency_ms IS NOT NULL
                  AND measured_at >= ? AND measured_at < ?
                ORDER BY latency_ms
                LIMIT 1 OFFSET (
                    SELECT CAST((COUNT(*) - 1) * 0.95 AS INTEGER)
                    FROM vpn_latency_samples
                    WHERE latency_ms IS NOT NULL
                      AND measured_at >= ? AND measured_at < ?
                )
                """,
                (start, end, start, end),
            )
            p95_ms = p95_row["latency_ms"] if p95_row else None
            p99_ms = await self._vpn_latency_percentile(start, end, 0.99, measured)
            p99_9_ms = await self._vpn_latency_percentile(start, end, 0.999, measured)
            counts = await self.fetchone(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN latency_ms >= ? THEN 1 ELSE 0 END), 0) AS p95_count,
                    COALESCE(SUM(CASE WHEN latency_ms >= ? THEN 1 ELSE 0 END), 0) AS p99_count,
                    COALESCE(SUM(CASE WHEN latency_ms >= ? THEN 1 ELSE 0 END), 0) AS p99_9_count
                FROM vpn_latency_samples
                WHERE latency_ms IS NOT NULL
                  AND measured_at >= ? AND measured_at < ?
                """,
                (p95_ms, p99_ms, p99_9_ms, start, end),
            )
            if counts:
                p95_count = int(counts["p95_count"])
                p99_count = int(counts["p99_count"])
                p99_9_count = int(counts["p99_9_count"])
        return {
            "total": total,
            "ok_count": ok_count,
            "fail_count": total - ok_count,
            "measured": measured,
            "avg_ms": row["avg_ms"] if row else None,
            "min_ms": row["min_ms"] if row else None,
            "max_ms": row["max_ms"] if row else None,
            "p95_ms": p95_ms,
            "p99_ms": p99_ms,
            "p99_9_ms": p99_9_ms,
            "p95_count": p95_count,
            "p99_count": p99_count,
            "p99_9_count": p99_9_count,
            "bucket_0_100": int(row["bucket_0_100"]) if row else 0,
            "bucket_100_500": int(row["bucket_100_500"]) if row else 0,
            "bucket_500_1000": int(row["bucket_500_1000"]) if row else 0,
            "bucket_1000": int(row["bucket_1000"]) if row else 0,
            "no_ping": int(row["no_ping"]) if row else 0,
        }

    async def _vpn_latency_percentile(
        self, start: str, end: str, p: float, measured: int
    ) -> int | None:
        offset = min(measured - 1, max(0, math.ceil(p * measured) - 1))
        row = await self.fetchone(
            """
            SELECT latency_ms FROM vpn_latency_samples
            WHERE latency_ms IS NOT NULL
              AND measured_at >= ? AND measured_at < ?
            ORDER BY latency_ms
            LIMIT 1 OFFSET ?
            """,
            (start, end, offset),
        )
        return row["latency_ms"] if row else None

    async def vpn_top_nodes(self, start: str, end: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT
                node_name,
                subscription,
                COUNT(*) AS samples,
                AVG(CASE WHEN ok = 1 THEN latency_ms END) AS avg_ms,
                MIN(CASE WHEN ok = 1 THEN latency_ms END) AS min_ms,
                MAX(CASE WHEN ok = 1 THEN latency_ms END) AS max_ms,
                SUM(CASE WHEN ok = 1 THEN 0 ELSE 1 END) AS fail_count
            FROM vpn_latency_samples
            WHERE measured_at >= ? AND measured_at < ?
            GROUP BY node_name, subscription
            ORDER BY samples DESC
            LIMIT ?
            """,
            (start, end, limit),
        )
        result = [dict(r) for r in rows]
        if not result:
            return result

        clauses: list[str] = []
        params: list[Any] = [start, end]
        for item in result:
            clauses.append("(node_name IS ? AND subscription IS ?)")
            params.extend([item["node_name"], item["subscription"]])
        lat_rows = await self.fetchall(
            f"""
            SELECT node_name, subscription, latency_ms
            FROM vpn_latency_samples
            WHERE measured_at >= ? AND measured_at < ?
              AND ok = 1 AND latency_ms IS NOT NULL
              AND ({" OR ".join(clauses)})
            """,
            params,
        )
        grouped: dict[tuple[Any, Any], list[int]] = defaultdict(list)
        for sample in lat_rows:
            grouped[(sample["node_name"], sample["subscription"])].append(int(sample["latency_ms"]))
        _apply_vpn_tail_stats(result, grouped, lambda item: (item["node_name"], item["subscription"]))
        return result

    async def vpn_top_subscriptions(self, start: str, end: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT
                subscription,
                COUNT(*) AS samples,
                AVG(CASE WHEN ok = 1 THEN latency_ms END) AS avg_ms,
                MIN(CASE WHEN ok = 1 THEN latency_ms END) AS min_ms,
                MAX(CASE WHEN ok = 1 THEN latency_ms END) AS max_ms,
                SUM(CASE WHEN ok = 1 THEN 0 ELSE 1 END) AS fail_count
            FROM vpn_latency_samples
            WHERE measured_at >= ? AND measured_at < ?
            GROUP BY subscription
            ORDER BY samples DESC
            LIMIT ?
            """,
            (start, end, limit),
        )
        result = [dict(r) for r in rows]
        if not result:
            return result

        clauses = ["(subscription IS ?)"] * len(result)
        params: list[Any] = [start, end, *[item["subscription"] for item in result]]
        lat_rows = await self.fetchall(
            f"""
            SELECT subscription, latency_ms
            FROM vpn_latency_samples
            WHERE measured_at >= ? AND measured_at < ?
              AND ok = 1 AND latency_ms IS NOT NULL
              AND ({" OR ".join(clauses)})
            """,
            params,
        )
        grouped: dict[Any, list[int]] = defaultdict(list)
        for sample in lat_rows:
            grouped[sample["subscription"]].append(int(sample["latency_ms"]))
        _apply_vpn_tail_stats(result, grouped, lambda item: item["subscription"])
        return result

    # --- admin database editor ---

    async def list_table_names(self) -> list[str]:
        rows = await self.fetchall(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        return [str(row["name"]) for row in rows]

    async def list_tables_with_counts(self) -> list[tuple[str, int]]:
        result: list[tuple[str, int]] = []
        for name in await self.list_table_names():
            quoted = quote_ident(name)
            row = await self.fetchone(f"SELECT COUNT(*) AS c FROM {quoted}")
            result.append((name, int(row["c"]) if row else 0))
        return result

    async def _require_table(self, name: str) -> str:
        quoted = quote_ident(name)
        row = await self.fetchone(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        )
        if row is None:
            raise SqlError("Таблица не найдена")
        return quoted

    async def table_schema(self, name: str) -> list[dict[str, Any]]:
        quoted = await self._require_table(name)
        rows = await self.fetchall(f"PRAGMA table_info({quoted})")
        return [dict(row) for row in rows]

    async def table_indexes(self, name: str) -> list[str]:
        await self._require_table(name)
        rows = await self.fetchall(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
            (name,),
        )
        return [str(row["sql"]) for row in rows]

    async def schema_dump(self) -> str:
        rows = await self.fetchall(
            """
            SELECT type, name, sql FROM sqlite_master
            WHERE sql IS NOT NULL
            ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name
            """
        )
        parts = [str(row["sql"]).rstrip() + ";" for row in rows if row["sql"]]
        return "\n\n".join(parts) + ("\n" if parts else "")

    async def table_page(
        self, name: str, offset: int = 0, limit: int = 10
    ) -> dict[str, Any]:
        quoted = await self._require_table(name)
        count_row = await self.fetchone(f"SELECT COUNT(*) AS c FROM {quoted}")
        total = int(count_row["c"]) if count_row else 0
        offset = max(0, int(offset))
        limit = max(1, int(limit))
        async with self.conn.execute(
            f"SELECT * FROM {quoted} ORDER BY rowid LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            columns = [item[0] for item in (cur.description or [])]
            rows = [tuple(row) for row in await cur.fetchall()]
        return {"name": name, "columns": columns, "rows": rows, "total": total, "offset": offset}

    async def table_export(self, name: str, limit: int = 5000) -> dict[str, Any]:
        quoted = await self._require_table(name)
        count_row = await self.fetchone(f"SELECT COUNT(*) AS c FROM {quoted}")
        total = int(count_row["c"]) if count_row else 0
        async with self.conn.execute(
            f"SELECT * FROM {quoted} ORDER BY rowid LIMIT ?",
            (max(1, int(limit)),),
        ) as cur:
            columns = [item[0] for item in (cur.description or [])]
            rows = [tuple(row) for row in await cur.fetchall()]
        return {"name": name, "columns": columns, "rows": rows, "total": total}

    async def integrity_report(self) -> str:
        rows = await self.fetchall("PRAGMA integrity_check")
        if not rows:
            return "нет ответа"
        return "\n".join(str(row[0]) for row in rows)

    async def run_sql(self, sql: str, *, max_rows: int = 200) -> dict[str, Any]:
        sql = sql.strip()
        if sql.endswith(";"):
            sql = sql[:-1].rstrip()
        keyword = assert_sql_allowed(sql)
        try:
            async with self.conn.execute(sql) as cur:
                description = cur.description
                if description is not None:
                    columns = [item[0] for item in description]
                    fetched = await cur.fetchmany(max_rows + 1)
                    truncated = len(fetched) > max_rows
                    rows = [tuple(row) for row in fetched[:max_rows]]
                    rowcount = cur.rowcount
                    await self.conn.commit()
                    return {
                        "keyword": keyword,
                        "columns": columns,
                        "rows": rows,
                        "rowcount": rowcount,
                        "truncated": truncated,
                    }
                rowcount = cur.rowcount
            await self.conn.commit()
            return {
                "keyword": keyword,
                "columns": [],
                "rows": [],
                "rowcount": rowcount,
                "truncated": False,
            }
        except Exception:
            await self.conn.rollback()
            raise

    async def purge_content(self, owner_id: int) -> dict[str, int]:
        tables = await self.list_table_names()
        deleted: dict[str, int] = {}
        wiped: list[str] = []
        await self.conn.commit()
        await self.conn.execute("PRAGMA foreign_keys=OFF")
        try:
            await self.conn.execute("BEGIN IMMEDIATE")
            for name in tables:
                if name in KEEP_TABLES:
                    continue
                quoted = quote_ident(name)
                if name in OWNER_SCOPED_TABLES:
                    cur = await self.conn.execute(
                        f"DELETE FROM {quoted} WHERE telegram_id != ?",
                        (owner_id,),
                    )
                else:
                    cur = await self.conn.execute(f"DELETE FROM {quoted}")
                    wiped.append(name)
                deleted[name] = int(cur.rowcount or 0)
            seq = await self.fetchone(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
            )
            if seq is not None:
                for name in wiped:
                    await self.conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (name,))
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
        finally:
            await self.conn.execute("PRAGMA foreign_keys=ON")
        return deleted

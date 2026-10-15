from __future__ import annotations

import sqlite3
from dataclasses import replace

from database.clicks_database import ClicksDatabase
from database.database import Database
from database.file_cap import on_disk_bytes
from database.queries import Repo
from tests.conftest import make_config


async def _callbacks(db: ClicksDatabase) -> list[str]:
    async with db.conn.execute(
        "SELECT callback_data FROM button_clicks ORDER BY id ASC"
    ) as cur:
        rows = await cur.fetchall()
    return [str(row["callback_data"]) for row in rows]


async def test_new_clicks_db_is_incremental_and_page_capped(tmp_path):
    config = make_config(tmp_path)
    db = ClicksDatabase(config)
    await db.initialize()
    try:
        async with db.conn.execute("PRAGMA auto_vacuum") as cur:
            assert int((await cur.fetchone())[0]) == 2
        async with db.conn.execute("PRAGMA journal_mode") as cur:
            assert str((await cur.fetchone())[0]).lower() == "wal"
        async with db.conn.execute("PRAGMA page_size") as cur:
            page = int((await cur.fetchone())[0])
        async with db.conn.execute("PRAGMA max_page_count") as cur:
            pages = int((await cur.fetchone())[0])
        assert pages == config.telemetry_db_max_bytes // page
    finally:
        await db.close()


async def test_clicks_cap_drops_oldest_and_shrinks_the_file(tmp_path):
    db = ClicksDatabase(make_config(tmp_path))
    await db.initialize()
    try:
        blob = "x" * 4000
        for i in range(120):
            await db.record(
                telegram_id=1,
                clicked_at=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
                button_kind="k",
                callback_data=f"cb-{i:04d}",
                button_text=blob,
                is_owner=False,
            )
        before = await _callbacks(db)
        assert before[0] == "cb-0000"
        assert before[-1] == "cb-0119"
        max_bytes = 256 * 1024
        deleted = await db.enforce_size_cap(max_bytes=max_bytes)
        left = await _callbacks(db)
        assert deleted > 0
        assert left
        assert left[-1] == "cb-0119"
        assert left[0] != "cb-0000"
        assert on_disk_bytes(db.path) <= max_bytes
    finally:
        await db.close()


async def test_clicks_under_the_cap_are_kept(tmp_path):
    db = ClicksDatabase(make_config(tmp_path))
    await db.initialize()
    try:
        await db.record(
            telegram_id=1,
            clicked_at="2026-01-01T00:00:00+00:00",
            button_kind="k",
            callback_data="keep",
            button_text="ok",
            is_owner=False,
        )
        assert await db.enforce_size_cap() == 0
        assert await _callbacks(db) == ["keep"]
    finally:
        await db.close()


async def test_disabled_cap_does_not_delete_or_limit_pages(tmp_path):
    db = ClicksDatabase(replace(make_config(tmp_path), telemetry_db_max_bytes=0))
    await db.initialize()
    try:
        for i in range(3):
            await db.record(
                telegram_id=1,
                clicked_at=f"2026-01-01T00:00:0{i}+00:00",
                button_kind="k",
                callback_data=f"c{i}",
                button_text=None,
                is_owner=True,
            )
        assert await db.enforce_size_cap() == 0
        assert await _callbacks(db) == ["c0", "c1", "c2"]
        async with db.conn.execute("PRAGMA max_page_count") as cur:
            pages = int((await cur.fetchone())[0])
        assert pages >= 1_000_000_000
    finally:
        await db.close()


async def test_legacy_clicks_file_converts_to_incremental_vacuum(tmp_path):
    path = tmp_path / "clicks.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE button_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            clicked_at TEXT NOT NULL,
            button_kind TEXT NOT NULL,
            callback_data TEXT NOT NULL,
            button_text TEXT,
            is_owner INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO button_clicks
            (telegram_id, clicked_at, button_kind, callback_data, is_owner)
        VALUES (1, '2020-01-01T00:00:00+00:00', 'k', 'old', 0)
        """
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    db = ClicksDatabase(make_config(tmp_path))
    await db.initialize()
    try:
        async with db.conn.execute("PRAGMA auto_vacuum") as cur:
            assert int((await cur.fetchone())[0]) == 2
        assert await _callbacks(db) == ["old"]
    finally:
        await db.close()


async def test_click_writes_stay_within_a_small_cap(tmp_path):
    max_bytes = 192 * 1024
    db = ClicksDatabase(replace(make_config(tmp_path), telemetry_db_max_bytes=max_bytes))
    await db.initialize()
    try:
        blob = "y" * 20_000
        for i in range(25):
            await db.record(
                telegram_id=7,
                clicked_at=f"2026-02-01T00:00:{i:02d}+00:00",
                button_kind="k",
                callback_data=f"live-{i:04d}",
                button_text=blob,
                is_owner=False,
            )
        left = await _callbacks(db)
        assert left[-1] == "live-0024"
        assert "live-0000" not in left
        assert len(left) < 25
        assert on_disk_bytes(db.path) <= max_bytes
    finally:
        await db.close()


async def test_vpn_cap_drops_oldest_samples_and_shrinks(tmp_path):
    database = Database(make_config(tmp_path))
    await database.initialize()
    repo = Repo(database)
    try:
        for i in range(120):
            await repo.insert_vpn_sample(
                f"2026-03-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
                i % 2 == 0,
                15,
                f"node-{i:04d}",
                "sub",
                "e" * 4000,
            )
        vpn = database.vpn_db
        assert vpn is not None
        max_bytes = 256 * 1024
        deleted = await vpn.enforce_size_cap(max_bytes=max_bytes)
        rows = await repo.list_vpn_samples(
            "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"
        )
        names = [row.node_name for row in rows]
        assert deleted > 0
        assert names[-1] == "node-0119"
        assert names[0] != "node-0000"
        assert on_disk_bytes(vpn.path) <= max_bytes
    finally:
        await database.close()


async def test_vpn_writes_stay_within_a_small_cap(tmp_path):
    max_bytes = 192 * 1024
    database = Database(replace(make_config(tmp_path), telemetry_db_max_bytes=max_bytes))
    await database.initialize()
    repo = Repo(database)
    try:
        for i in range(25):
            await repo.insert_vpn_sample(
                f"2026-04-01T00:00:{i:02d}+00:00",
                False,
                None,
                f"n-{i:04d}",
                "sub",
                "z" * 20_000,
            )
        vpn = database.vpn_db
        assert vpn is not None
        rows = await repo.list_vpn_samples(
            "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"
        )
        names = [row.node_name for row in rows]
        assert names[-1] == "n-0024"
        assert "n-0000" not in names
        assert len(names) < 25
        assert on_disk_bytes(vpn.path) <= max_bytes
    finally:
        await database.close()

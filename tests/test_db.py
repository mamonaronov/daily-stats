from __future__ import annotations

from datetime import date

import pytest

from database.database import Database
from tests.conftest import make_config
from services.users import can_write
from utils.time import to_iso, now_utc


@pytest.mark.asyncio
async def test_migration_sets_user_version(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    version = await db.user_version()
    await db.close()
    assert version == config.required_db_version == 3


@pytest.mark.asyncio
async def test_wal_mode(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    async with db.conn.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
    await db.close()
    assert row[0].lower() == "wal"


@pytest.mark.asyncio
async def test_backup_and_integrity(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    path = await db.backup(prefix="test")
    assert path.exists()
    assert await db.integrity_ok()
    await db.close()


@pytest.mark.asyncio
async def test_user_isolation(repo):
    a = await repo.create_user(1, "a", "A", None, "UTC", 10, "23:00")
    b = await repo.create_user(2, "b", "B", None, "UTC", 10, "23:00")
    await repo.add_cigarette(a.telegram_id, to_iso(now_utc()))
    cigs_a = await repo.list_cigarettes(a.telegram_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    cigs_b = await repo.list_cigarettes(b.telegram_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    assert len(cigs_a) == 1
    assert len(cigs_b) == 0
    stolen = await repo.get_cigarette(cigs_a[0].id, b.telegram_id)
    assert stolen is None


@pytest.mark.asyncio
async def test_snus_pack_lifetime_and_isolation(repo):
    from datetime import datetime, timezone

    from services.entries import add_snus_bought, add_snus_finished

    a = await repo.create_user(11, "a", "A", None, "UTC", 0, "23:00")
    b = await repo.create_user(12, "b", "B", None, "UTC", 0, "23:00")
    bought = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    item_id, error = await add_snus_bought(repo, a, bought)
    assert error is None and item_id is not None
    _, error = await add_snus_finished(repo, a, finished)
    assert error is None
    pack = await repo.get_snus_pack(item_id, a.telegram_id)
    assert pack is not None
    assert pack.duration_minutes == 3 * 24 * 60
    assert await repo.get_snus_pack(item_id, b.telegram_id) is None
    packs_b = await repo.list_snus_packs(b.telegram_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    assert packs_b == []


@pytest.mark.asyncio
async def test_snus_finish_requires_open_pack(repo):
    from datetime import datetime, timezone

    from services.entries import add_snus_finished

    user = await repo.create_user(13, "c", "C", None, "UTC", 0, "23:00")
    _, error = await add_snus_finished(repo, user, datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc))
    assert error is not None
    assert "открытой" in error.lower()


@pytest.mark.asyncio
async def test_snus_finish_rejects_time_before_buy(repo):
    from datetime import datetime, timezone

    from services.entries import add_snus_bought, add_snus_finished

    user = await repo.create_user(14, "d", "D", None, "UTC", 0, "23:00")
    await add_snus_bought(repo, user, datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc))
    _, error = await add_snus_finished(repo, user, datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc))
    assert error is not None
    assert "раньше" in error.lower()
    assert await repo.count_open_snus(user.telegram_id) == 1


@pytest.mark.asyncio
async def test_snus_finish_closes_oldest_open(repo):
    from datetime import datetime, timezone

    from services.entries import add_snus_bought, add_snus_finished

    user = await repo.create_user(15, "e", "E", None, "UTC", 0, "23:00")
    first, _ = await add_snus_bought(repo, user, datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc))
    second, _ = await add_snus_bought(repo, user, datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc))
    closed_id, error = await add_snus_finished(repo, user, datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc))
    assert error is None
    assert closed_id == first
    first_pack = await repo.get_snus_pack(first, user.telegram_id)
    second_pack = await repo.get_snus_pack(second, user.telegram_id)
    assert first_pack.finished_at is not None
    assert second_pack.finished_at is None


@pytest.mark.asyncio
async def test_can_write_paid_until(repo):
    user = await repo.create_user(9, "x", "X", None, "UTC", 10, "23:00")
    assert can_write(user, date(2026, 8, 17)) is False
    await repo.apply_balance_change(9, "credit", delta=10, comment="pay", performed_by=1)
    user = await repo.get_user(9)
    assert can_write(user, date(2026, 8, 17)) is True
    await repo.apply_balance_change(
        9,
        "debit",
        delta=-10,
        comment="day",
        performed_by=0,
        paid_until_date="2026-08-17",
        last_charge_date="2026-08-17",
    )
    user = await repo.get_user(9)
    assert user.balance == 0
    assert can_write(user, date(2026, 8, 17)) is True
    assert can_write(user, date(2026, 8, 18)) is False


@pytest.mark.asyncio
async def test_sleep_wake_can_be_logged_later(repo):
    from datetime import datetime, timezone

    from services.entries import add_sleep_bed, add_sleep_wake

    user = await repo.create_user(16, "s", "S", None, "UTC", 0, "23:00")
    bed = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)  # 23:00 MSK-ish; UTC 20:00
    wake = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)  # logged hours after waking
    _, error = await add_sleep_bed(repo, user, bed)
    assert error is None
    item_id, error = await add_sleep_wake(repo, user, wake, quality=4)
    assert error is None and item_id is not None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    assert rec.wake_time is not None
    assert rec.quality == 4
    assert rec.duration_minutes == 8 * 60

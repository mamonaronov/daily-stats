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
    assert version == config.required_db_version == 12


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
async def test_backup_does_not_use_main_connection(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()

    async def boom(*_args, **_kwargs):
        raise AssertionError("backup must not run on the request connection")

    db.conn.backup = boom  # type: ignore[method-assign]
    path = await db.backup(prefix="side")
    assert path.exists()
    async with db.conn.execute("SELECT 1") as cur:
        row = await cur.fetchone()
    await db.close()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_backup_rotation_skips_pending_restore(tmp_path):
    from database.database import list_sqlite_backups

    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    pending = config.backup_path / "pending-restore.sqlite3"
    pending.write_bytes(b"sqlite-placeholder")
    try:
        for index in range(config.backup_keep + 2):
            await db.backup(prefix=f"t{index}")
        assert pending.exists()
        names = [path.name for path in list_sqlite_backups(config.backup_path)]
        assert "pending-restore.sqlite3" not in names
        assert len(names) == config.backup_keep
    finally:
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
async def test_fooling_isolation(repo):
    a = await repo.create_user(3, "a", "A", None, "UTC", 10, "23:00")
    b = await repo.create_user(4, "b", "B", None, "UTC", 10, "23:00")
    await repo.add_fooling(a.telegram_id, to_iso(now_utc()))
    items_a = await repo.list_fooling(a.telegram_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    items_b = await repo.list_fooling(b.telegram_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    assert len(items_a) == 1
    assert len(items_b) == 0
    stolen = await repo.get_fooling(items_a[0].id, b.telegram_id)
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

    from services.entries import add_sleep_onset, add_sleep_phone_away, add_sleep_wake

    user = await repo.create_user(16, "s", "S", None, "UTC", 0, "23:00")
    bed = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    onset = datetime(2026, 8, 16, 20, 30, tzinfo=timezone.utc)
    wake = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)
    _, error = await add_sleep_phone_away(repo, user, bed)
    assert error is None
    item_id, error = await add_sleep_wake(repo, user, wake, quality=4)
    assert error is None and item_id is not None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    assert rec.wake_time is not None
    assert rec.quality == 4
    assert rec.duration_minutes is None
    from services.entries import add_sleep_up

    up = datetime(2026, 8, 17, 4, 10, tzinfo=timezone.utc)
    _, error = await add_sleep_up(repo, user, up)
    assert error is None
    _, error = await add_sleep_onset(repo, user, onset)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.duration_minutes == 7 * 60 + 30


@pytest.mark.asyncio
async def test_admin_sql_and_table_browser(repo):
    from database.queries import SqlError

    user = await repo.create_user(21, "owner", "Owner", None, "UTC", 10, "23:00")
    await repo.add_cigarette(user.telegram_id, to_iso(now_utc()))
    names = await repo.list_table_names()
    assert "users" in names
    assert "cigarettes" in names
    tables = dict(await repo.list_tables_with_counts())
    assert tables["cigarettes"] == 1
    schema = await repo.table_schema("users")
    assert any(col["name"] == "telegram_id" and col["pk"] for col in schema)
    page = await repo.table_page("cigarettes", 0, 10)
    assert page["total"] == 1
    assert page["columns"][0] == "id"
    result = await repo.run_sql("SELECT telegram_id, username FROM users ORDER BY telegram_id")
    assert result["keyword"] == "SELECT"
    assert result["rows"][0][0] == 21
    inserted = await repo.run_sql("INSERT INTO cigarettes (telegram_id, occurred_at, created_at) VALUES (21, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
    assert inserted["keyword"] == "INSERT"
    assert inserted["rowcount"] == 1
    with pytest.raises(SqlError, match="ATTACH"):
        await repo.run_sql("ATTACH DATABASE ':memory:' AS other")
    with pytest.raises(SqlError, match="не найдена"):
        await repo.table_page("not_a_table")
    with pytest.raises(SqlError, match="Некорректное"):
        await repo.table_page("users; DROP TABLE users")
    report = await repo.integrity_report()
    assert "ok" in report.lower()
    dump = await repo.schema_dump()
    assert "CREATE TABLE" in dump


@pytest.mark.asyncio
async def test_purge_content_keeps_only_bot_runtime_rows(repo):
    owner = await repo.create_user(1, "owner", "Owner", None, "UTC", 15.5, "22:30")
    other = await repo.create_user(22, "other", "Other", None, "UTC", 10, "23:00")
    await repo.apply_balance_change(owner.telegram_id, "credit", delta=100, comment="pay", performed_by=1)
    await repo.add_cigarette(owner.telegram_id, to_iso(now_utc()))
    await repo.add_cigarette(other.telegram_id, to_iso(now_utc()))
    await repo.db._set_system("marker", "keep-me")

    deleted = await repo.purge_content(owner.telegram_id)
    assert deleted["users"] == 1
    assert deleted["cigarettes"] >= 2
    assert deleted["balance_operations"] >= 1

    kept = await repo.get_user(owner.telegram_id)
    assert kept is not None
    assert kept.timezone == "UTC"
    assert kept.default_sleep_time == "22:30"
    assert await repo.get_user(other.telegram_id) is None
    assert await repo.count_user_entries(owner.telegram_id) == 0
    marker = await repo.fetchone("SELECT value FROM system_info WHERE key = ?", ("marker",))
    assert marker is not None and marker["value"] == "keep-me"
    remaining = dict(await repo.list_tables_with_counts())
    assert remaining["users"] == 1
    assert remaining["user_settings"] == 1
    assert remaining["cigarettes"] == 0
    async with repo.conn.execute("PRAGMA foreign_keys") as cur:
        row = await cur.fetchone()
    assert int(row[0]) == 1


@pytest.mark.asyncio
async def test_service_started_at_is_earliest_registration(repo):
    assert await repo.service_started_at() is None
    await repo.create_user(1, "a", "A", None, "UTC", 10, "23:00")
    await repo.create_user(2, "b", "B", None, "UTC", 10, "23:00")
    await repo.execute("UPDATE users SET registered_at = ? WHERE telegram_id = 1", ("2026-01-01T00:00:00+00:00",))
    await repo.conn.commit()
    assert await repo.service_started_at() == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_earliest_vpn_measured_at(repo):
    assert await repo.earliest_vpn_measured_at() is None
    await repo.insert_vpn_sample("2026-08-10T00:00:00+00:00", True, 20, "n", "s", None)
    await repo.insert_vpn_sample("2026-08-01T00:00:00+00:00", True, 10, "n", "s", None)
    assert await repo.earliest_vpn_measured_at() == "2026-08-01T00:00:00+00:00"


def test_admin_db_keyboards_callback_limit():
    from keyboards.main import admin_db_kb, admin_table_kb, admin_tables_kb

    tables = [("custom_metric_values", 12), ("users", 1)]
    keyboards = [admin_db_kb(), admin_tables_kb(tables), admin_table_kb("custom_metric_values", 10, 30, 10)]
    for kb in keyboards:
        datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert all(data and len(data.encode()) <= 64 for data in datas)
    table_datas = [btn.callback_data for row in admin_tables_kb(tables).inline_keyboard for btn in row]
    assert "ad:tp:custom_metric_values:0" in table_datas


def test_format_sql_grid_and_csv():
    from handlers.admin_db import format_sql_grid, rows_to_csv

    grid = format_sql_grid(["id", "name"], [(1, None), (2, "ok")])
    assert "NULL" in grid
    assert "id" in grid
    csv_text = rows_to_csv(["id", "name"], [(1, None), (2, "ok")])
    assert csv_text.splitlines()[0] == "id,name"
    assert ",ok" in csv_text

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from database.database import Database
from database.queries import Repo
from services.telegram_restore import (
    PENDING_RESTORE_NAME,
    PENDING_SQLITE_NAME,
    RestoreError,
    RestorePreview,
    _cli,
    apply_archive,
    apply_pending_telegram_restore,
    extract_database,
    format_restore_done,
    format_restore_preview,
    inspect_archive,
    looks_like_backup_archive,
    stage_pending_restore,
    stage_pending_sqlite,
)
from tests.conftest import make_config
from utils.runtime import RuntimeControl


def _pack_backup(db_file: Path, archive: Path, *, env: bool = True, extra: list[tuple[Path, str]] | None = None) -> Path:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(db_file, arcname="database.sqlite3")
        if env:
            env_file = db_file.parent / "packed.env"
            env_file.write_text("BOT_TOKEN=secret\n", encoding="utf-8")
            tar.add(env_file, arcname=".env")
        for src, name in extra or []:
            tar.add(src, arcname=name)
    return archive


async def _pack_live_db(db: Database, archive: Path, **kwargs) -> Path:
    snap = archive.parent / f"{archive.stem}-snap.sqlite3"
    await db.backup_to(snap)
    return _pack_backup(snap, archive, **kwargs)


async def _init_db_with_user(tmp_path: Path, telegram_id: int) -> Database:
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    await Repo(db).create_user(telegram_id, "u", "User", None, "UTC", 10, "23:00")
    return db


@pytest.mark.asyncio
async def test_restore_reads_archive_created_by_backup(tmp_path, monkeypatch):
    from dataclasses import replace
    import tarfile

    from services.telegram_backup import create_telegram_archive

    def _gzip_tar(src_dir: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(src_dir, arcname=".")

    root = tmp_path / "host"
    root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")
    config = replace(make_config(tmp_path / "src"), telegram_backup_root=root)
    db = Database(config)
    await db.initialize()
    await Repo(db).create_user(4, "o", "Owner", None, "UTC", 0, "23:00")
    monkeypatch.setattr("services.telegram_backup.write_tar_pigz", _gzip_tar)
    try:
        archive = await create_telegram_archive(db, config)
        preview = await inspect_archive(archive, config.required_db_version)
    finally:
        await db.close()
    assert preview.has_env is True
    assert preview.users_count == 1
    assert preview.compatible is True
    assert preview.db_version == config.required_db_version


def test_looks_like_backup_archive():
    assert looks_like_backup_archive("daily-stats-backup_x.tar.gz") is True
    assert looks_like_backup_archive("x.tgz") is True
    assert looks_like_backup_archive(
        "daily_stats_backup_21_08_2026_19_03_56_33feeff_багофикс_polling_.gz"
    ) is True
    assert looks_like_backup_archive("notes.gz") is False
    assert looks_like_backup_archive("notes.txt") is False
    assert looks_like_backup_archive(None) is False


def test_runtime_restart_sets_stop():
    kicked = []
    runtime = RuntimeControl()
    runtime.bind(lambda: kicked.append(1))
    runtime.request_restart()
    assert runtime.restart is True
    assert runtime.stop.is_set()
    assert kicked == [1]


@pytest.mark.asyncio
async def test_inspect_archive_reads_db_and_env(tmp_path):
    db = await _init_db_with_user(tmp_path / "src", 7)
    try:
        archive = await _pack_live_db(db, tmp_path / "b.tar.gz")
        preview = await inspect_archive(archive, db.config.required_db_version)
    finally:
        await db.close()
    assert preview.integrity_ok is True
    assert preview.has_env is True
    assert preview.users_count == 1
    assert preview.db_version == db.config.required_db_version
    assert preview.compatible is True
    assert "Бэкап принят" in format_restore_preview(preview)
    assert "запущен с данными" in format_restore_done(preview)


@pytest.mark.asyncio
async def test_extract_rejects_missing_database(tmp_path):
    archive = tmp_path / "empty.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        dummy = tmp_path / "x.txt"
        dummy.write_text("no", encoding="utf-8")
        tar.add(dummy, arcname=".env")
    with pytest.raises(RestoreError, match="database.sqlite3"):
        extract_database(archive, tmp_path / "out")


@pytest.mark.asyncio
async def test_extract_ignores_path_traversal(tmp_path):
    db = await _init_db_with_user(tmp_path / "src", 3)
    evil = tmp_path / "payload.txt"
    evil.write_text("hack", encoding="utf-8")
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(db.path, arcname="database.sqlite3")
        tar.add(evil, arcname="../../tmp/hacked")
    try:
        dest = tmp_path / "out"
        sqlite_path, _ = extract_database(archive, dest)
        assert sqlite_path.is_file()
        assert not (tmp_path.parent / "tmp" / "hacked").exists()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_apply_archive_replaces_database(tmp_path):
    src = await _init_db_with_user(tmp_path / "src", 11)
    dst = await _init_db_with_user(tmp_path / "dst", 22)
    archive = await _pack_live_db(src, tmp_path / "b.tar.gz")
    await src.close()
    await dst.close()
    preview = await apply_archive(archive, dst.path, dst.config.required_db_version)
    assert preview.users_count == 1
    check = Database(make_config(tmp_path / "dst"))
    await check.connect()
    try:
        user = await Repo(check).get_user(11)
        gone = await Repo(check).get_user(22)
        assert user is not None
        assert gone is None
        quarantined = list((tmp_path / "dst").glob("database.sqlite3.pre_restore.*"))
        assert quarantined
    finally:
        await check.close()


@pytest.mark.asyncio
async def test_pending_restore_applied_on_startup_helper(tmp_path):
    src = await _init_db_with_user(tmp_path / "src", 5)
    archive = await _pack_live_db(src, tmp_path / "b.tar.gz")
    await src.close()
    dest_dir = tmp_path / "live"
    config = make_config(dest_dir)
    live = Database(config)
    await live.initialize()
    await Repo(live).create_user(99, "old", "Old", None, "UTC", 0, "23:00")
    await live.close()
    stage_pending_restore(archive, config.backup_path, "daily-stats-backup_example.tar.gz")
    assert (config.backup_path / PENDING_RESTORE_NAME).is_file()
    preview = await apply_pending_telegram_restore(config)
    assert preview is not None
    assert preview.users_count == 1
    assert preview.archive_name == "daily-stats-backup_example.tar.gz"
    assert not (config.backup_path / PENDING_RESTORE_NAME).is_file()
    applied = list(config.backup_path.glob("applied-restore_*"))
    assert applied
    live = Database(config)
    await live.initialize()
    try:
        assert await Repo(live).get_user(5) is not None
        assert await Repo(live).get_user(99) is None
    finally:
        await live.close()


@pytest.mark.asyncio
async def test_pending_sqlite_restore_from_disk_copy(tmp_path):
    src = await _init_db_with_user(tmp_path / "src", 15)
    snap = tmp_path / "snap.sqlite3"
    await src.backup_to(snap)
    await src.close()
    dest_dir = tmp_path / "live"
    config = make_config(dest_dir)
    live = Database(config)
    await live.initialize()
    await Repo(live).create_user(77, "old", "Old", None, "UTC", 0, "23:00")
    await live.close()
    stage_pending_sqlite(snap, config.backup_path, "manual_copy.sqlite3")
    assert (config.backup_path / PENDING_SQLITE_NAME).is_file()
    preview = await apply_pending_telegram_restore(config)
    assert preview is not None
    assert preview.users_count == 1
    assert preview.archive_name == "manual_copy.sqlite3"
    assert not (config.backup_path / PENDING_SQLITE_NAME).is_file()
    live = Database(config)
    await live.initialize()
    try:
        assert await Repo(live).get_user(15) is not None
        assert await Repo(live).get_user(77) is None
    finally:
        await live.close()


@pytest.mark.asyncio
async def test_pending_restore_rejects_newer_schema(tmp_path):
    src = await _init_db_with_user(tmp_path / "src", 8)
    await src.set_user_version(999)
    archive = await _pack_live_db(src, tmp_path / "new.tar.gz")
    await src.close()
    dest_dir = tmp_path / "live"
    config = make_config(dest_dir)
    live = Database(config)
    await live.initialize()
    await Repo(live).create_user(1, "keep", "Keep", None, "UTC", 0, "23:00")
    await live.close()
    stage_pending_restore(archive, config.backup_path)
    preview = await apply_pending_telegram_restore(config)
    assert preview is None
    rejected = list(config.backup_path.glob("rejected-restore_*"))
    assert rejected
    live = Database(config)
    await live.initialize()
    try:
        assert await Repo(live).get_user(1) is not None
        assert await Repo(live).get_user(8) is None
    finally:
        await live.close()


def test_cli_stage(tmp_path, capsys):
    import sqlite3

    db_file = tmp_path / "database.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA user_version=1")
    conn.execute("CREATE TABLE users (id INTEGER)")
    conn.commit()
    conn.close()
    archive = _pack_backup(db_file, tmp_path / "b.tar.gz")
    backup_dir = tmp_path / "backups"
    code = _cli(["--stage", "--backup-dir", str(backup_dir), str(archive)])
    assert code == 0
    assert (backup_dir / PENDING_RESTORE_NAME).is_file()
    out = capsys.readouterr().out
    assert "staged" in out


def test_cli_missing_archive(tmp_path):
    assert _cli([str(tmp_path / "nope.tar.gz")]) == 2


def test_format_preview_too_new():
    preview = RestorePreview(
        archive_name="x.tar.gz",
        db_version=99,
        integrity_ok=True,
        users_count=2,
        has_env=False,
        db_size=1000,
        required_db_version=5,
    )
    text = format_restore_preview(preview)
    assert "более новой" in text
    assert preview.compatible is False


def test_admin_restore_keyboards_callback_limit():
    from keyboards.main import (
        admin_backups_kb,
        admin_db_kb,
        admin_disk_backups_kb,
        admin_restore_confirm_kb,
        admin_root_kb,
    )

    root = [btn.callback_data for row in admin_root_kb().inline_keyboard for btn in row]
    assert "ad:bk" in root
    assert "ad:bc" in root
    assert "ad:rst" not in root
    backups = [btn.callback_data for row in admin_backups_kb().inline_keyboard for btn in row]
    assert "ad:bknow" in backups
    assert "ad:rst" in backups
    assert "ad:bkl" in backups
    db_datas = [btn.callback_data for row in admin_db_kb().inline_keyboard for btn in row]
    assert "ad:bk" in db_datas
    confirm = [btn.callback_data for row in admin_restore_confirm_kb().inline_keyboard for btn in row]
    assert "ad:rstok" in confirm
    assert "ad:bk" in confirm
    disk = admin_disk_backups_kb(3, 0, 2)
    datas = [btn.callback_data for row in disk.inline_keyboard for btn in row]
    assert "ad:bks:0" in datas
    assert "ad:bkr:1" in datas
    for kb in (admin_root_kb(), admin_db_kb(), admin_backups_kb(), admin_restore_confirm_kb(disk=True), disk):
        payload = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert all(data and len(data.encode()) <= 64 for data in payload)

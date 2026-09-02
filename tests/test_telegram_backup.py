from __future__ import annotations

import shutil
import tarfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database.database import Database
from database.queries import Repo
from services.jobs import setup_scheduler, telegram_backup_job
from services.telegram_backup import (
    LAST_SENT_KEY,
    TelegramBackupError,
    add_dotenv,
    backup_archive_name,
    backup_group_membership_action,
    backup_timezone,
    collect_configs,
    create_telegram_archive,
    last_telegram_backup_at,
    next_telegram_backup_at,
    send_telegram_backup,
    set_telegram_backup_chat,
    telegram_backup_chat,
    telegram_backup_due,
    write_env_snapshot,
    write_tar_pigz,
)
from tests.conftest import make_config
from utils.app_version import parse_build_git, slugify_commit_title
from utils.time import now_utc, to_iso


def _gzip_tar(src_dir: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(src_dir, arcname=".")


def test_telegram_backup_interval_default(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.delenv("TELEGRAM_BACKUP_INTERVAL_HOURS", raising=False)
    monkeypatch.delenv("TELEGRAM_BACKUP_INTERVAL_MINUTES", raising=False)
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    from config import load_config

    assert load_config().telegram_backup_interval_minutes == 30


def test_telegram_backup_interval_minutes_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("TELEGRAM_BACKUP_INTERVAL_MINUTES", "45")
    monkeypatch.setenv("TELEGRAM_BACKUP_INTERVAL_HOURS", "12")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    from config import load_config

    assert load_config().telegram_backup_interval_minutes == 45


def test_telegram_backup_interval_hours_compat(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.delenv("TELEGRAM_BACKUP_INTERVAL_MINUTES", raising=False)
    monkeypatch.setenv("TELEGRAM_BACKUP_INTERVAL_HOURS", "12")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    from config import load_config

    assert load_config().telegram_backup_interval_minutes == 720


def test_telegram_backup_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("TELEGRAM_BACKUP_INTERVAL_MINUTES", "0")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    from config import load_config

    assert load_config().telegram_backup_interval_minutes == 0


def test_telegram_backup_due_from_last_send():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    assert telegram_backup_due(None, 30, now) is True
    assert telegram_backup_due(now - timedelta(minutes=1), 30, now) is False
    assert telegram_backup_due(now - timedelta(minutes=30), 30, now) is True
    assert telegram_backup_due(now - timedelta(minutes=31), 30, now) is True
    assert telegram_backup_due(now - timedelta(minutes=15), 15, now) is True
    assert telegram_backup_due(None, 0, now) is False


def test_next_telegram_backup_at_counts_from_last_send():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(minutes=10)
    assert next_telegram_backup_at(last, 30, now) == last + timedelta(minutes=30)
    assert next_telegram_backup_at(now - timedelta(minutes=31), 30, now) == now
    assert next_telegram_backup_at(None, 30, now) == now


def test_next_backup_caption():
    from services.telegram_backup import next_backup_caption

    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(minutes=15)
    assert next_backup_caption(last, 30, now) == "Следующий бекап через 15 мин"
    assert next_backup_caption(None, 30, now) == "Следующий бекап: сейчас"
    assert next_backup_caption(last, 0, now) == "Следующий бекап: выкл"


def test_backup_group_membership_action():
    assert (
        backup_group_membership_action(
            "supergroup",
            chat_id=-100,
            stored_id=None,
            old_in=False,
            new_in=True,
            actor_is_owner=True,
        )
        == "bind"
    )
    assert (
        backup_group_membership_action(
            "supergroup",
            chat_id=-100,
            stored_id=None,
            old_in=False,
            new_in=True,
            actor_is_owner=False,
        )
        is None
    )
    assert (
        backup_group_membership_action(
            "private",
            chat_id=1,
            stored_id=None,
            old_in=False,
            new_in=True,
            actor_is_owner=True,
        )
        is None
    )
    assert (
        backup_group_membership_action(
            "supergroup",
            chat_id=-100,
            stored_id=-100,
            old_in=True,
            new_in=False,
            actor_is_owner=False,
        )
        == "unbind"
    )
    assert (
        backup_group_membership_action(
            "supergroup",
            chat_id=-200,
            stored_id=-100,
            old_in=True,
            new_in=False,
            actor_is_owner=True,
        )
        is None
    )


def test_backup_archive_name_has_start_time_commit_and_db():
    started = datetime(2026, 8, 1, 7, 10, 10, tzinfo=timezone.utc)
    name = backup_archive_name(started, "a1b2c3d", "add telegram backup", 4, "Europe/Moscow")
    assert name == "daily-stats-backup_01-08-2026_10-10-10_a1b2c3d_add-telegram-backup_db4.tgz"
    assert ":" not in name


@pytest.mark.asyncio
async def test_backup_timezone_uses_owner_setting(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    try:
        assert await backup_timezone(db, config) == "Europe/Moscow"
        await Repo(db).create_user(1, "o", "O", None, "Asia/Vladivostok", 0, "23:00")
        assert await backup_timezone(db, config) == "Asia/Vladivostok"
    finally:
        await db.close()


def test_slugify_commit_title_keeps_cyrillic():
    assert "бекап" in slugify_commit_title("Добавить бекап в Telegram!")
    assert parse_build_git("commit=abc\ntitle=fix: foo=bar\n") == ("abc", "fix: foo=bar")


def test_app_build_identity_from_env(monkeypatch):
    monkeypatch.setenv("APP_GIT_COMMIT", "deadbeef")
    monkeypatch.setenv("APP_GIT_COMMIT_TITLE", "bake commit into image")
    from utils.app_version import app_build_identity

    assert app_build_identity() == ("deadbeef", "bake commit into image")


def test_telegram_backup_job_scheduled(tmp_path):
    before = datetime.now(timezone.utc)
    scheduler = AsyncIOScheduler(timezone="UTC")
    setup_scheduler(scheduler, bot=object(), repo=object(), db=object(), config=make_config(tmp_path))
    job = scheduler.get_job("telegram_backup")
    assert job is not None
    run_at = job.trigger.run_date
    assert run_at is not None
    assert run_at <= before + timedelta(seconds=10)


def test_telegram_backup_job_skipped_when_disabled(tmp_path):
    config = replace(make_config(tmp_path), telegram_backup_interval_minutes=0)
    scheduler = AsyncIOScheduler(timezone="UTC")
    setup_scheduler(scheduler, bot=object(), repo=object(), db=object(), config=config)
    assert scheduler.get_job("telegram_backup") is None


def test_add_dotenv_copies_file(tmp_path):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    assert add_dotenv(staging, root) == "file"
    assert (staging / ".env").read_text(encoding="utf-8") == "BOT_TOKEN=secret\n"


def test_add_dotenv_snapshots_env(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".env.example").write_text("BOT_TOKEN=\nOWNER_TELEGRAM_ID=\n", encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "from-env")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "42")
    staging = tmp_path / "staging"
    staging.mkdir()
    assert add_dotenv(staging, root) == "snapshot"
    text = (staging / ".env").read_text(encoding="utf-8")
    assert "BOT_TOKEN=from-env" in text
    assert "OWNER_TELEGRAM_ID=42" in text


def test_write_env_snapshot_without_example(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "abc")
    dest = tmp_path / ".env"
    write_env_snapshot(dest, None)
    assert "BOT_TOKEN=abc" in dest.read_text(encoding="utf-8")


def test_collect_configs(tmp_path):
    root = tmp_path / "host"
    (root / "deploy" / "mihomo").mkdir(parents=True)
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "config.py").write_text("# cfg\n", encoding="utf-8")
    (root / "deploy" / "mihomo" / "config.yaml").write_text("mixed-port: 1\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    packed = collect_configs(staging, root)
    assert "docker-compose.yml" in packed
    assert "config.py" in packed
    assert "deploy" in packed
    assert (staging / "configs" / "docker-compose.yml").is_file()
    assert (staging / "configs" / "deploy" / "mihomo" / "config.yaml").is_file()


@pytest.mark.asyncio
async def test_create_archive_contains_db_env_configs(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    config = replace(make_config(tmp_path), telegram_backup_root=root)
    db = Database(config)
    await db.initialize()
    monkeypatch.setattr("services.telegram_backup.write_tar_pigz", _gzip_tar)
    monkeypatch.setattr(
        "services.telegram_backup.app_build_identity",
        lambda: ("abc1234", "add telegram backup"),
    )
    try:
        await Repo(db).insert_vpn_sample("2026-08-20T12:00:00+00:00", True, 40, "n", "s", None)
        archive = await create_telegram_archive(db, config)
        assert archive.exists()
        assert f"_abc1234_add-telegram-backup_db{config.required_db_version}.tgz" in archive.name
        assert archive.name.startswith("daily-stats-backup_")
        with tarfile.open(archive, "r:gz") as tar:
            names = set(tar.getnames())
            db_member = next(
                name for name in names if name.endswith("database.sqlite3") or name == "./database.sqlite3"
            )
            tar.extract(db_member, path=tmp_path / "extracted")
        assert any(name.endswith("database.sqlite3") or name == "./database.sqlite3" for name in names)
        assert not any("vpn.sqlite3" in name for name in names)
        extracted = next((tmp_path / "extracted").rglob("database.sqlite3"))
        import sqlite3

        conn = sqlite3.connect(extracted)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vpn_latency_samples'"
            ).fetchone()
            assert row is None
        finally:
            conn.close()
        assert any(name.endswith(".env") or name == "./.env" for name in names)
        assert any("docker-compose.yml" in name for name in names)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_send_telegram_backup_is_silent_and_records_time(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")
    config = replace(make_config(tmp_path), telegram_backup_root=root)
    db = Database(config)
    await db.initialize()
    monkeypatch.setattr("services.telegram_backup.write_tar_pigz", _gzip_tar)
    monkeypatch.setattr(
        "services.telegram_backup.app_build_identity",
        lambda: ("abc1234", "add telegram backup"),
    )
    sent = {}

    class FakeBot:
        async def send_document(self, chat_id, document, **kwargs):
            sent["chat_id"] = chat_id
            sent["document"] = document
            sent["kwargs"] = kwargs
            return SimpleNamespace(message_id=1)

    try:
        before = now_utc()
        with pytest.raises(TelegramBackupError, match="not set"):
            await send_telegram_backup(db, FakeBot(), config)
        await set_telegram_backup_chat(db, -100123, "Backups")
        path = await send_telegram_backup(db, FakeBot(), config)
        assert sent["chat_id"] == -100123
        assert sent["kwargs"]["disable_notification"] is True
        assert f"v{config.required_db_version}" in sent["kwargs"]["caption"]
        assert "add telegram backup" in sent["kwargs"]["caption"]
        assert "abc1234" in sent["document"].filename
        assert sent["document"].filename.endswith(f"_db{config.required_db_version}.tgz")
        assert not path.exists()
        stored = await last_telegram_backup_at(db)
        assert stored is not None
        assert stored >= before
        assert await db.get_system(LAST_SENT_KEY)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_send_telegram_backup_manual_is_not_silent(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")
    config = replace(make_config(tmp_path), telegram_backup_root=root)
    db = Database(config)
    await db.initialize()
    monkeypatch.setattr("services.telegram_backup.write_tar_pigz", _gzip_tar)
    sent = {}

    class FakeBot:
        async def send_document(self, chat_id, document, **kwargs):
            sent["kwargs"] = kwargs
            return SimpleNamespace(message_id=1)

    try:
        await send_telegram_backup(db, FakeBot(), config, silent=False, chat_id=1)
        assert sent["kwargs"]["disable_notification"] is False
        assert "вручную" in sent["kwargs"]["caption"]
    finally:
        await db.close()


def test_format_backups_panel():
    from datetime import datetime, timezone

    from services.telegram_backup import format_backups_panel

    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(minutes=15)
    text = format_backups_panel(
        last_sent=last,
        interval_minutes=30,
        disk_count=3,
        latest_disk="scheduled_20260821.sqlite3",
        last_disk_at=last,
        group_id=-100123,
        group_title="Backups",
        now=now,
    )
    assert "Бэкапы" in text
    assert "каждые 30 мин" in text
    assert "Backups" in text
    assert "В личку — только по кнопке" in text
    assert "Копий SQLite на диске: 3" in text
    assert "scheduled_20260821.sqlite3" in text
    assert "Следующий бекап через 15 мин" in text

    unbound = format_backups_panel(
        last_sent=last,
        interval_minutes=30,
        disk_count=0,
        latest_disk=None,
        now=now,
    )
    assert "группа не привязана" in unbound
    assert "/backup_here" in unbound


@pytest.mark.asyncio
async def test_job_skips_and_reschedules_when_recently_sent(tmp_path):
    config = replace(make_config(tmp_path), telegram_backup_interval_minutes=30)
    db = Database(config)
    await db.initialize()
    last = now_utc() - timedelta(minutes=10)
    await db._set_system(LAST_SENT_KEY, to_iso(last))
    scheduler = AsyncIOScheduler(timezone="UTC")
    sent = {"n": 0}

    async def fake_send(*_args, **_kwargs):
        sent["n"] += 1

    try:
        import services.telegram_backup as tb

        original = tb.send_telegram_backup
        tb.send_telegram_backup = fake_send
        await telegram_backup_job(scheduler, db, object(), config)
        assert sent["n"] == 0
        job = scheduler.get_job("telegram_backup")
        assert job is not None
        expected = last + timedelta(minutes=30)
        assert abs((job.trigger.run_date - expected).total_seconds()) < 2
    finally:
        tb.send_telegram_backup = original
        await db.close()


@pytest.mark.asyncio
async def test_job_skips_when_group_not_bound(tmp_path):
    config = replace(make_config(tmp_path), telegram_backup_interval_minutes=30)
    db = Database(config)
    await db.initialize()
    scheduler = AsyncIOScheduler(timezone="UTC")
    sent = {"n": 0}

    async def fake_send(*_args, **_kwargs):
        sent["n"] += 1

    try:
        import services.telegram_backup as tb

        original = tb.send_telegram_backup
        tb.send_telegram_backup = fake_send
        before = now_utc()
        await telegram_backup_job(scheduler, db, object(), config)
        assert sent["n"] == 0
        job = scheduler.get_job("telegram_backup")
        assert job is not None
        assert job.trigger.run_date >= before + timedelta(minutes=29, seconds=50)
    finally:
        tb.send_telegram_backup = original
        await db.close()


@pytest.mark.asyncio
async def test_job_sends_when_interval_elapsed(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")
    config = replace(make_config(tmp_path), telegram_backup_root=root, telegram_backup_interval_minutes=30)
    db = Database(config)
    await db.initialize()
    await db._set_system(LAST_SENT_KEY, to_iso(now_utc() - timedelta(minutes=31)))
    await set_telegram_backup_chat(db, -100123, "Backups")
    monkeypatch.setattr("services.telegram_backup.write_tar_pigz", _gzip_tar)
    scheduler = AsyncIOScheduler(timezone="UTC")
    sent = {"n": 0}

    class FakeBot:
        async def send_document(self, chat_id, document, **kwargs):
            sent["n"] += 1
            return SimpleNamespace(message_id=1)

    try:
        before = now_utc()
        await telegram_backup_job(scheduler, db, FakeBot(), config)
        assert sent["n"] == 1
        job = scheduler.get_job("telegram_backup")
        assert job is not None
        assert job.trigger.run_date >= before + timedelta(minutes=29, seconds=50)
        stored = await last_telegram_backup_at(db)
        assert stored is not None
        assert stored >= before
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_telegram_backup_chat_roundtrip(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    try:
        assert await telegram_backup_chat(db) == (None, None)
        await set_telegram_backup_chat(db, -100123, "Backups")
        assert await telegram_backup_chat(db) == (-100123, "Backups")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_owner_join_binds_backup_group(tmp_path):
    from handlers.admin_restore import backup_chat_member_update

    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    answers: list[str] = []

    class Event:
        chat = SimpleNamespace(id=-100123, type="supergroup", title="Backups")
        from_user = SimpleNamespace(id=config.owner_id)
        old_chat_member = SimpleNamespace(status="left", is_member=False)
        new_chat_member = SimpleNamespace(status="member", is_member=True)

        async def answer(self, text):
            answers.append(text)

    try:
        await backup_chat_member_update(Event(), config, Repo(db), object(), None)
        assert await telegram_backup_chat(db) == (-100123, "Backups")
        assert answers and "автоматические бэкапы" in answers[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_non_owner_join_does_not_bind_backup_group(tmp_path):
    from handlers.admin_restore import backup_chat_member_update

    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()

    class Event:
        chat = SimpleNamespace(id=-100123, type="supergroup", title="Backups")
        from_user = SimpleNamespace(id=config.owner_id + 1)
        old_chat_member = SimpleNamespace(status="left", is_member=False)
        new_chat_member = SimpleNamespace(status="member", is_member=True)

        async def answer(self, text):
            raise AssertionError("should not announce")

    try:
        await backup_chat_member_update(Event(), config, Repo(db), object(), None)
        assert await telegram_backup_chat(db) == (None, None)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_leave_unbinds_backup_group(tmp_path, monkeypatch):
    from handlers.admin_restore import backup_chat_member_update

    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    await set_telegram_backup_chat(db, -100123, "Backups")
    notices: list[str] = []

    async def fake_notify(_bot, _config, text, **_kwargs):
        notices.append(text)

    monkeypatch.setattr("handlers.admin_restore.notify_owner", fake_notify)

    class Event:
        chat = SimpleNamespace(id=-100123, type="supergroup", title="Backups")
        from_user = SimpleNamespace(id=99)
        old_chat_member = SimpleNamespace(status="member", is_member=True)
        new_chat_member = SimpleNamespace(status="left", is_member=False)

        async def answer(self, text):
            raise AssertionError("cannot write to a left chat")

    try:
        await backup_chat_member_update(Event(), config, Repo(db), object(), None)
        assert await telegram_backup_chat(db) == (None, None)
        assert notices and "отключена" in notices[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backup_here_binds_group(tmp_path):
    from handlers.admin_restore import backup_here

    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    answers: list[str] = []

    class Message:
        from_user = SimpleNamespace(id=config.owner_id)
        chat = SimpleNamespace(id=-100123, type="supergroup", title="Backups")

        async def answer(self, text):
            answers.append(text)

    try:
        await backup_here(Message(), config, Repo(db), object(), None)
        assert await telegram_backup_chat(db) == (-100123, "Backups")
        assert answers
    finally:
        await db.close()


def test_write_tar_pigz_requires_pigz(tmp_path, monkeypatch):
    monkeypatch.setattr("services.telegram_backup.shutil.which", lambda _name: None)
    with pytest.raises(TelegramBackupError, match="pigz"):
        write_tar_pigz(tmp_path, tmp_path / "out.tar.gz")


def test_write_tar_pigz_invokes_pigz(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("ok", encoding="utf-8")
    dest = tmp_path / "out.tar.gz"
    monkeypatch.setattr("services.telegram_backup.shutil.which", lambda _name: "/usr/bin/pigz")

    def fake_run(cmd, **_kwargs):
        assert cmd[0] == "tar"
        assert cmd[1] == "--use-compress-program"
        assert cmd[2] == "/usr/bin/pigz"
        Path(cmd[4]).write_bytes(b"gz")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("services.telegram_backup.subprocess.run", fake_run)
    write_tar_pigz(src, dest)
    assert dest.read_bytes() == b"gz"


@pytest.mark.skipif(not shutil.which("pigz"), reason="pigz not installed")
def test_write_tar_pigz_real_archive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.txt").write_text("hello", encoding="utf-8")
    dest = tmp_path / "out.tar.gz"
    write_tar_pigz(src, dest)
    assert dest.is_file() and dest.stat().st_size > 0
    with tarfile.open(dest, "r:gz") as tar:
        names = tar.getnames()
    assert any(name.endswith("hello.txt") for name in names)

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
    backup_timezone,
    collect_configs,
    create_telegram_archive,
    last_telegram_backup_at,
    next_telegram_backup_at,
    send_telegram_backup,
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
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    from config import load_config

    assert load_config().telegram_backup_interval_hours == 12


def test_telegram_backup_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("TELEGRAM_BACKUP_INTERVAL_HOURS", "0")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    from config import load_config

    assert load_config().telegram_backup_interval_hours == 0


def test_telegram_backup_due_from_last_send():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    assert telegram_backup_due(None, 12, now) is True
    assert telegram_backup_due(now - timedelta(hours=1), 12, now) is False
    assert telegram_backup_due(now - timedelta(hours=12), 12, now) is True
    assert telegram_backup_due(now - timedelta(hours=13), 12, now) is True
    assert telegram_backup_due(now - timedelta(hours=6), 6, now) is True
    assert telegram_backup_due(None, 0, now) is False


def test_next_telegram_backup_at_counts_from_last_send():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(hours=3)
    assert next_telegram_backup_at(last, 12, now) == last + timedelta(hours=12)
    assert next_telegram_backup_at(now - timedelta(hours=13), 12, now) == now
    assert next_telegram_backup_at(None, 12, now) == now


def test_next_backup_caption():
    from services.telegram_backup import next_backup_caption

    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(hours=6)
    assert next_backup_caption(last, 12, now) == "Следующий бекап через 6 ч"
    assert next_backup_caption(None, 12, now) == "Следующий бекап: сейчас"
    assert next_backup_caption(last, 0, now) == "Следующий бекап: выкл"


def test_backup_archive_name_has_start_time_commit_and_db():
    started = datetime(2026, 8, 1, 7, 10, 10, tzinfo=timezone.utc)
    name = backup_archive_name(started, "a1b2c3d", "add telegram backup", 4, "Europe/Moscow")
    assert name == "daily-stats-backup_01-08-2026_10-10-10_a1b2c3d_add-telegram-backup_db4.tar.gz"


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
    config = replace(make_config(tmp_path), telegram_backup_interval_hours=0)
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
        archive = await create_telegram_archive(db, config)
        assert archive.exists()
        assert f"_abc1234_add-telegram-backup_db{config.required_db_version}.tar.gz" in archive.name
        assert archive.name.startswith("daily-stats-backup_")
        with tarfile.open(archive, "r:gz") as tar:
            names = set(tar.getnames())
        assert any(name.endswith("database.sqlite3") or name == "./database.sqlite3" for name in names)
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
        path = await send_telegram_backup(db, FakeBot(), config)
        assert sent["chat_id"] == 1
        assert sent["kwargs"]["disable_notification"] is True
        assert "Резервная копия" in sent["kwargs"]["caption"]
        assert f"v{config.required_db_version}" in sent["kwargs"]["caption"]
        assert "add telegram backup" in sent["kwargs"]["caption"]
        assert "abc1234" in sent["document"].filename
        assert sent["document"].filename.endswith(f"_db{config.required_db_version}.tar.gz")
        assert not path.exists()
        stored = await last_telegram_backup_at(db)
        assert stored is not None
        assert stored >= before
        assert await db.get_system(LAST_SENT_KEY)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_job_skips_and_reschedules_when_recently_sent(tmp_path):
    config = replace(make_config(tmp_path), telegram_backup_interval_hours=12)
    db = Database(config)
    await db.initialize()
    last = now_utc() - timedelta(hours=3)
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
        expected = last + timedelta(hours=12)
        assert abs((job.trigger.run_date - expected).total_seconds()) < 2
    finally:
        tb.send_telegram_backup = original
        await db.close()


@pytest.mark.asyncio
async def test_job_sends_when_interval_elapsed(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".env").write_text("BOT_TOKEN=secret\n", encoding="utf-8")
    config = replace(make_config(tmp_path), telegram_backup_root=root, telegram_backup_interval_hours=12)
    db = Database(config)
    await db.initialize()
    await db._set_system(LAST_SENT_KEY, to_iso(now_utc() - timedelta(hours=13)))
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
        assert job.trigger.run_date >= before + timedelta(hours=11, minutes=59)
        stored = await last_telegram_backup_at(db)
        assert stored is not None
        assert stored >= before
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

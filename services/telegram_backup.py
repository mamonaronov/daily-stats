"""Pack database + configs + .env and send a silent Telegram backup."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from config import Config
from database.database import Database
from database.queries import Repo
from utils.app_version import app_build_identity, slugify_commit_title
from utils.formatting import seconds_human
from utils.time import is_valid_timezone, now_utc, parse_iso, to_iso, to_user

logger = logging.getLogger(__name__)

TELEGRAM_DOCUMENT_LIMIT = 50 * 1024 * 1024
CONFIG_ITEMS = (
    ".env.example",
    "config.py",
    "docker-compose.yml",
    "docker-compose.override.yml",
    "Dockerfile",
    "docker-entrypoint.sh",
    "deploy",
)
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".gitignore")
LAST_SENT_KEY = "last_telegram_backup_at"


class TelegramBackupError(RuntimeError):
    pass


def telegram_backup_due(
    last_sent: datetime | None,
    interval_hours: int,
    now: datetime | None = None,
) -> bool:
    if interval_hours <= 0:
        return False
    now = now or now_utc()
    if last_sent is None:
        return True
    return now >= last_sent + timedelta(hours=interval_hours)


def next_telegram_backup_at(
    last_sent: datetime | None,
    interval_hours: int,
    now: datetime | None = None,
) -> datetime:
    now = now or now_utc()
    if last_sent is None or interval_hours <= 0:
        return now
    due_at = last_sent + timedelta(hours=interval_hours)
    return now if now >= due_at else due_at


def next_backup_caption(
    last_sent: datetime | None,
    interval_hours: int,
    now: datetime | None = None,
) -> str:
    now = now or now_utc()
    if interval_hours <= 0:
        return "Следующий бекап: выкл"
    when = next_telegram_backup_at(last_sent, interval_hours, now)
    remaining = (when - now).total_seconds()
    if remaining <= 0:
        return "Следующий бекап: сейчас"
    return f"Следующий бекап через {seconds_human(remaining)}"


async def last_telegram_backup_at(db: Database) -> datetime | None:
    raw = await db.get_system(LAST_SENT_KEY)
    if not raw:
        return None
    try:
        return parse_iso(raw)
    except ValueError:
        logger.warning("Invalid %s value: %s", LAST_SENT_KEY, raw)
        return None


async def mark_telegram_backup_sent(db: Database, when: datetime | None = None) -> datetime:
    stamp = when or now_utc()
    await db._set_system(LAST_SENT_KEY, to_iso(stamp))
    return stamp


async def backup_timezone(db: Database, config: Config) -> str:
    user = await Repo(db).get_user(config.owner_id)
    tz = (user.timezone if user else "") or config.default_timezone
    if not is_valid_timezone(tz):
        return config.default_timezone
    return tz


def backup_archive_name(
    started: datetime,
    commit: str,
    title: str,
    db_version: int,
    tz_name: str,
) -> str:
    # Colons in filenames break KDE Ark/Qt: the name is parsed as a URL (scheme:host).
    stamp = to_user(started, tz_name).strftime("%d-%m-%Y_%H-%M-%S")
    short = re.sub(r"[^A-Za-z0-9]", "", commit)[:12] or "unknown"
    slug = slugify_commit_title(title)
    return f"daily-stats-backup_{stamp}_{short}_{slug}_db{db_version}.tar.gz"


def _copy_item(src: Path, dest: Path) -> bool:
    if not src.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=_IGNORE)
        return True
    shutil.copy2(src, dest)
    return True


def _env_keys_from_example(example: Path) -> list[str]:
    if not example.is_file():
        return []
    keys: list[str] = []
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.append(stripped.split("=", 1)[0])
    return keys


def write_env_snapshot(dest: Path, example: Path | None = None) -> None:
    keys = _env_keys_from_example(example) if example else []
    if not keys:
        keys = [
            "BOT_TOKEN",
            "OWNER_TELEGRAM_ID",
            "OWNER_CONTACT",
            "DEFAULT_TIMEZONE",
            "DEFAULT_DAILY_PRICE",
            "DB_PATH",
            "BACKUP_PATH",
            "TELEGRAM_PROXY_URL",
        ]
    lines = ["# Snapshot of runtime environment", ""]
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{key}={os.environ.get(key, '')}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_dotenv(staging: Path, root: Path) -> str:
    dest = staging / ".env"
    for src in (root / ".env", Path("/app/.env.runtime")):
        try:
            if src.is_file():
                shutil.copy2(src, dest)
                return "file"
        except OSError:
            logger.warning("Cannot read %s, trying next source", src)
    write_env_snapshot(dest, root / ".env.example")
    return "snapshot"


def collect_configs(staging: Path, root: Path) -> list[str]:
    packed: list[str] = []
    configs_dir = staging / "configs"
    for name in CONFIG_ITEMS:
        src = root / name
        try:
            if _copy_item(src, configs_dir / name):
                packed.append(name)
        except OSError:
            logger.warning("Skipped config %s", src)
    return packed


def write_tar_pigz(src_dir: Path, dest: Path) -> None:
    pigz = shutil.which("pigz")
    if not pigz:
        raise TelegramBackupError("pigz is not installed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        subprocess.run(
            [
                "tar",
                "--use-compress-program",
                pigz,
                "-cf",
                str(tmp),
                "-C",
                str(src_dir),
                ".",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        tmp.replace(dest)
    except subprocess.CalledProcessError as exc:
        tmp.unlink(missing_ok=True)
        detail = (exc.stderr or exc.stdout or "").strip()
        raise TelegramBackupError(f"pigz/tar failed: {detail or exc}") from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def create_telegram_archive(db: Database, config: Config) -> Path:
    started = now_utc()
    db_version = await db.user_version()
    commit, title = app_build_identity()
    tz_name = await backup_timezone(db, config)
    archive_name = backup_archive_name(started, commit, title, db_version, tz_name)
    archive_path = config.backup_path / archive_name
    root = config.telegram_backup_root
    staging = Path(tempfile.mkdtemp(prefix="tg-backup-"))
    try:
        await db.backup_to(staging / "database.sqlite3")
        env_source = add_dotenv(staging, root)
        packed = collect_configs(staging, root)
        logger.info(
            "Telegram backup packing env=%s configs=%s commit=%s db=%s",
            env_source,
            ",".join(packed) or "-",
            commit,
            db_version,
        )
        await asyncio.to_thread(write_tar_pigz, staging, archive_path)
        return archive_path
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def send_telegram_backup(db: Database, bot: Bot, config: Config) -> Path:
    path = await create_telegram_archive(db, config)
    size = path.stat().st_size
    if size > TELEGRAM_DOCUMENT_LIMIT:
        raise TelegramBackupError(
            f"Archive {path.name} is {size} bytes, over Telegram limit {TELEGRAM_DOCUMENT_LIMIT}"
        )
    commit, title = app_build_identity()
    db_version = await db.user_version()
    caption = (
        "📦 <b>Резервная копия</b>\n"
        f"<code>{path.name}</code>\n"
        f"Коммит: {html.escape(title)} (<code>{html.escape(commit)}</code>)\n"
        f"БД: v{db_version}\n"
        "БД + конфиги + .env"
    )
    try:
        await bot.send_document(
            config.owner_id,
            FSInputFile(path, filename=path.name),
            caption=caption,
            disable_notification=True,
            request_timeout=120,
        )
    except Exception:
        logger.exception("Failed to send telegram backup %s, file kept at %s", path.name, path)
        raise
    path.unlink(missing_ok=True)
    await mark_telegram_backup_sent(db)
    logger.info("Telegram backup sent %s (%s bytes)", path.name, size)
    return path

"""Service configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REQUIRED_DB_VERSION = 2


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    owner_id: int
    owner_contact: str
    default_timezone: str
    default_daily_price: float
    default_sleep_time: str
    reminder_hours_before_sleep: int
    reminder_fallback_time: str
    db_path: Path
    backup_path: Path
    backup_interval_hours: int
    backup_keep: int
    billing_check_minutes: int
    reminder_check_minutes: int
    log_level: str
    telegram_proxy_url: str | None
    required_db_version: int = REQUIRED_DB_VERSION


def load_config() -> Config:
    load_dotenv()

    token = _require("BOT_TOKEN")
    owner_id = int(_require("OWNER_TELEGRAM_ID"))

    db_path = Path(os.getenv("DB_PATH", "/app/data/database.sqlite3"))
    backup_path = Path(os.getenv("BACKUP_PATH", "/app/backups"))

    return Config(
        bot_token=token,
        owner_id=owner_id,
        owner_contact=os.getenv("OWNER_CONTACT", "").strip() or "владелец сервиса",
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow").strip(),
        default_daily_price=_float("DEFAULT_DAILY_PRICE", 10.0),
        default_sleep_time=os.getenv("DEFAULT_SLEEP_TIME", "23:00").strip(),
        reminder_hours_before_sleep=_int("REMINDER_HOURS_BEFORE_SLEEP", 3),
        reminder_fallback_time=os.getenv("REMINDER_FALLBACK_TIME", "20:45").strip(),
        db_path=db_path,
        backup_path=backup_path,
        backup_interval_hours=_int("BACKUP_INTERVAL_HOURS", 6),
        backup_keep=_int("BACKUP_KEEP", 14),
        billing_check_minutes=_int("BILLING_CHECK_MINUTES", 15),
        reminder_check_minutes=_int("REMINDER_CHECK_MINUTES", 1),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        telegram_proxy_url=_optional("TELEGRAM_PROXY_URL"),
    )

"""Service configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REQUIRED_DB_VERSION = 8
PROJECT_ROOT = Path(__file__).resolve().parent


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


def _telegram_backup_interval_minutes() -> int:
    minutes_raw = os.getenv("TELEGRAM_BACKUP_INTERVAL_MINUTES")
    if minutes_raw is not None and minutes_raw.strip() != "":
        return max(0, int(minutes_raw))
    hours_raw = os.getenv("TELEGRAM_BACKUP_INTERVAL_HOURS")
    if hours_raw is not None and hours_raw.strip() != "":
        return max(0, int(hours_raw) * 60)
    return 30


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    owner_id: int
    owner_contact: str
    default_timezone: str
    default_daily_price: float
    default_sleep_time: str
    db_path: Path
    backup_path: Path
    backup_interval_hours: int
    backup_keep: int
    billing_check_minutes: int
    log_level: str
    telegram_proxy_url: str | None
    required_db_version: int = REQUIRED_DB_VERSION
    vpn_monitor_enabled: bool = True
    vpn_monitor_interval_seconds: int = 10
    vpn_monitor_timeout_seconds: int = 8
    mihomo_api_url: str = "http://127.0.0.1:19090"
    mihomo_api_secret: str | None = None
    mihomo_proxy_group: str = "AUTO"
    vpn_log_dir: Path | None = None
    vpn_log_keep_days: int = 31
    telegram_backup_interval_minutes: int = 30
    telegram_backup_root: Path = PROJECT_ROOT


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
        db_path=db_path,
        backup_path=backup_path,
        backup_interval_hours=_int("BACKUP_INTERVAL_HOURS", 6),
        backup_keep=_int("BACKUP_KEEP", 14),
        billing_check_minutes=_int("BILLING_CHECK_MINUTES", 15),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        telegram_proxy_url=_optional("TELEGRAM_PROXY_URL"),
        vpn_monitor_enabled=_bool("VPN_MONITOR_ENABLED", True),
        vpn_monitor_interval_seconds=_int("VPN_MONITOR_INTERVAL_SECONDS", 10),
        vpn_monitor_timeout_seconds=_int("VPN_MONITOR_TIMEOUT_SECONDS", 8),
        mihomo_api_url=os.getenv("MIHOMO_API_URL", "http://127.0.0.1:19090").strip()
        or "http://127.0.0.1:19090",
        mihomo_api_secret=_optional("MIHOMO_API_SECRET"),
        mihomo_proxy_group=os.getenv("MIHOMO_PROXY_GROUP", "AUTO").strip() or "AUTO",
        vpn_log_dir=Path(os.getenv("VPN_LOG_DIR", str(db_path.parent / "vpn"))),
        vpn_log_keep_days=_int("VPN_LOG_KEEP_DAYS", 31),
        telegram_backup_interval_minutes=_telegram_backup_interval_minutes(),
        telegram_backup_root=Path(os.getenv("TELEGRAM_BACKUP_ROOT", "").strip() or str(PROJECT_ROOT)),
    )

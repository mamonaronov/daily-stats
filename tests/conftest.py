from __future__ import annotations

from pathlib import Path

import pytest

from config import Config
from database.database import Database
from database.queries import Repo


def make_config(tmp_path: Path) -> Config:
    return Config(
        bot_token="0:test",
        owner_id=1,
        owner_contact="@owner",
        default_timezone="Europe/Moscow",
        default_daily_price=10.0,
        default_sleep_time="23:00",
        db_path=tmp_path / "database.sqlite3",
        backup_path=tmp_path / "backups",
        backup_interval_hours=6,
        backup_keep=5,
        billing_check_minutes=15,
        log_level="WARNING",
        telegram_proxy_url=None,
    )


@pytest.fixture
async def repo(tmp_path: Path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    try:
        yield Repo(db)
    finally:
        await db.close()

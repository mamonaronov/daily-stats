from __future__ import annotations

from datetime import datetime, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.jobs import setup_scheduler
from services.wake_reminders import REMINDER_TEXT, is_wake_reminder_due, send_wake_up_reminders
from tests.conftest import make_config
from tests.test_ui import FakeBot
from utils.time import to_iso, user_now


def test_wake_reminder_due_clock():
    now = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
    assert is_wake_reminder_due("10:00", None, now, False)
    assert is_wake_reminder_due("09:30", None, now, False)
    assert not is_wake_reminder_due("10:01", None, now, False)
    assert not is_wake_reminder_due("10:00", "2026-09-10", now, False)
    assert is_wake_reminder_due("10:00", "2026-09-09", now, False)
    assert not is_wake_reminder_due("10:00", None, now, True)
    assert not is_wake_reminder_due(None, None, now, False)
    assert not is_wake_reminder_due("nope", None, now, False)


def test_wake_reminder_job_is_scheduled(tmp_path):
    scheduler = AsyncIOScheduler(timezone="UTC")
    setup_scheduler(scheduler, bot=object(), repo=object(), db=object(), config=make_config(tmp_path))
    job = scheduler.get_job("wake_up_reminders")
    assert job is not None


@pytest.mark.asyncio
async def test_wake_reminder_sends_once_until_up_logged(repo):
    user = await repo.create_user(71, "wake", "Ваня", None, "UTC", 0, "23:00")
    await repo.set_wake_up_reminder(user.telegram_id, "00:00")
    bot = FakeBot()

    sent = await send_wake_up_reminders(repo, bot)
    assert sent == 1
    assert bot.sent[0][0] == 71
    assert bot.sent[0][1] == REMINDER_TEXT
    assert bot.sent[0][2] is not None

    sent = await send_wake_up_reminders(repo, bot)
    assert sent == 0
    assert len(bot.sent) == 1

    user = await repo.get_user(71)
    assert user.wake_up_reminder_time == "00:00"
    assert user.wake_up_reminder_sent_on == user_now("UTC").date().isoformat()


@pytest.mark.asyncio
async def test_wake_reminder_skips_if_already_got_up(repo):
    user = await repo.create_user(72, "up", "Оля", None, "UTC", 0, "23:00")
    await repo.set_wake_up_reminder(user.telegram_id, "00:00")
    await repo.add_sleep(user.telegram_id, out_of_bed_at=to_iso(user_now("UTC")))
    bot = FakeBot()
    sent = await send_wake_up_reminders(repo, bot)
    assert sent == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_wake_reminder_still_asks_if_awake_but_not_up(repo):
    user = await repo.create_user(73, "awake", "Кира", None, "UTC", 0, "23:00")
    await repo.set_wake_up_reminder(user.telegram_id, "00:00")
    await repo.add_sleep(user.telegram_id, wake_time=to_iso(user_now("UTC")))
    bot = FakeBot()
    sent = await send_wake_up_reminders(repo, bot)
    assert sent == 1
    markup = bot.sent[0][2]
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "Встал" in texts


@pytest.mark.asyncio
async def test_wake_reminder_off_is_not_listed(repo):
    user = await repo.create_user(74, "off", "Нет", None, "UTC", 0, "23:00")
    await repo.set_wake_up_reminder(user.telegram_id, "08:00")
    await repo.set_wake_up_reminder(user.telegram_id, None)
    user = await repo.get_user(74)
    assert user.wake_up_reminder_time is None
    assert await repo.list_wake_reminder_users() == []
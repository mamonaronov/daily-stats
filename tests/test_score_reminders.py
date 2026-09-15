from __future__ import annotations

from datetime import datetime, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.jobs import setup_scheduler
from services.score_reminders import (
    is_score_reminder_due,
    reminder_text,
    send_daily_score_reminders,
)
from services.ui_prefs import prefs_of, save_prefs
from tests.conftest import make_config
from tests.test_ui import FakeBot
from utils.time import to_iso, user_now


def test_score_reminder_due_clock():
    now = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)
    assert is_score_reminder_due("21:00", None, now, ["day_rating"])
    assert is_score_reminder_due("20:30", None, now, ["mood"])
    assert not is_score_reminder_due("21:01", None, now, ["day_rating"])
    assert not is_score_reminder_due("21:00", "2026-09-10", now, ["day_rating"])
    assert is_score_reminder_due("21:00", "2026-09-09", now, ["day_rating"])
    assert not is_score_reminder_due("21:00", None, now, [])
    assert not is_score_reminder_due(None, None, now, ["day_rating"])
    assert not is_score_reminder_due("nope", None, now, ["day_rating"])


def test_score_reminder_text_lists_missing():
    assert "оценка дня" in reminder_text(["day_rating"])
    blob = reminder_text(["mood", "stress"])
    assert "настроение" in blob
    assert "стресс" in blob


def test_score_reminder_job_is_scheduled(tmp_path):
    scheduler = AsyncIOScheduler(timezone="UTC")
    setup_scheduler(scheduler, bot=object(), repo=object(), db=object(), config=make_config(tmp_path))
    job = scheduler.get_job("daily_score_reminders")
    assert job is not None


async def _track_scores(repo, user, *keys: str):
    prefs = prefs_of(user)
    prefs.tracked.update(keys)
    return await save_prefs(repo, user, prefs)


@pytest.mark.asyncio
async def test_score_reminder_sends_once_until_rated(repo):
    user = await repo.create_user(81, "score", "Ваня", None, "UTC", 0, "23:00")
    user = await _track_scores(repo, user, "day_rating")
    await repo.set_daily_score_reminder(user.telegram_id, "00:00")
    bot = FakeBot()

    sent = await send_daily_score_reminders(repo, bot)
    assert sent == 1
    assert bot.sent[0][0] == 81
    assert "оценить" in bot.sent[0][1].lower()
    texts = [btn.text for row in bot.sent[0][2].inline_keyboard for btn in row]
    assert any("сегодня" in text.lower() for text in texts)

    sent = await send_daily_score_reminders(repo, bot)
    assert sent == 0
    assert len(bot.sent) == 1

    user = await repo.get_user(81)
    assert user.daily_score_reminder_time == "00:00"
    assert user.daily_score_reminder_sent_on == user_now("UTC").date().isoformat()


@pytest.mark.asyncio
async def test_score_reminder_skips_if_already_rated(repo):
    user = await repo.create_user(82, "rated", "Оля", None, "UTC", 0, "23:00")
    user = await _track_scores(repo, user, "day_rating")
    await repo.set_daily_score_reminder(user.telegram_id, "00:00")
    await repo.upsert_daily_score(
        user.telegram_id,
        user_now("UTC").date().isoformat(),
        "day_rating",
        4,
        to_iso(user_now("UTC")),
    )
    bot = FakeBot()
    sent = await send_daily_score_reminders(repo, bot)
    assert sent == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_score_reminder_still_asks_if_some_scores_missing(repo):
    user = await repo.create_user(83, "partial", "Кира", None, "UTC", 0, "23:00")
    user = await _track_scores(repo, user, "mood", "stress")
    await repo.set_daily_score_reminder(user.telegram_id, "00:00")
    await repo.upsert_daily_score(
        user.telegram_id,
        user_now("UTC").date().isoformat(),
        "mood",
        3,
        to_iso(user_now("UTC")),
    )
    bot = FakeBot()
    sent = await send_daily_score_reminders(repo, bot)
    assert sent == 1
    assert "стресс" in bot.sent[0][1].lower()
    assert "настроение" not in bot.sent[0][1].lower()


@pytest.mark.asyncio
async def test_score_reminder_skips_without_tracked_scores(repo):
    user = await repo.create_user(84, "empty", "Нет", None, "UTC", 0, "23:00")
    await repo.set_daily_score_reminder(user.telegram_id, "00:00")
    bot = FakeBot()
    sent = await send_daily_score_reminders(repo, bot)
    assert sent == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_score_reminder_off_is_not_listed(repo):
    user = await repo.create_user(85, "off", "Нет", None, "UTC", 0, "23:00")
    await repo.set_daily_score_reminder(user.telegram_id, "21:00")
    await repo.set_daily_score_reminder(user.telegram_id, None)
    user = await repo.get_user(85)
    assert user.daily_score_reminder_time is None
    assert await repo.list_daily_score_reminder_users() == []

from __future__ import annotations

from datetime import timedelta

import pytest

from services.charts import build_charts
from services.daily_scores import parse_daily_score, spec_of
from services.entries import upsert_daily_score
from services.history import build_timeline, format_timeline
from services.statistics import render_stats
from services.today import day_snapshot
from utils.time import user_today


def test_parse_daily_score_range():
    assert parse_daily_score("1") == 1
    assert parse_daily_score("5") == 5
    try:
        parse_daily_score("0")
        raise AssertionError("expected error")
    except ValueError:
        pass
    try:
        parse_daily_score("6")
        raise AssertionError("expected error")
    except ValueError:
        pass


@pytest.mark.asyncio
async def test_daily_score_upsert_one_per_day_and_timeline(repo):
    user = await repo.create_user(90, "scores", "Саша", None, "UTC", 0, "23:00")
    today = user_today(user.timezone)
    item_id, error, updated = await upsert_daily_score(repo, user, today, "mood", 3)
    assert error is None and item_id is not None and updated is False
    same_id, error, updated = await upsert_daily_score(repo, user, today, "mood", 5)
    assert error is None and updated is True
    assert same_id == item_id
    rec = await repo.get_daily_score(item_id, user.telegram_id)
    assert rec is not None
    assert rec.kind == "mood"
    assert rec.score == 5
    assert rec.day == today.isoformat()
    energy_id, error, updated = await upsert_daily_score(repo, user, today, "energy", 2)
    assert error is None and updated is False
    assert energy_id != item_id
    rows = await repo.list_daily_scores_for_day(user.telegram_id, today.isoformat())
    assert {row.kind: row.score for row in rows} == {"mood": 5, "energy": 2}
    items = await build_timeline(repo, user, today, today)
    moods = [item for item in items if item.kind == "daily_score" and "Настроение" in item.title]
    assert len(moods) == 1
    assert "отлично" in moods[0].detail
    text = format_timeline(user, today, items)
    assert "😊 Настроение —" in text
    assert "00:00 😊" not in text
    snap = await day_snapshot(repo, user)
    assert snap.scores == {"mood": 5, "energy": 2}
    shown = snap.as_text({"mood", "energy"})
    assert "😊" in shown and "отлично" in shown
    assert "⚡" in shown
    hidden = snap.as_text({"mood"})
    assert "😊" in hidden
    assert "⚡" not in hidden


@pytest.mark.asyncio
async def test_daily_scores_past_day_stats_and_charts(repo):
    user = await repo.create_user(91, "past-sc", "Петя", None, "Europe/Moscow", 0, "23:00")
    today = user_today(user.timezone)
    yesterday = today - timedelta(days=1)
    _, error, _ = await upsert_daily_score(repo, user, yesterday, "wellbeing", 2)
    assert error is None
    _, error, _ = await upsert_daily_score(repo, user, today, "wellbeing", 4)
    assert error is None
    _, error, _ = await upsert_daily_score(repo, user, today, "day_rating", 5)
    assert error is None
    yest = await repo.get_daily_score_by_day(user.telegram_id, yesterday.isoformat(), "wellbeing")
    now = await repo.get_daily_score_by_day(user.telegram_id, today.isoformat(), "wellbeing")
    assert yest is not None and yest.score == 2
    assert now is not None and now.score == 4
    stats = await render_stats(repo, user, yesterday, today, ["wellbeing", "day_rating"])
    assert spec_of("wellbeing").label in stats
    assert "Дней с записью: 2" in stats
    assert spec_of("day_rating").label in stats
    charts = await build_charts(repo, user, yesterday, today, ["wellbeing"])
    assert charts and charts[0][0] == spec_of("wellbeing").label
    mood_charts = await build_charts(repo, user, yesterday, today, ["mood"])
    assert all(title != spec_of("wellbeing").label for title, _ in mood_charts)


@pytest.mark.asyncio
async def test_daily_scores_isolated_between_users(repo):
    a = await repo.create_user(92, "sc-a", "А", None, "UTC", 0, "23:00")
    b = await repo.create_user(93, "sc-b", "Б", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    await upsert_daily_score(repo, a, today, "productivity", 1)
    await upsert_daily_score(repo, b, today, "productivity", 5)
    assert (await repo.get_daily_score_by_day(a.telegram_id, today.isoformat(), "productivity")).score == 1
    assert (await repo.get_daily_score_by_day(b.telegram_id, today.isoformat(), "productivity")).score == 5
    assert await repo.list_daily_scores_for_day(b.telegram_id, today.isoformat()) != []
    rows = await repo.list_daily_scores(
        a.telegram_id,
        "2000-01-01T00:00:00+00:00",
        "2100-01-01T00:00:00+00:00",
    )
    assert rows[0].score == 1


@pytest.mark.asyncio
async def test_daily_score_rejects_bad_kind_and_range(repo):
    user = await repo.create_user(94, "bad-sc", "Вика", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    item_id, error, updated = await upsert_daily_score(repo, user, today, "sleep", 3)
    assert item_id is None and error and updated is False
    item_id, error, updated = await upsert_daily_score(repo, user, today, "mood", 9)
    assert item_id is None and error and updated is False

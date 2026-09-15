from __future__ import annotations

from datetime import timedelta

import pytest

from services.charts import build_charts
from services.daily_scores import parse_daily_score, spec_of
from services.entries import clear_daily_score, undo_entry, upsert_daily_score
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


@pytest.mark.asyncio
async def test_stress_is_sixth_daily_score(repo):
    spec = spec_of("stress")
    assert spec.label == "Стресс"
    assert spec.code == "st"
    user = await repo.create_user(95, "stress-sc", "Кира", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    item_id, error, updated = await upsert_daily_score(repo, user, today, "stress", 2)
    assert error is None and item_id is not None and updated is False
    rec = await repo.get_daily_score(item_id, user.telegram_id)
    assert rec is not None
    assert rec.kind == "stress"
    assert rec.score == 2
    snap = await day_snapshot(repo, user)
    assert snap.scores == {"stress": 2}
    shown = snap.as_text({"stress"})
    assert "😰" in shown
    assert "плохо" in shown


@pytest.mark.asyncio
async def test_clear_daily_score_today_and_past(repo):
    user = await repo.create_user(96, "clear-sc", "Лена", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    yesterday = today - timedelta(days=1)
    today_id, error, _ = await upsert_daily_score(repo, user, today, "mood", 4)
    assert error is None and today_id is not None
    yest_id, error, _ = await upsert_daily_score(repo, user, yesterday, "energy", 2)
    assert error is None and yest_id is not None
    assert await clear_daily_score(repo, user, today, "mood") is None
    assert await repo.get_daily_score(today_id, user.telegram_id) is None
    assert await repo.get_daily_score_by_day(user.telegram_id, today.isoformat(), "mood") is None
    assert await clear_daily_score(repo, user, today, "mood") is None
    assert await clear_daily_score(repo, user, yesterday, "energy") is None
    assert await repo.get_daily_score_by_day(user.telegram_id, yesterday.isoformat(), "energy") is None
    mood_id, error, updated = await upsert_daily_score(repo, user, today, "mood", 5)
    assert error is None and updated is False
    assert await undo_entry(repo, user, "dsc", mood_id) is None
    assert await repo.get_daily_score(mood_id, user.telegram_id) is None
    assert await clear_daily_score(repo, user, today, "sleep") == "Неизвестная оценка."

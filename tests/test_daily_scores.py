from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from services.charts import build_charts
from services.daily_scores import (
    format_open_scores,
    list_open_score_gaps,
    missing_by_day,
    missing_keys_by_day,
    missing_keys_since_first,
    missing_score_count,
    open_score_total,
    open_scores_button_label,
    page_open_scores,
    parse_daily_score,
    score_day_is_due,
    spec_of,
)
from services.entries import clear_daily_score, undo_entry, upsert_daily_score
from services.history import build_timeline, format_timeline
from services.statistics import render_stats
from services.today import day_snapshot
from utils.time import UTC, user_today


def test_score_screen_does_not_share_the_happy_legend_with_stress():
    from handlers.daily_scores import _value_text

    day = date(2026, 9, 23)
    text = _value_text(day, day, [spec_of("mood"), spec_of("stress")], {})
    assert "Нажмите лицо. Ещё раз или ✖️ — снять." in text
    assert "Лица:" not in text
    assert "ужасно" not in text
    assert "слева спокойно, справа сильнее" in text


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
    assert spec.face(1) == "😌"
    assert spec.face(5) == "😫"
    assert spec.word(5) == "очень сильно"
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
    assert "слабо" in shown
    assert "плохо" not in shown


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


def test_missing_by_day_counts_only_open_days():
    keys = ["mood", "energy", "stress"]
    assert missing_score_count({"mood"}, keys) == 2
    assert missing_score_count(set(), []) == 0
    days = [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)]
    pairs = [
        ("2026-08-10", "mood"),
        ("2026-08-10", "energy"),
        ("2026-08-10", "stress"),
        ("2026-08-11", "mood"),
    ]
    assert missing_keys_by_day(pairs, keys, days) == {
        date(2026, 8, 11): ["energy", "stress"],
        date(2026, 8, 12): ["mood", "energy", "stress"],
    }
    assert missing_by_day(pairs, keys, days) == {
        date(2026, 8, 11): 2,
        date(2026, 8, 12): 3,
    }
    assert missing_by_day(pairs, [], days) == {}


def test_missing_since_first_recorded_of_each_kind():
    keys = ["energy", "mood", "stress"]
    pairs = [
        ("2026-08-10", "mood"),
        ("2026-08-12", "mood"),
        ("2026-08-11", "energy"),
    ]
    today = date(2026, 8, 13)
    assert missing_keys_since_first(pairs, keys, today) == {
        date(2026, 8, 11): ["mood"],
        date(2026, 8, 12): ["energy"],
        date(2026, 8, 13): ["energy", "mood"],
    }
    skipped = {("2026-08-13", "mood")}
    assert missing_keys_since_first(pairs, keys, today, skipped)[date(2026, 8, 13)] == ["energy"]
    assert "stress" not in {
        key for left in missing_keys_since_first(pairs, keys, today).values() for key in left
    }
    assert missing_keys_since_first([], keys, today) == {}


def test_open_scores_page_clamps():
    gaps = [(date(2026, 9, day), ["mood"]) for day in range(1, 10)]
    shown, page, pages = page_open_scores(gaps, 0, size=4)
    assert pages == 3
    assert page == 0
    assert len(shown) == 4
    shown, page, pages = page_open_scores(gaps, 9, size=4)
    assert page == 2
    assert len(shown) == 1
    assert page_open_scores([], 3) == ([], 0, 1)


def test_today_waits_for_score_reminder_clock():
    today = date(2026, 9, 23)
    yesterday = today - timedelta(days=1)
    early = datetime(2026, 9, 23, 0, 1, tzinfo=UTC)
    due = datetime(2026, 9, 23, 21, 0, tzinfo=UTC)
    assert not score_day_is_due(today, today, "21:00", early)
    assert score_day_is_due(today, today, "21:00", due)
    assert score_day_is_due(yesterday, today, "21:00", early)
    assert score_day_is_due(today, today, None, early)
    assert score_day_is_due(today, today, "nope", early)


def test_open_scores_text_lists_forgotten_and_button_counts_them():
    today = date(2026, 9, 22)
    gaps = [
        (today, ["energy", "productivity"]),
        (today - timedelta(days=1), ["mood"]),
    ]
    text = format_open_scores(gaps, today)
    assert text.startswith("Не оценено")
    assert "сегодня — ⚡ Энергия, 📈 Продуктивность" in text
    assert "вчера — 😊 Настроение" in text
    assert "Нажмите день" in text
    assert "убирает запись" in text
    assert open_score_total(gaps) == 3
    assert open_scores_button_label(3) == "Неоценено · 3"
    assert open_scores_button_label(0) == "Всё оценено"
    assert format_open_scores([], today) == "Всё оценено.\n\nПустых оценок нет."


@pytest.mark.asyncio
async def test_open_score_gaps_start_at_first_record_and_can_be_dismissed(repo):
    from services.ui_prefs import prefs_of, save_prefs

    user = await repo.create_user(98, "gaps", "Нина", None, "UTC", 0, "23:00")
    prefs = prefs_of(user)
    prefs.tracked = {"mood", "energy", "stress"}
    user = await save_prefs(repo, user, prefs)
    today = user_today(user.timezone)
    await upsert_daily_score(repo, user, today - timedelta(days=20), "mood", 3)
    await upsert_daily_score(repo, user, today - timedelta(days=3), "energy", 4)
    gaps = await list_open_score_gaps(repo, user)
    by_day = dict(gaps)
    assert today - timedelta(days=20) not in by_day
    assert by_day[today - timedelta(days=19)] == ["mood"]
    assert by_day[today - timedelta(days=4)] == ["mood"]
    assert by_day[today - timedelta(days=3)] == ["mood"]
    assert by_day[today - timedelta(days=2)] == ["energy", "mood"]
    assert all("stress" not in keys for _, keys in gaps)
    await repo.add_daily_score_skip(user.telegram_id, (today - timedelta(days=2)).isoformat(), "energy")
    await repo.add_daily_score_skip(user.telegram_id, (today - timedelta(days=2)).isoformat(), "energy")
    gaps = await list_open_score_gaps(repo, user)
    assert dict(gaps)[today - timedelta(days=2)] == ["mood"]


@pytest.mark.asyncio
async def test_open_score_gaps_hide_today_until_reminder(repo, monkeypatch):
    from services.ui_prefs import prefs_of, save_prefs

    registered = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("utils.time.now_utc", lambda: registered)
    monkeypatch.setattr("database.queries.now_utc", lambda: registered)
    user = await repo.create_user(96, "early", "Лена", None, "UTC", 0, "23:00")
    prefs = prefs_of(user)
    prefs.tracked = {"mood", "day_rating"}
    user = await save_prefs(repo, user, prefs)
    await repo.set_daily_score_reminder(user.telegram_id, "21:00")
    await upsert_daily_score(repo, user, date(2026, 9, 21), "mood", 4)
    await upsert_daily_score(repo, user, date(2026, 9, 21), "day_rating", 4)

    early = datetime(2026, 9, 23, 0, 1, tzinfo=UTC)
    monkeypatch.setattr("utils.time.now_utc", lambda: early)
    user = await repo.get_user(user.telegram_id)
    assert user is not None
    gaps = await list_open_score_gaps(repo, user)
    assert gaps == [(date(2026, 9, 22), ["mood", "day_rating"])]

    later = datetime(2026, 9, 23, 21, 0, tzinfo=UTC)
    monkeypatch.setattr("utils.time.now_utc", lambda: later)
    user = await repo.get_user(user.telegram_id)
    assert user is not None
    gaps = await list_open_score_gaps(repo, user)
    assert gaps[0] == (date(2026, 9, 23), ["mood", "day_rating"])
    assert date(2026, 9, 22) in {day for day, _ in gaps}


@pytest.mark.asyncio
async def test_score_kinds_between_follow_local_day(repo):
    user = await repo.create_user(97, "kinds", "Оля", None, "UTC", 0, "23:00")
    await repo.upsert_daily_score(
        user.telegram_id,
        "2026-08-10",
        "mood",
        4,
        "2026-08-12T00:00:00+00:00",
    )
    pairs = await repo.list_daily_score_kinds_between(user.telegram_id, "2026-08-10", "2026-08-10")
    assert pairs == [("2026-08-10", "mood")]
    assert await repo.list_daily_score_kinds_between(user.telegram_id, "2026-08-12", "2026-08-12") == []

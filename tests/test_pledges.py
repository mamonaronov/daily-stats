from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from services.history import build_timeline
from services.pledges import (
    close_next_pledge,
    close_open_pledge,
    close_pledge,
    pledge_card_text,
    progress_for,
    scheduled_dates,
    undo_last_pledge,
)
from services.statistics import choices_with_data, load_period, render_stats
from services.today import day_snapshot
from utils.time import user_today


def _metric(start: date, end: date | None, weekdays: int = 127, name: str = "Чтение"):
    return SimpleNamespace(
        id=1,
        name=name,
        data_type="pledge",
        enabled=1,
        starts_on=start.isoformat(),
        ends_on=None if end is None else end.isoformat(),
        weekdays=weekdays,
        unit=None,
        choices_json=None,
        pinned=0,
    )


def test_weekdays_skip_unselected_days_and_stop_at_today():
    start = date(2026, 9, 20)
    end = date(2026, 9, 30)
    today = date(2026, 9, 22)
    mask = (1 << 0) | (1 << 2)
    assert scheduled_dates(start, end, mask, until=today) == [date(2026, 9, 21)]
    later = scheduled_dates(start, end, mask, until=end)
    assert date(2026, 9, 23) in later
    assert date(2026, 9, 22) not in later


def test_open_dates_are_the_unclosed_prefix_through_today():
    start = date(2026, 9, 20)
    today = date(2026, 9, 22)
    metric = _metric(start, date(2026, 9, 30))
    progress = progress_for(metric, [], today, "UTC")
    assert progress.open_dates == (date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22))
    assert progress.next_future == date(2026, 9, 23)
    card = pledge_card_text(metric, progress)
    assert "20 сентября" in card
    assert "«Отметить 20 сентября»" in card


def test_future_start_has_nothing_to_close():
    today = date(2026, 9, 22)
    metric = _metric(today + timedelta(days=3), None)
    progress = progress_for(metric, [], today, "UTC")
    assert progress.open_dates == ()
    assert "Начнётся" in pledge_card_text(metric, progress)


@pytest.mark.asyncio
async def test_creating_pledge_returns_to_the_list(repo):
    from handlers.custom_metrics import _finish_create

    class State:
        async def clear(self) -> None:
            return None

    class Msg:
        def __init__(self) -> None:
            self.edits: list[tuple] = []

        async def edit_text(self, text, reply_markup=None, **kwargs):
            self.edits.append((text, reply_markup))
            return self

    class Cb:
        def __init__(self, message) -> None:
            self.message = message
            self.answered: list[tuple] = []

        async def answer(self, *args, **kwargs):
            self.answered.append((args, kwargs))

    user = await repo.create_user(85, "u", "U", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    msg = Msg()
    cb = Cb(msg)
    await _finish_create(
        cb,
        State(),
        repo,
        user,
        "Чтение",
        "pledge",
        None,
        None,
        starts_on=today.isoformat(),
        ends_on=None,
        weekdays=127,
    )
    assert cb.answered
    assert msg.edits
    text, markup = msg.edits[0]
    assert "Хорошие решения" in text
    assert "Чтение" in text
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "Чтение" in labels
    assert "➕ Создать решение" in labels
    assert "⬅️ К решениям" not in labels


@pytest.mark.asyncio
async def test_close_only_in_order_and_not_ahead(repo):
    user = await repo.create_user(81, "u", "U", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    start = today - timedelta(days=2)
    end = today + timedelta(days=4)
    metric_id = await repo.add_metric(
        user.telegram_id,
        "Чтение",
        "pledge",
        None,
        None,
        starts_on=start.isoformat(),
        ends_on=end.isoformat(),
        weekdays=127,
    )
    metric = await repo.get_metric(metric_id, user.telegram_id)
    assert metric is not None

    _, error = await close_pledge(repo, user, metric, [today], today=today)
    assert error == "Сначала закройте более раннюю дату."
    _, error = await close_pledge(repo, user, metric, [today + timedelta(days=1)], today=today)
    assert error == "Эту дату ещё нельзя закрыть."

    days, error = await close_next_pledge(repo, user, metric, today=today)
    assert error is None
    assert days == [start]
    days, error = await close_open_pledge(repo, user, metric, today=today)
    assert error is None
    assert days == [start + timedelta(days=1), today]
    _, error = await close_next_pledge(repo, user, metric, today=today)
    assert error == "До сегодня всё закрыто."

    marks = [rec for rec in await repo.list_pledge_values(user.telegram_id) if rec.metric_id == metric_id]
    assert len(marks) == 3
    progress = progress_for(metric, marks, today, "UTC")
    assert today + timedelta(days=1) not in progress.closed_slots
    assert progress.next_future == today + timedelta(days=1)


@pytest.mark.asyncio
async def test_undo_removes_only_the_latest_closed_date(repo):
    user = await repo.create_user(82, "u", "U", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    start = today - timedelta(days=2)
    metric_id = await repo.add_metric(
        user.telegram_id,
        "Чтение",
        "pledge",
        None,
        None,
        starts_on=start.isoformat(),
        ends_on=(today + timedelta(days=2)).isoformat(),
    )
    metric = await repo.get_metric(metric_id, user.telegram_id)
    assert metric is not None
    await close_open_pledge(repo, user, metric, today=today)
    removed, error = await undo_last_pledge(repo, user, metric, today=today)
    assert error is None
    assert removed == today
    progress = progress_for(
        metric,
        [rec for rec in await repo.list_pledge_values(user.telegram_id) if rec.metric_id == metric_id],
        today,
        "UTC",
    )
    assert progress.open_dates == (today,)
    assert progress.last_closed == today - timedelta(days=1)
    removed, error = await undo_last_pledge(repo, user, metric, today=today)
    assert removed == today - timedelta(days=1)
    assert error is None


@pytest.mark.asyncio
async def test_weekday_pledge_does_not_close_an_off_day(repo):
    user = await repo.create_user(83, "u", "U", None, "UTC", 0, "23:00")
    today = date(2026, 9, 22)
    metric_id = await repo.add_metric(
        user.telegram_id,
        "Чтение",
        "pledge",
        None,
        None,
        starts_on="2026-09-20",
        ends_on="2026-09-30",
        weekdays=(1 << 0) | (1 << 2),
    )
    metric = await repo.get_metric(metric_id, user.telegram_id)
    assert metric is not None
    days, error = await close_open_pledge(repo, user, metric, today=today)
    assert error is None
    assert days == [date(2026, 9, 21)]
    _, error = await close_next_pledge(repo, user, metric, today=today)
    assert error == "До сегодня всё закрыто."


@pytest.mark.asyncio
async def test_today_history_and_stats_show_open_dates(repo):
    user = await repo.create_user(84, "u", "U", None, "UTC", 0, "23:00")
    today = user_today("UTC")
    metric_id = await repo.add_metric(
        user.telegram_id,
        "Чтение",
        "pledge",
        None,
        None,
        starts_on=(today - timedelta(days=1)).isoformat(),
        ends_on=(today + timedelta(days=2)).isoformat(),
    )
    metric = await repo.get_metric(metric_id, user.telegram_id)
    assert metric is not None
    await close_next_pledge(repo, user, metric, today=today)
    snap = await day_snapshot(repo, user)
    text = "\n".join(snap.pledge_lines)
    assert "Чтение" in text
    assert "открыто" in text
    assert "Чтение" not in "\n".join(snap.custom_lines)

    items = await build_timeline(repo, user, today - timedelta(days=1), today)
    assert any(item.detail == "закрыто" and item.extra.get("all_day") for item in items)

    data = await load_period(repo, user, today - timedelta(days=1), today)
    keys, custom = choices_with_data(data)
    assert f"m{metric_id}" in keys
    assert custom[0].name == "Чтение"
    stats = await render_stats(repo, user, today - timedelta(days=1), today, [f"m{metric_id}"])
    assert "В периоде закрыто 1 из 2" in stats
    assert "открыто" in stats

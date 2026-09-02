from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from handlers.common import prompt_with_hint
from handlers.sleep import bed_times_hint, onset_prompt_text
from services.entries import (
    add_sleep_onset,
    add_sleep_phone_away,
    add_sleep_phone_in,
    add_sleep_up,
    add_sleep_wake,
    add_sleep_wake_and_up,
)


@pytest.mark.asyncio
async def test_sleep_phone_then_away_then_wake_up_and_onset(repo):
    user = await repo.create_user(31, "s", "S", None, "UTC", 0, "23:00")
    phone = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    away = datetime(2026, 8, 16, 20, 40, tzinfo=timezone.utc)
    wake = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)
    onset = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)

    item_id, error = await add_sleep_phone_in(repo, user, phone)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    assert rec.phase() == "with_phone"

    _, error = await add_sleep_phone_away(repo, user, away)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "no_phone"
    assert rec.phone_away_at is not None

    _, error = await add_sleep_wake_and_up(repo, user, wake, quality=5)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "need_onset"
    assert rec.wake_time is not None
    assert rec.out_of_bed_at == rec.wake_time
    assert rec.quality == 5
    assert rec.duration_minutes is None

    _, error = await add_sleep_onset(repo, user, onset)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "idle"
    assert rec.duration_minutes == 7 * 60


@pytest.mark.asyncio
async def test_sleep_onset_after_wake_without_getting_up(repo):
    user = await repo.create_user(34, "s", "S", None, "UTC", 0, "23:00")
    bed = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    wake = datetime(2026, 8, 17, 6, 0, tzinfo=timezone.utc)
    onset = datetime(2026, 8, 16, 22, 0, tzinfo=timezone.utc)

    item_id, error = await add_sleep_phone_away(repo, user, bed)
    assert error is None
    _, error = await add_sleep_wake(repo, user, wake, quality=4)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "awake"
    assert rec.sleep_onset_at is None

    _, error = await add_sleep_onset(repo, user, onset)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.sleep_onset_at is not None
    assert rec.out_of_bed_at is None
    assert rec.phase() == "awake"
    assert rec.duration_minutes == 8 * 60


@pytest.mark.asyncio
async def test_bedtime_after_wake_attaches_when_earlier(repo):
    user = await repo.create_user(35, "s", "S", None, "UTC", 0, "23:00")
    wake = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
    bed = datetime(2026, 8, 16, 23, 0, tzinfo=timezone.utc)

    wake_id, error = await add_sleep_wake(repo, user, wake, quality=4)
    assert error is None
    rec = await repo.get_sleep(wake_id, user.telegram_id)
    assert rec.phase() == "awake"
    assert rec.phone_in_bed_at is None

    same_id, error = await add_sleep_phone_in(repo, user, bed)
    assert error is None
    assert same_id == wake_id
    rec = await repo.get_sleep(wake_id, user.telegram_id)
    assert rec.phone_in_bed_at is not None
    assert rec.wake_time is not None
    assert rec.quality == 4


@pytest.mark.asyncio
async def test_nophone_after_wake_and_up_attaches_when_earlier(repo):
    user = await repo.create_user(36, "s", "S", None, "UTC", 0, "23:00")
    wake = datetime(2026, 8, 17, 7, 30, tzinfo=timezone.utc)
    bed = datetime(2026, 8, 16, 22, 15, tzinfo=timezone.utc)

    wake_id, error = await add_sleep_wake_and_up(repo, user, wake, quality=5)
    assert error is None
    same_id, error = await add_sleep_phone_away(repo, user, bed)
    assert error is None
    assert same_id == wake_id
    rec = await repo.get_sleep(wake_id, user.telegram_id)
    assert rec.phone_away_at is not None
    assert rec.out_of_bed_at == rec.wake_time
    assert rec.phase() == "need_onset"


@pytest.mark.asyncio
async def test_evening_bedtime_after_wake_starts_new_night(repo):
    user = await repo.create_user(37, "s", "S", None, "UTC", 0, "23:00")
    wake = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 17, 23, 0, tzinfo=timezone.utc)

    first_id, error = await add_sleep_wake(repo, user, wake, quality=3)
    assert error is None
    second_id, error = await add_sleep_phone_in(repo, user, evening)
    assert error is None
    assert second_id != first_id
    old = await repo.get_sleep(first_id, user.telegram_id)
    new = await repo.get_sleep(second_id, user.telegram_id)
    assert old.phone_in_bed_at is None
    assert new.phase() == "with_phone"


@pytest.mark.asyncio
async def test_undo_bedtime_keeps_wake_record(repo):
    from services.entries import undo_entry

    user = await repo.create_user(38, "u", "U", None, "UTC", 0, "23:00")
    wake = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
    bed = datetime(2026, 8, 16, 23, 0, tzinfo=timezone.utc)
    item_id, error = await add_sleep_wake(repo, user, wake, quality=4)
    assert error is None
    _, error = await add_sleep_phone_in(repo, user, bed)
    assert error is None
    assert await undo_entry(repo, user, "sp", item_id) is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    assert rec.wake_time is not None
    assert rec.phone_in_bed_at is None
    assert rec.bedtime is None


@pytest.mark.asyncio
async def test_new_night_leaves_previous_open(repo):
    user = await repo.create_user(32, "s", "S", None, "UTC", 0, "23:00")
    first = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    wake = datetime(2026, 8, 17, 5, 0, tzinfo=timezone.utc)
    second = datetime(2026, 8, 17, 22, 0, tzinfo=timezone.utc)

    first_id, error = await add_sleep_phone_away(repo, user, first)
    assert error is None
    _, error = await add_sleep_wake(repo, user, wake, quality=3)
    assert error is None
    rec = await repo.get_sleep(first_id, user.telegram_id)
    assert rec.phase() == "awake"

    second_id, error = await add_sleep_phone_in(repo, user, second)
    assert error is None
    assert second_id != first_id
    old = await repo.get_sleep(first_id, user.telegram_id)
    new = await repo.get_sleep(second_id, user.telegram_id)
    assert old.out_of_bed_at is None
    assert new.phase() == "with_phone"


@pytest.mark.asyncio
async def test_migrated_bedtime_is_no_phone_phase(repo):
    user = await repo.create_user(33, "s", "S", None, "UTC", 0, "23:00")
    item_id = await repo.add_sleep(user.telegram_id, bedtime="2026-08-16T20:00:00+00:00")
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    await repo.update_sleep(
        item_id,
        user.telegram_id,
        phone_away_at=rec.bedtime,
    )
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phase() == "no_phone"


@pytest.mark.asyncio
async def test_undo_cigarette_deletes_record(repo):
    from services.entries import add_cigarette, undo_entry

    user = await repo.create_user(41, "u", "U", None, "UTC", 0, "23:00")
    when = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    item_id, error = await add_cigarette(repo, user, when)
    assert error is None
    assert await repo.get_cigarette(item_id, user.telegram_id) is not None
    assert await undo_entry(repo, user, "cig", item_id) is None
    assert await repo.get_cigarette(item_id, user.telegram_id) is None


@pytest.mark.asyncio
async def test_undo_sleep_away_keeps_the_night(repo):
    from services.entries import add_sleep_phone_away, add_sleep_phone_in, undo_entry

    user = await repo.create_user(42, "u", "U", None, "UTC", 0, "23:00")
    phone = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    away = datetime(2026, 8, 16, 20, 40, tzinfo=timezone.utc)
    item_id, error = await add_sleep_phone_in(repo, user, phone)
    assert error is None
    _, error = await add_sleep_phone_away(repo, user, away)
    assert error is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec.phone_away_at is not None
    assert await undo_entry(repo, user, "sa", item_id) is None
    rec = await repo.get_sleep(item_id, user.telegram_id)
    assert rec is not None
    assert rec.phone_away_at is None
    assert rec.phone_in_bed_at is not None


@pytest.mark.asyncio
async def test_undo_snus_finish_reopens_pack(repo):
    from services.entries import add_snus_bought, add_snus_finished, undo_entry

    user = await repo.create_user(43, "u", "U", None, "UTC", 0, "23:00")
    bought = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    item_id, error = await add_snus_bought(repo, user, bought)
    assert error is None
    _, error = await add_snus_finished(repo, user, finished)
    assert error is None
    rec = await repo.get_snus_pack(item_id, user.telegram_id)
    assert rec.finished_at is not None
    assert await undo_entry(repo, user, "snf", item_id) is None
    rec = await repo.get_snus_pack(item_id, user.telegram_id)
    assert rec is not None
    assert rec.finished_at is None
    assert rec.duration_minutes is None


def _user_tz(name: str = "UTC"):
    return SimpleNamespace(timezone=name)


def test_onset_prompt_shows_phone_in_and_away():
    rec = SimpleNamespace(
        phone_in_bed_at="2026-08-16T20:00:00+00:00",
        phone_away_at="2026-08-16T20:40:00+00:00",
        bedtime="2026-08-16T20:00:00+00:00",
    )
    text = onset_prompt_text(_user_tz(), rec)
    assert text.startswith("Когда заснули?")
    assert "Лёг с телефоном: 16 августа 20:00" in text
    assert "Без телефона: 16 августа 20:40" in text
    assert "Лёг без телефона" not in text


def test_onset_prompt_nophone_only():
    rec = SimpleNamespace(
        phone_in_bed_at=None,
        phone_away_at="2026-08-16T21:15:00+00:00",
        bedtime="2026-08-16T21:15:00+00:00",
    )
    hint = bed_times_hint(_user_tz(), rec)
    assert hint == "Лёг без телефона: 16 августа 21:15"
    assert "Лёг с телефоном" not in onset_prompt_text(_user_tz(), rec)


def test_onset_prompt_phone_only_and_empty():
    rec = SimpleNamespace(
        phone_in_bed_at="2026-08-16T22:00:00+00:00",
        phone_away_at=None,
        bedtime="2026-08-16T22:00:00+00:00",
    )
    assert bed_times_hint(_user_tz(), rec) == "Лёг с телефоном: 16 августа 22:00"
    assert onset_prompt_text(_user_tz(), None) == "Когда заснули?"
    assert bed_times_hint(_user_tz(), None) == ""


def test_onset_prompt_legacy_bedtime():
    rec = SimpleNamespace(phone_in_bed_at=None, phone_away_at=None, bedtime="2026-08-16T23:00:00+00:00")
    assert bed_times_hint(_user_tz(), rec) == "Лёг спать: 16 августа 23:00"


def test_prompt_with_hint_appends_bed_times():
    hint = "Лёг с телефоном: 16 августа 20:00\nБез телефона: 16 августа 20:40"
    text = prompt_with_hint("Выберите час — можно уже прошедший:", {"time_hint": hint})
    assert text.startswith("Выберите час")
    assert "Лёг с телефоном: 16 августа 20:00" in text
    assert prompt_with_hint("Когда заснули?", {}) == "Когда заснули?"

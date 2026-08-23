from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from services.entries import add_cigarette
from services.spam_watch import (
    KIND_BUTTONS,
    KIND_DAILY,
    KIND_ROWS,
    KIND_WRITES,
    AbuseSnapshot,
    SpamWatch,
    format_spam_alert,
    set_spam_watch,
)
from tests.conftest import make_config
from tests.test_lifecycle import _FakeBot


def _watch(repo, tmp_path, bot=None, coalesce=0.0, **fields) -> tuple[SpamWatch, object]:
    config = replace(
        make_config(tmp_path),
        owner_id=1,
        spam_button_window_seconds=8,
        spam_button_count=5,
        spam_write_window_seconds=60,
        spam_write_count=4,
        spam_daily_entries=3,
        spam_user_rows=5,
        spam_alert_cooldown_minutes=30,
        **fields,
    )
    bot = bot or _FakeBot()
    watch = SpamWatch(bot, repo, config, coalesce=coalesce)
    return watch, bot


@pytest.mark.asyncio
async def test_button_spam_notifies_owner_without_blocking(repo, tmp_path):
    user = await repo.create_user(2, "spammer", "Вася", None, "UTC", 0, "23:00")
    watch, bot = _watch(repo, tmp_path)
    for _ in range(5):
        watch.note_button(user.telegram_id, "cig:now", username="spammer", first_name="Вася", db_user=user)
    await watch.drain()
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "Пользователь <b>не ограничен</b>" in text
    assert "спам кнопок" in text
    assert "cig:now" in text
    assert "Вася" in text
    assert str(user.telegram_id) in text
    markup = bot.sent[0]["reply_markup"]
    datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert f"ad:u:{user.telegram_id}" in datas
    assert f"ad:bn:{user.telegram_id}" in datas


@pytest.mark.asyncio
async def test_owner_button_spam_is_ignored(repo, tmp_path):
    watch, bot = _watch(repo, tmp_path)
    for _ in range(20):
        watch.note_button(1, "n:m", first_name="Owner")
    await watch.drain()
    assert bot.sent == []


@pytest.mark.asyncio
async def test_write_burst_and_daily_and_rows_alert(repo, tmp_path):
    user = await repo.create_user(2, "spammer", "Вася", None, "UTC", 0, "23:00")
    watch, bot = _watch(repo, tmp_path, coalesce=0.2)
    set_spam_watch(watch)
    try:
        when = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        for _ in range(5):
            item_id, error = await add_cigarette(repo, user, when)
            assert error is None
            assert item_id is not None
        await watch.drain()
    finally:
        set_spam_watch(None)
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "пачка записей за короткое время" in text
    assert "нереалистично много записей за день" in text
    assert "много строк дневника в базе" in text
    assert "сигарета" in text
    assert "23 августа 2026 (UTC): 5" in text
    assert await repo.count_user_entries(user.telegram_id) == 5


@pytest.mark.asyncio
async def test_alert_cooldown_suppresses_same_reason(repo, tmp_path):
    user = await repo.create_user(2, "spammer", "Вася", None, "UTC", 0, "23:00")
    watch, bot = _watch(repo, tmp_path)
    for _ in range(5):
        watch.note_button(user.telegram_id, "n:m", db_user=user)
    await watch.drain()
    for _ in range(5):
        watch.note_button(user.telegram_id, "n:m", db_user=user)
    await watch.drain()
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_count_user_entries_between_is_scoped(repo):
    a = await repo.create_user(2, "a", "A", None, "UTC", 0, "23:00")
    b = await repo.create_user(3, "b", "B", None, "UTC", 0, "23:00")
    await repo.add_cigarette(a.telegram_id, "2026-08-23T10:00:00+00:00")
    await repo.add_cigarette(a.telegram_id, "2026-08-24T10:00:00+00:00")
    await repo.add_cigarette(b.telegram_id, "2026-08-23T11:00:00+00:00")
    await repo.add_snus_pack(a.telegram_id, "2026-08-23T09:00:00+00:00", None, None)
    n = await repo.count_user_entries_between(
        a.telegram_id,
        "2026-08-23T00:00:00+00:00",
        "2026-08-24T00:00:00+00:00",
    )
    assert n == 2
    assert await repo.count_user_entries(a.telegram_id) == 3


def test_format_spam_alert_escapes_html():
    text = format_spam_alert(
        AbuseSnapshot(
            telegram_id=9,
            reasons=[KIND_BUTTONS, KIND_WRITES, KIND_DAILY, KIND_ROWS],
            name="A<b>",
            username="x<y>",
            status="active",
            balance_text="10 ₽",
            timezone="UTC",
            button_count=20,
            button_window=8,
            button_limit=20,
            last_callback="cig:now",
            write_count=15,
            write_window=60,
            write_limit=15,
            last_action="сигарета",
            daily_count=80,
            daily_limit=80,
            daily_label="23 августа 2026 (UTC)",
            total_rows=5000,
            row_limit=5000,
        )
    )
    assert "A&lt;b&gt;" in text
    assert "@x&lt;y&gt;" in text
    assert "A<b>" not in text
    assert "не ограничен" in text

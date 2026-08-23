from __future__ import annotations

from pathlib import Path

import pytest
from aiogram.fsm.storage.base import StorageKey

from database.fsm_storage import SqliteStorage
from handlers.common import HOW_TO, TZ_PROMPT, menu_text
from services.charts import build_charts
from services.export import export_user_csv
from services.history import PAGE_SIZE, build_timeline, paginate
from services.notices import send_coverage_notices
from services.paid import report_payment
from services.statistics import render_stats
from services.today import day_snapshot, sleep_status_line
from services.ui_prefs import MAX_PINS, prefs_of, save_prefs, toggle_hidden
from tests.conftest import make_config
from utils.time import now_utc, to_iso, user_today


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs.get("reply_markup")))
        return None


@pytest.mark.asyncio
async def test_today_snapshot_and_menu_text(repo):
    user = await repo.create_user(10, "ann", "Анна", None, "UTC", 10, "23:00")
    stamp = to_iso(now_utc())
    await repo.add_cigarette(user.telegram_id, stamp)
    await repo.add_cigarette(user.telegram_id, stamp)
    await repo.add_snus_pack(user.telegram_id, stamp, None, None)
    await repo.add_sleep(user.telegram_id, phone_in_bed_at=stamp)

    user = await repo.get_user(10)
    snap = await day_snapshot(repo, user)
    assert snap.cigarettes == 2
    assert snap.snus_line.startswith("открыта с")
    assert snap.sleep_line == "лёг"
    assert sleep_status_line(None) == "нет записи"

    text = menu_text(user, make_config(Path("/tmp")), snap.as_text())
    assert "Сегодня" in text
    assert "🚬 2" in text
    assert "лёг" in text


def test_tz_prompt_does_not_promise_reminders():
    blob = f"{TZ_PROMPT}\n{HOW_TO}".lower()
    assert "напоминан" not in blob


def test_bot_commands_exist():
    src = Path("handlers/start.py").read_text(encoding="utf-8")
    assert 'Command("menu")' in src
    assert 'Command("today")' in src
    assert 'Command("stats")' in src


@pytest.mark.asyncio
async def test_history_pagination_keeps_the_tail(repo):
    user = await repo.create_user(11, "bob", "Боб", None, "UTC", 10, "23:00")
    stamp = to_iso(now_utc())
    for _ in range(PAGE_SIZE + 3):
        await repo.add_cigarette(user.telegram_id, stamp)
    today = user_today(user.timezone)
    items = await build_timeline(repo, user, today, today)
    assert len(items) == PAGE_SIZE + 3
    page0, page, pages = paginate(items, 0)
    assert (page, pages, len(page0)) == (0, 2, PAGE_SIZE)
    page1, page, _pages = paginate(items, 1)
    assert page == 1
    assert len(page1) == 3
    assert {item.id for item in page0}.isdisjoint({item.id for item in page1})


@pytest.mark.asyncio
async def test_stats_and_charts_use_only_selected_custom(repo):
    user = await repo.create_user(12, "kat", "Катя", None, "UTC", 10, "23:00")
    water = await repo.add_metric(user.telegram_id, "Вода", "number", "мл", None)
    weight = await repo.add_metric(user.telegram_id, "Вес", "number", "кг", None)
    stamp = to_iso(now_utc())
    await repo.add_metric_value(user.telegram_id, water, stamp, value_number=250)
    await repo.add_metric_value(user.telegram_id, weight, stamp, value_number=70)
    today = user_today(user.timezone)
    selected = [f"m{water}"]
    text = await render_stats(repo, user, today, today, selected)
    assert "Вода" in text
    assert "Вес" not in text
    charts = await build_charts(repo, user, today, today, selected)
    titles = [title for title, _png in charts]
    assert any("Вода" in title for title in titles)
    assert all("Вес" not in title for title in titles)


@pytest.mark.asyncio
async def test_csv_export_is_isolated(repo):
    a = await repo.create_user(21, "a", "Аня", None, "UTC", 10, "23:00")
    b = await repo.create_user(22, "b", "Боря", None, "UTC", 10, "23:00")
    stamp = to_iso(now_utc())
    await repo.add_cigarette(a.telegram_id, stamp)
    await repo.add_cigarette(b.telegram_id, stamp)
    today = user_today("UTC")
    filename, body = await export_user_csv(repo, a, today, today)
    assert filename.endswith(".csv")
    assert "Сигарета" in body
    assert str(b.telegram_id) not in body
    rows = [line for line in body.splitlines() if line.strip()]
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_report_payment_notifies_owner_without_changing_balance(repo, tmp_path):
    config = make_config(tmp_path)
    user = await repo.create_user(42, "payer", "Плательщик", None, "UTC", 10, "23:00")
    await repo.apply_balance_change(42, "credit", delta=50, comment="seed", performed_by=1)
    user = await repo.get_user(42)
    assert user.balance == pytest.approx(50)
    bot = FakeBot()
    await report_payment(bot, config, user, "300")
    user = await repo.get_user(42)
    assert user.balance == pytest.approx(50)
    assert bot.sent
    chat_id, text, markup = bot.sent[0]
    assert chat_id == config.owner_id
    assert "42" in text
    assert "300" in text
    assert markup is not None


@pytest.mark.asyncio
async def test_hidden_types_and_pin_limit(repo):
    user = await repo.create_user(30, "d", "Дима", None, "UTC", 10, "23:00")
    prefs = prefs_of(user)
    assert prefs.hidden == set()
    prefs = toggle_hidden(prefs, "caffeine")
    user = await save_prefs(repo, user, prefs)
    assert "caffeine" in prefs_of(user).hidden

    ids = []
    for name in ("Вода", "Шаги", "Вес"):
        ids.append(await repo.add_metric(user.telegram_id, name, "number", None, None))
    for metric_id in ids:
        await repo.update_metric(metric_id, user.telegram_id, pinned=1)
    metrics = await repo.list_metrics(user.telegram_id, enabled_only=True)
    pinned = [item for item in metrics if item.pinned]
    assert len(pinned) == 3
    assert MAX_PINS == 3


@pytest.mark.asyncio
async def test_sqlite_fsm_roundtrip(repo):
    storage = SqliteStorage(repo.db)
    key = StorageKey(bot_id=1, chat_id=7, user_id=7)
    await storage.set_state(key, "diary:amount")
    await storage.set_data(key, {"drink_type": "coffee", "amount": 250})
    assert await storage.get_state(key) == "diary:amount"
    data = await storage.get_data(key)
    assert data["drink_type"] == "coffee"
    assert data["amount"] == 250


@pytest.mark.asyncio
async def test_coverage_notice_once_a_day(repo, tmp_path):
    config = make_config(tmp_path)
    await repo.create_user(55, "low", "Маша", None, "UTC", 10, "23:00")
    await repo.apply_balance_change(55, "credit", delta=25, comment="seed", performed_by=1)
    bot = FakeBot()
    await send_coverage_notices(repo, bot, config)
    targeted = [item for item in bot.sent if item[0] == 55]
    assert targeted
    await send_coverage_notices(repo, bot, config)
    targeted_again = [item for item in bot.sent if item[0] == 55]
    assert len(targeted_again) == 1

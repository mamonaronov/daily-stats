from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from database.database import Database, is_managed_sqlite_backup
from services.click_charts import build_click_charts
from services.click_stats import (
    admin_click_summary_lines,
    classify_button,
    click_window,
    kind_label,
    record_callback_click,
    render_click_journal,
    render_click_report,
    render_user_click_log,
    ux_kind_share,
)
from tests.conftest import make_config
from utils.time import to_iso


def test_classify_button_kinds():
    assert classify_button("e:cig") == "cigarettes"
    assert classify_button("cig:now") == "cigarettes"
    assert classify_button("n:m") == "menu"
    assert classify_button("n:a") == "admin"
    assert classify_button("ad:clk") == "admin"
    assert classify_button("adclk:7") == "admin"
    assert classify_button("adclkj:today") == "admin"
    assert classify_button("stp:yesterday") == "stats"
    assert classify_button("stp:all") == "stats"
    assert classify_button("stp:since") == "stats"
    assert classify_button("stp:marker") == "stats"
    assert classify_button("stre:0") == "stats"
    assert classify_button("stmk:12") == "stats"
    assert classify_button("stmkp:1") == "stats"
    assert classify_button("hist:since") == "history"
    assert classify_button("hmk:12") == "history"
    assert classify_button("hmkp:0") == "history"
    assert classify_button("stp:q:10000") == "steps"
    assert classify_button("e:ds") == "daily_scores"
    assert classify_button("ds:q:md:4") == "daily_scores"
    assert classify_button("ds:x:md") == "daily_scores"
    assert classify_button("dscal:2026-09-02") == "daily_scores"
    assert classify_button("slp:wake") == "sleep"
    assert classify_button("slk:self") == "sleep"
    assert classify_button("slq:4") == "sleep"
    assert classify_button("slb:now") == "sleep"
    assert classify_button("sln:time") == "sleep"
    assert classify_button("mkt:yesterday") == "markers"
    assert classify_button("mkt:date") == "markers"
    assert classify_button("mk:new") == "markers"
    assert classify_button("cm:add:3") == "custom"
    assert classify_button("n:pl") == "pledges"
    assert classify_button("pl:new") == "pledges"
    assert classify_button("cm:pn:3") == "pledges"
    assert classify_button("noop") == "unknown"
    assert classify_button("") == "unknown"
    assert kind_label("cigarettes") == "Сигареты"


def test_admin_clicks_kb_callback_limit():
    from keyboards.main import admin_clicks_kb, admin_root_kb

    root = [(btn.text, btn.callback_data) for row in admin_root_kb().inline_keyboard for btn in row]
    assert ("🖱 Нажатия", "ad:clk") in root
    kb = admin_clicks_kb("7")
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "[7 дней]" in labels
    assert "Сегодня" in labels
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "adclk:7" in datas
    assert "adclkc:7" in datas
    assert "adclkj:7" in datas
    assert all(data and len(data.encode()) <= 64 for data in datas)
    from keyboards.main import admin_user_kb

    user_datas = [btn.callback_data for row in admin_user_kb(42).inline_keyboard for btn in row]
    assert "ad:uclk:42" in user_datas
    assert all(data and len(data.encode()) <= 64 for data in user_datas)


def test_backup_list_skips_clicks_and_vpn(tmp_path):
    vpn = tmp_path / "vpn.sqlite3"
    clicks = tmp_path / "clicks.sqlite3"
    load = tmp_path / "load.sqlite3"
    real = tmp_path / "backup_20260902_120000.sqlite3"
    for path in (vpn, clicks, load, real):
        path.write_bytes(b"sqlite")
    assert is_managed_sqlite_backup(vpn) is False
    assert is_managed_sqlite_backup(clicks) is False
    assert is_managed_sqlite_backup(load) is False
    assert is_managed_sqlite_backup(real) is True


@pytest.mark.asyncio
async def test_clicks_db_is_separate_and_records_user_vs_owner(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    try:
        from database.queries import Repo

        repo = Repo(db)
        assert db.clicks_db is not None
        assert db.clicks_db.path == config.clicks_db_path
        assert config.clicks_db_path.exists()
        owner_cb = _callback(1, "n:a", "🛠 Админ-панель")
        user_cb = _callback(2, "e:cig", "🚬 Сигарета")
        await record_callback_click(repo, config, owner_cb)
        await record_callback_click(repo, config, user_cb)
        await record_callback_click(repo, config, _callback(2, "noop", " "))
        stats = await db.clicks_db.overview("2000-01-01T00:00:00+00:00")
        assert stats["users_total"] == 1
        assert stats["owner_total"] == 1
        assert stats["last_user"]["telegram_id"] == 2
        assert stats["last_user"]["button_kind"] == "cigarettes"
        async with db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='button_clicks'"
        ) as cur:
            assert await cur.fetchone() is None
        backup = await db.backup(prefix="clicktest")
        import sqlite3

        conn = sqlite3.connect(backup)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='button_clicks'"
            ).fetchone()
            assert row is None
        finally:
            conn.close()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_admin_summary_uses_owner_timezone_not_utc(repo):
    await repo.create_user(1, "owner", "Хозяин", None, "Europe/Moscow", 0, "23:00")
    await repo.create_user(2, "u", "Иван", None, "UTC", 0, "23:00")
    clicks = repo.db.clicks_db
    assert clicks is not None
    # 23:30 UTC on 1 Sep = 02:30 Moscow on 2 Sep
    await clicks.record(
        telegram_id=1,
        clicked_at="2026-09-01T23:30:00+00:00",
        button_kind="admin",
        callback_data="n:a",
        button_text="Админка",
        is_owner=True,
    )
    await clicks.record(
        telegram_id=2,
        clicked_at="2026-09-02T10:15:00+00:00",
        button_kind="menu",
        callback_data="n:m",
        button_text="Меню",
        is_owner=False,
    )
    lines = await admin_click_summary_lines(
        repo,
        "Europe/Moscow",
        now=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    )
    text = "\n".join(lines)
    assert "Нажатий пользователей: 1" in text
    assert "сегодня 1" in text
    assert "всего 1" in text
    assert "2 сентября 2026, 13:15:00" in text
    assert "Иван" in text
    assert "Меню" in text
    assert "n:m" in text
    assert "T10:15:00" not in text
    assert "+00:00" not in text
    assert "2026-09-02T" not in text


@pytest.mark.asyncio
async def test_click_report_and_charts_exclude_owner(repo):
    await repo.create_user(9, "ivan", "Иван", None, "UTC", 0, "23:00")
    clicks = repo.db.clicks_db
    assert clicks is not None
    await clicks.record(
        telegram_id=1,
        clicked_at="2026-09-02T08:00:00+00:00",
        button_kind="admin",
        callback_data="ad:clk",
        button_text="Нажатия",
        is_owner=True,
    )
    await clicks.record(
        telegram_id=9,
        clicked_at="2026-09-02T09:00:00+00:00",
        button_kind="cigarettes",
        callback_data="e:cig",
        button_text="Сигарета",
        is_owner=False,
    )
    await clicks.record(
        telegram_id=9,
        clicked_at="2026-09-02T10:00:00+00:00",
        button_kind="menu",
        callback_data="n:m",
        button_text="Меню",
        is_owner=False,
    )
    start, end, title = click_window(
        "today",
        "UTC",
        now=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    )
    text = await render_click_report(repo, start=start, end=end, title=title, tz_name="UTC")
    assert "Нажатий: 2" in text
    assert "Сигареты" in text
    assert "e:cig" in text
    assert "Кто нажимал:" in text
    assert "Иван — 2" in text
    assert "Последние:" in text
    assert "2 сентября 10:00 · Иван · Меню" in text
    assert "2 сентября 09:00 · Иван · Сигарета" in text
    assert "ad:clk" not in text
    assert "не попадает в бэкап" in text
    journal = await render_click_journal(repo, start=start, end=end, title=title, tz_name="UTC")
    assert journal is not None
    assert "Иван" in journal
    assert "id=9" in journal
    assert "e:cig" in journal
    assert "02.09.2026 10:00:00" in journal
    assert "id=1" not in journal
    user_log = await render_user_click_log(repo, telegram_id=9, tz_name="UTC")
    assert "Нажатия Иван" in user_log
    assert "Всего: 2" in user_log
    assert "Сигарета" in user_log
    owner_log = await render_user_click_log(repo, telegram_id=1, tz_name="UTC")
    assert "Всего: 1" in owner_log
    assert "ad:clk" in owner_log
    share = ux_kind_share(await clicks.kind_counts(to_iso(start), to_iso(end)))
    kinds = {row["kind"] for row in share}
    assert kinds == {"cigarettes", "menu"}
    charts = await build_click_charts(clicks, start, end, title, "UTC")
    assert charts
    assert all(png.startswith(b"\x89PNG") for _, png in charts)
    from io import BytesIO

    from PIL import Image

    for _, png in charts:
        red, green, blue = Image.open(BytesIO(png)).convert("RGB").getpixel((4, 4))
        assert (red + green + blue) / 3 < 50


@pytest.mark.asyncio
async def test_click_report_escapes_names_and_empty_journal(repo):
    await repo.create_user(8, "x", "<b>x</b>", None, "UTC", 0, "23:00")
    clicks = repo.db.clicks_db
    assert clicks is not None
    await clicks.record(
        telegram_id=8,
        clicked_at="2026-09-02T11:00:00+00:00",
        button_kind="menu",
        callback_data="n:m",
        button_text="<Меню>",
        is_owner=False,
    )
    start, end, title = click_window(
        "today",
        "UTC",
        now=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    )
    text = await render_click_report(repo, start=start, end=end, title=title, tz_name="UTC")
    assert "&lt;b&gt;x&lt;/b&gt;" in text
    assert "<b>x</b>" not in text
    assert "&lt;Меню&gt;" in text
    journal = await render_click_journal(repo, start=start, end=end, title=title, tz_name="UTC")
    assert journal is not None
    assert "<b>x</b>" in journal
    empty_start, empty_end, empty_title = click_window(
        "today",
        "UTC",
        now=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert await render_click_journal(
        repo, start=empty_start, end=empty_end, title=empty_title, tz_name="UTC"
    ) is None
    missing = await render_user_click_log(repo, telegram_id=99, tz_name="UTC")
    assert "99" in missing
    assert "Пока нет нажатий" in missing


def _callback(telegram_id: int, data: str, text: str):
    button = SimpleNamespace(callback_data=data, text=text)
    markup = SimpleNamespace(inline_keyboard=[[button]])
    message = SimpleNamespace(reply_markup=markup)
    user = SimpleNamespace(id=telegram_id)
    return SimpleNamespace(data=data, from_user=user, message=message)

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from types import SimpleNamespace

from services.vpn_monitor import (
    append_vpn_log,
    collect_vpn_log_entries,
    format_vpn_log,
    parse_node,
    prune_vpn_logs,
    sanitize_error,
    subscription_label,
    vpn_samples_as_dicts,
)


def test_parse_node_from_auto_now():
    name, sub = parse_node("s3 | 🇨🇾yprus, Nicosia | [BL]-01")
    assert name.endswith("[BL]-01")
    assert sub == "sub3"
    assert subscription_label(sub) == "VLESS / mobile"


def test_parse_node_unknown_and_empty():
    assert parse_node("DIRECT") == ("DIRECT", "unknown")
    assert parse_node(None) == (None, None)
    assert parse_node("  ") == (None, None)
    name, sub = parse_node("s1 | 🇳🇱he Netherlands, Amsterdam | [BL]-12")
    assert sub == "sub1"


def test_sanitize_error_strips_bot_token():
    class Fake(Exception):
        pass

    text = sanitize_error(Fake("https://api.telegram.org/bot123456789:AA-this-is-a-fake-token-valuexx/getMe"))
    assert "123456789:AA-this-is-a-fake-token-valuexx" not in text
    assert "***" in text


def test_vpn_log_append_and_prune(tmp_path):
    log_dir = tmp_path / "vpn"
    append_vpn_log(
        log_dir,
        {
            "measured_at": "2026-08-19T10:00:00+00:00",
            "ok": True,
            "latency_ms": 120,
            "node_name": "s3 | Cyprus",
            "subscription": "sub3",
            "error": None,
        },
    )
    today = log_dir / "2026-08-19.ndjson"
    assert today.exists()
    line = today.read_text(encoding="utf-8")
    assert "sub3" in line
    assert "120" in line

    old = log_dir / f"{(date.today() - timedelta(days=40)).isoformat()}.ndjson"
    old.write_text("{}\n", encoding="utf-8")
    recent = log_dir / f"{date.today().isoformat()}.ndjson"
    recent.write_text("{}\n", encoding="utf-8")
    removed = prune_vpn_logs(log_dir, keep_days=31)
    assert removed == 1
    assert not old.exists()
    assert recent.exists()


def test_collect_vpn_log_entries_filters_last_day(tmp_path):
    log_dir = tmp_path / "vpn"
    append_vpn_log(
        log_dir,
        {
            "measured_at": "2026-08-18T19:00:00+00:00",
            "ok": True,
            "latency_ms": 90,
            "node_name": "old",
            "subscription": "sub1",
            "error": None,
        },
    )
    append_vpn_log(
        log_dir,
        {
            "measured_at": "2026-08-18T20:10:00+00:00",
            "ok": True,
            "latency_ms": 110,
            "node_name": "s3 | Cyprus",
            "subscription": "sub3",
            "error": None,
        },
    )
    append_vpn_log(
        log_dir,
        {
            "measured_at": "2026-08-19T10:00:00+00:00",
            "ok": False,
            "latency_ms": 8000,
            "node_name": "s1 | NL",
            "subscription": "sub1",
            "error": "timeout",
        },
    )
    append_vpn_log(
        log_dir,
        {
            "measured_at": "2026-08-19T20:10:00+00:00",
            "ok": True,
            "latency_ms": 80,
            "node_name": "too-new",
            "subscription": "sub2",
            "error": None,
        },
    )
    start, end = "2026-08-18T20:06:00+00:00", "2026-08-19T20:06:00+00:00"
    entries = collect_vpn_log_entries(log_dir, start, end)
    assert [row["node_name"] for row in entries] == ["s3 | Cyprus", "s1 | NL"]
    text = format_vpn_log(entries, start, end)
    assert "samples: 2  ok: 1  fail: 1" in text
    assert "FAIL" in text
    assert "timeout" in text
    assert "too-new" not in text
    assert "18.08.2026 20:10:00" in text
    assert "2026-08-18T20:10:00+00:00" in text
    assert "19.08.2026 10:00:00" in text
    assert "2026-08-19T10:00:00+00:00" in text
    assert "18 августа 2026, 20:06:00" in text
    fail_line = next(line for line in text.splitlines() if "FAIL" in line and "timeout" in line)
    assert fail_line.index("19.08.2026 10:00:00") < fail_line.index("FAIL")
    assert fail_line.index("FAIL") < fail_line.index("8000ms")
    assert fail_line.index("8000ms") < fail_line.index("timeout")
    assert fail_line.index("timeout") < fail_line.index("s1 | NL")
    assert fail_line.index("s1 | NL") < fail_line.index("sub1")
    assert fail_line.index("sub1") < fail_line.index("2026-08-19T10:00:00+00:00")
    assert "время" in text and "нода" in text
    assert text.endswith("\n")
    moscow = format_vpn_log(entries, start, end, tz_name="Europe/Moscow")
    assert "19.08.2026 13:00:00" in moscow
    assert "2026-08-19T10:00:00+00:00" in moscow
    assert "Europe/Moscow" in moscow


async def test_list_vpn_samples_window(repo):
    await repo.insert_vpn_sample("2026-08-18T19:00:00+00:00", True, 50, "old", "sub1", None)
    await repo.insert_vpn_sample("2026-08-18T21:00:00+00:00", True, 120, "keep", "sub3", None)
    await repo.insert_vpn_sample("2026-08-19T21:00:00+00:00", False, 8000, "new", "sub1", "timeout")
    rows = await repo.list_vpn_samples("2026-08-18T20:00:00+00:00", "2026-08-19T20:00:00+00:00")
    assert [row.node_name for row in rows] == ["keep"]
    payload = vpn_samples_as_dicts(rows)
    assert payload[0]["ok"] is True
    assert payload[0]["subscription"] == "sub3"


async def test_vpn_sample_summary(repo):
    start = "2026-08-19T10:00:00+00:00"
    await repo.insert_vpn_sample(start, True, 100, "s3 | A", "sub3", None)
    await repo.insert_vpn_sample("2026-08-19T10:00:10+00:00", True, 200, "s3 | A", "sub3", None)
    await repo.insert_vpn_sample("2026-08-19T10:00:20+00:00", False, 8000, "s1 | B", "sub1", "timeout")
    latest = await repo.latest_vpn_sample()
    assert latest is not None
    assert latest.error == "timeout"
    assert latest.ok == 0
    assert latest.host_uptime_s is None
    summary = await repo.vpn_latency_summary(start, "2026-08-19T11:00:00+00:00")
    assert summary["total"] == 3
    assert summary["ok_count"] == 2
    assert summary["fail_count"] == 1
    assert round(summary["avg_ms"]) == 2767
    assert int(summary["min_ms"]) == 100
    assert int(summary["max_ms"]) == 8000
    assert summary["bucket_0_100"] == 0
    assert summary["bucket_100_500"] == 2
    assert summary["bucket_500_1000"] == 0
    assert summary["bucket_1000"] == 0
    assert summary["no_ping"] == 1
    assert int(summary["p99_ms"]) == 8000
    assert summary["p99_count"] == 1
    assert int(summary["p99_9_ms"]) == 8000
    assert summary["p99_9_count"] == 1
    top = await repo.vpn_top_nodes(start, "2026-08-19T11:00:00+00:00")
    assert top[0]["subscription"] == "sub3"
    assert int(top[0]["samples"]) == 2
    assert int(top[0]["min_ms"]) == 100
    assert int(top[0]["max_ms"]) == 200
    assert int(top[0]["p95_ms"]) == 200
    assert int(top[0]["p95_count"]) == 1
    assert int(top[0]["p99_ms"]) == 200
    assert int(top[0]["p99_count"]) == 1
    assert int(top[0]["p99_9_ms"]) == 200
    assert int(top[0]["p99_9_count"]) == 1
    top_subs = await repo.vpn_top_subscriptions(start, "2026-08-19T11:00:00+00:00")
    assert [row["subscription"] for row in top_subs] == ["sub3", "sub1"]
    assert int(top_subs[0]["samples"]) == 2
    assert int(top_subs[0]["min_ms"]) == 100
    assert int(top_subs[0]["max_ms"]) == 200
    assert int(top_subs[0]["p95_ms"]) == 200
    assert int(top_subs[0]["p95_count"]) == 1
    assert int(top_subs[0]["p99_ms"]) == 200
    assert int(top_subs[0]["p99_count"]) == 1
    assert int(top_subs[1]["samples"]) == 1
    assert int(top_subs[1]["fail_count"]) == 1
    assert top_subs[1]["avg_ms"] is None
    assert top_subs[1]["p95_ms"] is None
    assert int(top_subs[1]["p95_count"]) == 0
    assert top_subs[1]["p99_ms"] is None
    assert int(top_subs[1]["p99_count"]) == 0


async def test_vpn_sample_stores_host_uptime(repo):
    start = "2026-08-19T10:00:00+00:00"
    await repo.insert_vpn_sample(start, True, 80, "n", "sub1", None, 123.4)
    latest = await repo.latest_vpn_sample()
    assert latest is not None
    assert latest.host_uptime_s == 123.4
    beats = await repo.list_vpn_heartbeats(start, "2026-08-19T11:00:00+00:00")
    assert beats == [(start, 123.4)]


async def test_vpn_latency_buckets(repo):
    start = "2026-08-19T10:00:00+00:00"
    samples = [
        (True, 50),
        (True, 80),
        (True, 150),
        (True, 600),
        (True, 2000),
        (False, 8000),
    ]
    for i, (ok, ms) in enumerate(samples):
        await repo.insert_vpn_sample(
            f"2026-08-19T10:00:{i:02d}+00:00",
            ok,
            ms,
            "n",
            "sub1",
            None,
        )
    summary = await repo.vpn_latency_summary(start, "2026-08-19T11:00:00+00:00")
    assert summary["measured"] == 6
    assert summary["bucket_0_100"] == 2
    assert summary["bucket_100_500"] == 1
    assert summary["bucket_500_1000"] == 1
    assert summary["bucket_1000"] == 1
    assert summary["no_ping"] == 1
    assert int(summary["min_ms"]) == 50
    assert int(summary["max_ms"]) == 8000


async def test_vpn_latency_exclusive_status_buckets(repo):
    start = "2026-08-19T10:00:00+00:00"
    samples = [
        (True, 40, None),
        (True, 100, None),
        (True, 499, None),
        (True, 500, None),
        (True, 999, None),
        (True, 1000, None),
        (False, 8000, "timeout"),
        (False, None, "mihomo_unreachable:ClientConnectorError"),
        (True, 90, "mihomo_timeout"),
    ]
    for i, (ok, ms, error) in enumerate(samples):
        await repo.insert_vpn_sample(
            f"2026-08-19T10:00:{i:02d}+00:00",
            ok,
            ms,
            "n",
            "sub1",
            error,
        )
    summary = await repo.vpn_latency_summary(start, "2026-08-19T11:00:00+00:00")
    assert summary["bucket_0_100"] == 1
    assert summary["bucket_100_500"] == 2
    assert summary["bucket_500_1000"] == 2
    assert summary["bucket_1000"] == 1
    assert summary["no_ping"] == 3


async def test_vpn_percentiles_and_tail_counts(repo):
    start = "2026-08-19T10:00:00+00:00"
    for i in range(100):
        minute, second = divmod(i, 60)
        await repo.insert_vpn_sample(
            f"2026-08-19T10:{minute:02d}:{second:02d}+00:00",
            True,
            i + 1,
            "n",
            "sub1",
            None,
        )
    end = "2026-08-19T12:00:00+00:00"
    summary = await repo.vpn_latency_summary(start, end)
    # 1..100; p99 = ceil(99)-1 → 99, tail >= 99 = 2
    assert int(summary["p99_ms"]) == 99
    assert summary["p99_count"] == 2
    # p95 OFFSET = CAST(99*0.95)=94 → value 95, tail >= 95 = 6
    assert int(summary["p95_ms"]) == 95
    assert summary["p95_count"] == 6
    # p99.9 = ceil(99.9)-1 → 100, tail >= 100 = 1
    assert int(summary["p99_9_ms"]) == 100
    assert summary["p99_9_count"] == 1
    top = await repo.vpn_top_nodes(start, end)
    assert int(top[0]["p95_ms"]) == 95
    assert int(top[0]["p95_count"]) == 6
    assert int(top[0]["p99_ms"]) == 99
    assert int(top[0]["p99_count"]) == 2
    assert int(top[0]["p99_9_ms"]) == 100
    assert int(top[0]["p99_9_count"]) == 1
    top_subs = await repo.vpn_top_subscriptions(start, end)
    assert top_subs[0]["subscription"] == "sub1"
    assert int(top_subs[0]["samples"]) == 100
    assert int(top_subs[0]["p95_ms"]) == 95
    assert int(top_subs[0]["p95_count"]) == 6
    assert int(top_subs[0]["p99_ms"]) == 99
    assert int(top_subs[0]["p99_count"]) == 2
    assert int(top_subs[0]["p99_9_ms"]) == 100
    assert int(top_subs[0]["p99_9_count"]) == 1


def test_vpn_top_item_lines_layout():
    from handlers.admin import _vpn_top_item_lines

    lines = _vpn_top_item_lines(
        {
            "samples": 1147,
            "avg_ms": 97.4,
            "p95_ms": 448.2,
            "p95_count": 12,
        },
        "<code>s4 | Sweden | [*CIDR]-05</code>",
    )
    assert lines == [
        "• <code>s4 | Sweden | [*CIDR]-05</code>",
        "\t1147 раз, avg 97 мс,",
        "\tp95 448 мс (12 зам.)",
    ]


async def test_vpn_top_nodes_limit_five(repo):
    start = "2026-08-19T10:00:00+00:00"
    for i in range(6):
        await repo.insert_vpn_sample(
            f"2026-08-19T10:00:{i:02d}+00:00",
            True,
            50 + i,
            f"node-{i}",
            "sub1",
            None,
        )
    top = await repo.vpn_top_nodes(start, "2026-08-19T11:00:00+00:00")
    assert len(top) == 5


async def test_vpn_monitor_tick_writes_db_and_file(repo, tmp_path, monkeypatch):
    from dataclasses import replace

    from services.vpn_monitor import VpnMonitor

    async def fake_now(config):
        return "s3 | 🇨🇾yprus, Nicosia | [BL]-01", None

    monkeypatch.setattr("services.vpn_monitor.fetch_auto_now", fake_now)

    class FakeBot:
        async def get_me(self):
            await asyncio.sleep(0.01)
            return SimpleNamespace(id=1, username="bot")

    config = replace(repo.db.config, vpn_log_dir=tmp_path / "vpn")
    monitor = VpnMonitor(config)
    sample = await monitor.tick(FakeBot(), repo)
    assert sample["ok"] is True
    assert sample["subscription"] == "sub3"
    assert sample["latency_ms"] >= 10
    stored = await repo.latest_vpn_sample()
    assert stored is not None
    assert stored.node_name.startswith("s3 |")
    files = list((tmp_path / "vpn").glob("*.ndjson"))
    assert len(files) == 1


def test_make_probe_bot_is_not_the_polling_session(tmp_path):
    from dataclasses import replace

    from aiogram.client.session.aiohttp import AiohttpSession

    from services.vpn_monitor import make_probe_bot
    from tests.conftest import make_config

    config = replace(
        make_config(tmp_path),
        telegram_proxy_url="socks5://127.0.0.1:11808",
        vpn_monitor_timeout_seconds=8,
    )
    probe = make_probe_bot(config)
    assert isinstance(probe.session, AiohttpSession)
    assert probe.session.proxy == "socks5://127.0.0.1:11808"
    assert probe.session.timeout == 8.0
    assert probe.token == config.bot_token


async def test_reset_probe_replaces_hung_bot(tmp_path):
    from dataclasses import replace

    from services.vpn_monitor import VpnMonitor, make_probe_bot
    from tests.conftest import make_config

    config = replace(make_config(tmp_path), telegram_proxy_url=None)
    first = make_probe_bot(config)
    monitor = VpnMonitor(config, probe_bot=first)
    await monitor.reset_probe()
    assert monitor._probe_bot is not first
    assert monitor._probe_bot.session is not first.session
    await first.session.close()
    await monitor._probe_bot.session.close()


async def test_vpn_monitor_tick_uses_probe_bot_not_polling_bot(repo, monkeypatch):
    from dataclasses import replace

    from services.vpn_monitor import VpnMonitor

    async def fake_now(config):
        return "s3 | Cyprus | [BL]-01", None

    monkeypatch.setattr("services.vpn_monitor.fetch_auto_now", fake_now)

    class ProbeBot:
        async def get_me(self):
            return SimpleNamespace(id=1, username="probe")

    class PollingBot:
        async def get_me(self):
            raise AssertionError("VPN probe must not use the polling Bot")

    monitor = VpnMonitor(replace(repo.db.config, vpn_log_dir=None), probe_bot=ProbeBot())
    sample = await monitor.tick(PollingBot(), repo)
    assert sample["ok"] is True


async def test_measure_bot_latency_timeout():
    from services.vpn_monitor import measure_bot_latency

    class SlowBot:
        async def get_me(self):
            await asyncio.sleep(1)

    ok, latency_ms, error = await measure_bot_latency(SlowBot(), timeout=0.05)
    assert ok is False
    assert error == "timeout"
    assert latency_ms >= 50


async def test_measure_bot_latency_abandons_slow_cancel():
    from services.vpn_monitor import measure_bot_latency

    class StickyBot:
        def __init__(self) -> None:
            self.session = SimpleNamespace(closed=0)

            async def close() -> None:
                self.session.closed += 1

            self.session.close = close

        async def get_me(self):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.4)
                raise

    started = time.monotonic()
    bot = StickyBot()
    ok, _, error = await measure_bot_latency(bot, timeout=0.05)
    elapsed = time.monotonic() - started
    assert ok is False
    assert error == "timeout"
    assert elapsed < 0.3
    assert bot.session.closed == 1
    await asyncio.sleep(0.5)


def test_vpn_monitor_config_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("VPN_MONITOR_ENABLED", "0")
    monkeypatch.setenv("MIHOMO_API_SECRET", "not-a-real-secret")
    monkeypatch.setenv("VPN_MONITOR_INTERVAL_SECONDS", "10")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    from config import load_config

    cfg = load_config()
    assert cfg.vpn_monitor_enabled is False
    assert cfg.mihomo_api_secret == "not-a-real-secret"
    assert cfg.mihomo_proxy_group == "AUTO"
    assert cfg.vpn_log_keep_days == 31


def test_vpn_monitor_job_is_not_scheduled_immediately(tmp_path):
    from datetime import datetime, timedelta, timezone

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from services.jobs import setup_scheduler
    from tests.conftest import make_config

    before = datetime.now(timezone.utc)
    scheduler = AsyncIOScheduler(timezone="UTC")
    setup_scheduler(scheduler, bot=object(), repo=object(), db=object(), config=make_config(tmp_path))
    job = scheduler.get_job("vpn_monitor")
    assert job is not None
    assert job.next_run_time is not None
    assert job.next_run_time >= before + timedelta(seconds=5)


def test_admin_vpn_kb_callback_limit():
    from keyboards.main import admin_vpn_kb

    kb = admin_vpn_kb("24h")
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "adv:24h:n" in datas
    assert "adv:5m:n" in datas
    assert "adv:24h:s" in datas
    assert "adv:24h:a" in datas
    assert "adv:all:n" in datas
    assert "advl:24h" in datas
    assert "advc:24h" in datas
    assert all(len(data.encode()) <= 64 for data in datas)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any(text and text.startswith("• Ноды") for text in labels)
    assert any(text == "Доступность" for text in labels)

    week = admin_vpn_kb("7d", "s")
    week_datas = [btn.callback_data for row in week.inline_keyboard for btn in row]
    assert "adv:7d:s" in week_datas
    assert "advl:7d" in week_datas
    assert "advc:7d" in week_datas
    labels = [btn.text for row in week.inline_keyboard for btn in row]
    assert any(text and "Логи за неделю" in text for text in labels)
    assert any(text and "Картинки за неделю" in text for text in labels)
    assert any(text and text.startswith("• Подписки") for text in labels)

    avail = admin_vpn_kb("24h", "a")
    avail_datas = [btn.callback_data for row in avail.inline_keyboard for btn in row]
    avail_labels = [btn.text for row in avail.inline_keyboard for btn in row]
    assert "adv:24h:a" in avail_datas
    assert "adv:24h:a:r" in avail_datas
    assert "advc:24h:a" in avail_datas
    assert "advc:24h" not in avail_datas
    assert any(text and text.startswith("• Доступность") for text in avail_labels)
    assert any(text == "Округление" for text in avail_labels)
    assert any(text and "Доступность за сутки" in text for text in avail_labels)
    assert all(text and "Картинки" not in text for text in avail_labels)

    rounded = admin_vpn_kb("7d", "a", rounded=True)
    rounded_datas = [btn.callback_data for row in rounded.inline_keyboard for btn in row]
    rounded_labels = [btn.text for row in rounded.inline_keyboard for btn in row]
    assert "adv:7d:a:r" in rounded_datas
    assert "advc:7d:a:r" in rounded_datas
    assert any(text and text.startswith("• Округление") for text in rounded_labels)
    assert any(text and "Доступность за неделю" in text for text in rounded_labels)
    assert all(len(data.encode()) <= 64 for data in rounded_datas)

    all_time = admin_vpn_kb("all", "n")
    all_labels = [btn.text for row in all_time.inline_keyboard for btn in row]
    all_datas = [btn.callback_data for row in all_time.inline_keyboard for btn in row]
    assert "adv:all:n" in all_datas
    assert "advl:all" in all_datas
    assert "advc:all" in all_datas
    assert any(text and text.startswith("• всё время") for text in all_labels)
    assert any(text and "Логи за всё время" in text for text in all_labels)
    assert any(text and "Картинки за всё время" in text for text in all_labels)


async def test_vpn_report_hides_live_status_and_supports_all_time(repo):
    from datetime import datetime, timezone

    from handlers.admin import _vpn_report

    await repo.create_user(1, "a", "A", None, "UTC", 10, "23:00")
    await repo.execute("UPDATE users SET registered_at = ? WHERE telegram_id = 1", ("2026-08-01T00:00:00+00:00",))
    await repo.conn.commit()
    await repo.insert_vpn_sample(
        "2026-08-19T10:00:00+00:00",
        True,
        50,
        "s2 | NL | [BL]-02",
        "sub2",
        None,
    )
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    text = await _vpn_report(repo, repo.db.config, "24h", now=now)
    assert "Сейчас AUTO" not in text
    assert "Подписка:" not in text
    assert "Последний замер:" not in text
    assert "Возраст сервиса:" in text
    assert "Следующий бекап" in text
    assert "Коммит:" in text
    assert "За последние сутки" in text
    assert "Замеров: 0 из 8640" in text
    assert "должно быть 8640 зам." in text

    all_text = await _vpn_report(repo, repo.db.config, "all", now=now)
    assert "За всё время" in all_text
    assert "Замеров: 1 из 18000" in all_text
    assert "Сейчас AUTO" not in all_text
    assert "Топ нод:" in all_text

    avail_text = await _vpn_report(repo, repo.db.config, "all", now=now, view="a")
    assert "Топ нод:" not in avail_text
    assert "Топ подписок:" not in avail_text
    assert "0–100 мс:" in avail_text


def test_short_node_name():
    from services.vpn_charts import short_node_name

    assert short_node_name("s3 | 🇨🇾yprus, Nicosia | [BL]-01") == "s3 · [BL]-01"
    assert short_node_name(None) == "нет ноды"
    assert short_node_name("DIRECT") == "DIRECT"


def test_latency_central_tendency_mode_and_median():
    from services.vpn_charts import latency_central_tendency

    stats = latency_central_tendency([10, 20, 20, 30, 100])
    assert stats is not None
    assert stats.mean == 36
    assert stats.median == 20
    assert stats.mode == 20
    assert stats.mode_binned is False
    assert stats.mode_count == 2


def test_latency_mode_bins_when_all_unique():
    from services.vpn_charts import latency_central_tendency

    stats = latency_central_tendency([11, 12, 28, 29, 91])
    assert stats is not None
    assert stats.mode_binned is True
    assert stats.mode in {10, 30}


def test_timeline_down_has_nan_ping_but_keeps_time():
    import math
    from datetime import timezone

    from database.models import VpnLatencySample
    from services.vpn_charts import SIGNAL_NO_PING, samples_to_timeline

    samples = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 120, "s3 | A | n1", "sub3", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 0, 8000, "s3 | A | n1", "sub3", "timeout"),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 1, 90, "s1 | B | n2", "sub1", None),
    ]
    points = samples_to_timeline(samples, color_by_sub=False)
    assert points[0].ping_ms == 120
    assert math.isnan(points[1].ping_ms)
    assert points[1].signal == SIGNAL_NO_PING
    assert points[1].down is True
    assert points[1].time.tzinfo == timezone.utc
    assert points[1].time.hour == 10
    assert points[1].time.second == 10
    assert points[2].ping_ms == 90
    assert points[1].node == "s3 · n1"


def test_downsample_keeps_outages():
    from database.models import VpnLatencySample
    from services.vpn_charts import downsample_timeline, samples_to_timeline

    samples = []
    for i in range(50):
        ok = i != 17
        samples.append(
            VpnLatencySample(
                i,
                f"2026-08-19T10:00:{i:02d}+00:00",
                1 if ok else 0,
                100 if ok else 8000,
                "n",
                "sub1",
                None if ok else "timeout",
            )
        )
    points = downsample_timeline(samples_to_timeline(samples, color_by_sub=False), max_ok=8, max_down=8)
    assert any(point.signal is not None for point in points)
    assert any(point.signal is None for point in points)


def test_render_vpn_charts_png_and_down_only():
    from database.models import VpnLatencySample
    from services.vpn_charts import render_vpn_charts

    mixed = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 120, "s3 | A | n1", "sub3", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 0, 8000, "s3 | A | n1", "sub3", "timeout"),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 1, 90, "s1 | B | n2", "sub1", None),
        VpnLatencySample(4, "2026-08-19T10:00:30+00:00", 1, 90, "s1 | B | n2", "sub1", None),
        VpnLatencySample(5, "2026-08-19T10:00:40+00:00", 1, 110, "s1 | B | n2", "sub1", None),
    ]
    charts = render_vpn_charts(mixed, "последние 5 минут")
    assert len(charts) == 2
    assert "медиана" in charts[0][0]
    assert "Пинг по времени" in charts[1][0]
    for _caption, png in charts:
        assert png.startswith(b"\x89PNG")

    down_only = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 0, None, None, None, "timeout"),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 0, None, None, None, "timeout"),
    ]
    only_time = render_vpn_charts(down_only, "последние 5 минут")
    assert len(only_time) == 1
    assert "Пинг по времени" in only_time[0][0]
    assert only_time[0][1].startswith(b"\x89PNG")
    width, _height = _png_size(only_time[0][1])
    assert width >= 6000
    for _caption, png in charts + only_time:
        assert len(png) <= 1_000_000


def test_ping_y_ticks_familiar_values():
    from services.vpn_charts import nice_ping_ymax, ping_y_ticks

    assert nice_ping_ymax(80) == 100
    assert nice_ping_ymax(340) == 500
    assert nice_ping_ymax(510) == 1000
    assert nice_ping_ymax(1001) == 2000
    assert nice_ping_ymax(2300) == 2000
    assert nice_ping_ymax(3000) == 2000
    assert nice_ping_ymax(8000) == 2000
    majors, minors = ping_y_ticks(340)
    for mark in (0, 50, 100, 200, 500):
        assert mark in majors
    assert 10 in minors or 10 in majors
    assert 25 in minors or 25 in majors
    highs = [tick for tick in majors if tick >= 100]
    lows = [tick for tick in minors if tick <= 50]
    assert lows
    assert highs[1] - highs[0] >= 50
    high = [tick for tick in ping_y_ticks(2300)[0] if tick >= 1000]
    assert high == [1000, 2000]
    majors, minors = ping_y_ticks(340)
    for mark in (0, 50, 100, 200, 500):
        assert mark in majors
    assert 10 in minors or 10 in majors
    assert 25 in minors or 25 in majors
    highs = [tick for tick in majors if tick >= 100]
    lows = [tick for tick in minors if tick <= 50]
    assert lows
    assert highs[1] - highs[0] >= 50


def test_central_chart_ping_axis_uses_timeline_ticks():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from services.vpn_charts import _apply_ping_ticks, ping_y_ticks

    fig, ax = plt.subplots()
    _apply_ping_ticks(ax, 80, axis="x")
    majors, _minors = ping_y_ticks(80)
    assert list(ax.get_xticks()) == majors
    assert ax.get_xlim() == (0.0, 100.0)
    plt.close(fig)


def test_ping_axis_never_exceeds_2000():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from services.vpn_charts import _apply_ping_ticks, clip_ping_display

    assert clip_ping_display(80) == 80
    assert clip_ping_display(2000) == 2000
    assert clip_ping_display(8000) == 2000
    fig, ax = plt.subplots()
    _apply_ping_ticks(ax, 8000, axis="y")
    assert ax.get_ylim() == (0.0, 2000.0)
    plt.close(fig)
    fig, ax = plt.subplots()
    _apply_ping_ticks(ax, 8000, axis="x")
    assert ax.get_xlim() == (0.0, 2000.0)
    plt.close(fig)


def test_density_curve_stops_at_2000():
    from services.vpn_charts import _PING_DISPLAY_MAX, _smooth_density_curve

    xs, _ys = _smooth_density_curve([50, 55, 60, 80, 5000, 8000, 12000])
    assert xs
    assert max(xs) <= _PING_DISPLAY_MAX + 1e-6


def test_smooth_ping_series_ignores_spikes():
    from datetime import datetime, timedelta, timezone

    from services.vpn_charts import smooth_ping_series

    start = datetime(2026, 8, 19, 10, tzinfo=timezone.utc)
    times = [start + timedelta(seconds=10 * i) for i in range(80)]
    values = [80.0] * 80
    values[40] = 800.0
    smooth = smooth_ping_series(times, values)
    assert max(smooth) < 200
    assert 70 <= smooth[40] <= 120


def test_densify_curve_rounds_corners():
    from services.vpn_charts import _chaikin, _densify_curve

    xs, ys = _densify_curve([0.0, 1.0, 2.0], [0.0, 10.0, 0.0], steps=10)
    assert len(xs) > 10
    assert any(3.0 < y < 9.5 for y in ys)
    cx, cy = _chaikin([0.0, 1.0, 2.0], [0.0, 10.0, 0.0], iterations=3)
    assert len(cx) > 6
    peak = max(cy)
    assert 6.0 < peak < 10.0


def _png_size(data: bytes) -> tuple[int, int]:
    import struct

    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_classify_vpn_signal_kinds():
    from services.vpn_charts import (
        SIGNAL_NO_PING,
        classify_vpn_signal,
    )

    assert classify_vpn_signal(ok=True, latency_ms=80, error=None) is None
    assert classify_vpn_signal(ok=False, latency_ms=8000, error="timeout") == SIGNAL_NO_PING
    assert classify_vpn_signal(ok=True, latency_ms=None, error=None) == SIGNAL_NO_PING
    assert (
        classify_vpn_signal(ok=False, latency_ms=8000, error="mihomo_unreachable:ClientConnectorError")
        == SIGNAL_NO_PING
    )
    assert classify_vpn_signal(ok=True, latency_ms=90, error="mihomo_timeout") == SIGNAL_NO_PING
    assert (
        classify_vpn_signal(ok=False, latency_ms=8000, error="mihomo_unreachable:OSError; timeout")
        == SIGNAL_NO_PING
    )


def test_fill_downtime_gaps_without_uptime_is_service_down():
    from datetime import datetime, timezone

    from database.models import VpnLatencySample
    from services.vpn_charts import SIGNAL_SERVICE_DOWN, fill_downtime_gaps, samples_to_timeline

    utc = timezone.utc
    samples = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 100, "n1", "sub1", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 1, 110, "n1", "sub1", None),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 1, 90, "n1", "sub1", None),
        VpnLatencySample(4, "2026-08-19T10:02:00+00:00", 1, 95, "n1", "sub1", None),
        VpnLatencySample(5, "2026-08-19T10:02:10+00:00", 1, 80, "n1", "sub1", None),
    ]
    points = samples_to_timeline(samples, color_by_sub=False)
    filled = fill_downtime_gaps(
        points,
        window_start=datetime(2026, 8, 19, 9, 58, tzinfo=utc),
        window_end=datetime(2026, 8, 19, 10, 5, tzinfo=utc),
    )
    down = [point for point in filled if point.signal == SIGNAL_SERVICE_DOWN]
    assert len(down) >= 4
    assert any(point.time.hour == 9 for point in down)
    assert any(point.time.minute == 0 and point.time.second >= 20 for point in down)
    assert any(point.time.minute >= 2 for point in down)


def test_downtime_ticks_counts_gap_duration():
    from datetime import datetime, timezone

    from services.vpn_charts import downtime_ticks

    utc = timezone.utc
    heartbeats = [
        ("2026-08-19T10:00:00+00:00", None),
        ("2026-08-19T10:00:10+00:00", None),
        ("2026-08-19T10:00:20+00:00", None),
        ("2026-08-19T10:02:00+00:00", None),
        ("2026-08-19T10:02:10+00:00", None),
    ]
    down, off = downtime_ticks(
        heartbeats,
        window_start=datetime(2026, 8, 19, 9, 58, tzinfo=utc),
        window_end=datetime(2026, 8, 19, 10, 5, tzinfo=utc),
        interval_seconds=10,
    )
    # 9:58–10:00 = 120s; 10:00:20–10:02:00 minus 1 tick = 90s; 10:02:10–10:05 minus 1 tick = 160s
    assert down == 37
    assert off == 0
    empty_down, empty_off = downtime_ticks(
        [],
        window_start=datetime(2026, 8, 19, 10, 0, tzinfo=utc),
        window_end=datetime(2026, 8, 19, 10, 10, tzinfo=utc),
        interval_seconds=10,
    )
    assert empty_down == 60
    assert empty_off == 0


def test_downtime_ticks_splits_power_off_from_service_down():
    from datetime import datetime, timezone

    from services.vpn_charts import downtime_ticks

    utc = timezone.utc
    # 10 min silence; host has been up 60s when sampling resumes → power off then boot.
    down, off = downtime_ticks(
        [
            ("2026-08-19T10:00:00+00:00", 10000.0),
            ("2026-08-19T10:10:00+00:00", 60.0),
        ],
        window_start=datetime(2026, 8, 19, 10, 0, tzinfo=utc),
        window_end=datetime(2026, 8, 19, 10, 10, tzinfo=utc),
        interval_seconds=10,
        now_host_uptime_s=60.0,
    )
    # skip first step: 10:00:10–10:10:00 = 590s; off = 530s, service = 60s
    assert off == 53
    assert down == 6
    stayed_up_down, stayed_up_off = downtime_ticks(
        [
            ("2026-08-19T10:00:00+00:00", 20000.0),
            ("2026-08-19T10:10:00+00:00", 20600.0),
        ],
        window_start=datetime(2026, 8, 19, 10, 0, tzinfo=utc),
        window_end=datetime(2026, 8, 19, 10, 10, tzinfo=utc),
        interval_seconds=10,
        now_host_uptime_s=20600.0,
    )
    assert stayed_up_off == 0
    assert stayed_up_down == 59


def test_fill_downtime_gaps_marks_power_off_after_reboot():
    from datetime import datetime, timezone

    from database.models import VpnLatencySample
    from services.vpn_charts import SIGNAL_SERVER_OFF, SIGNAL_SERVICE_DOWN, fill_downtime_gaps, samples_to_timeline

    utc = timezone.utc
    samples = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 100, "n1", "sub1", None, 50000.0),
        VpnLatencySample(2, "2026-08-19T10:10:00+00:00", 1, 90, "n1", "sub1", None, 40.0),
    ]
    points = samples_to_timeline(samples, color_by_sub=False)
    filled = fill_downtime_gaps(
        points,
        window_start=datetime(2026, 8, 19, 10, 0, tzinfo=utc),
        window_end=datetime(2026, 8, 19, 10, 10, tzinfo=utc),
        now_host_uptime_s=40.0,
    )
    signals = {point.signal for point in filled}
    assert SIGNAL_SERVER_OFF in signals
    assert SIGNAL_SERVICE_DOWN in signals


def test_vpn_bucket_lines_exclusive_layout():
    from handlers.admin import _vpn_bucket_lines

    lines = _vpn_bucket_lines(
        {
            "bucket_0_100": 10,
            "bucket_100_500": 20,
            "bucket_500_1000": 5,
            "bucket_1000": 15,
            "no_ping": 30,
            "server_off": 12,
            "service_down": 8,
        },
        10,
    )
    assert lines[0] == "Время в диапазонах (тик 10 с, должно быть 100 зам.):"
    assert lines[1].startswith("0–100 мс:")
    assert "1 мин 40 с" in lines[1]
    assert "(10,0%)" in lines[1]
    assert lines[2].startswith("100–500 мс:")
    assert lines[3].startswith("500–1000 мс:")
    assert lines[4].startswith("&gt; 1000 мс:")
    assert lines[5].startswith("Нет пинга/соединения:")
    assert "5 мин" in lines[5]
    assert lines[6].startswith("сервис не запущен:")
    assert "(8,0%)" in lines[6]
    assert lines[7].startswith("сервер выключен:")


def test_vpn_bucket_lines_use_expected_period_ticks():
    from handlers.admin import _vpn_bucket_lines

    lines = _vpn_bucket_lines(
        {
            "bucket_0_100": 2,
            "bucket_100_500": 0,
            "bucket_500_1000": 0,
            "bucket_1000": 0,
            "no_ping": 0,
            "server_off": 0,
            "service_down": 0,
        },
        10,
        expected=30,
    )
    assert lines[0] == "Время в диапазонах (тик 10 с, должно быть 30 зам.):"
    assert "(6,7%)" in lines[1]
    assert lines[1].startswith("0–100 мс:")


def test_expected_vpn_ticks_matches_period():
    from datetime import datetime, timezone

    from services.vpn_charts import expected_vpn_ticks

    utc = timezone.utc
    end = datetime(2026, 8, 21, 12, 0, tzinfo=utc)
    assert expected_vpn_ticks(end, end, 10) == 0
    assert expected_vpn_ticks(end - timedelta(minutes=5), end, 10) == 30
    assert expected_vpn_ticks(end - timedelta(hours=1), end, 10) == 360
    assert expected_vpn_ticks(end - timedelta(hours=24), end, 10) == 8640
    assert expected_vpn_ticks(end - timedelta(seconds=25), end, 10) == 2


async def test_vpn_report_shows_expected_ticks_when_window_is_sparse(repo):
    from datetime import datetime, timezone

    from handlers.admin import _vpn_report

    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    await repo.insert_vpn_sample("2026-08-21T11:59:50+00:00", True, 40, "n", "sub1", None)
    await repo.insert_vpn_sample("2026-08-21T11:59:40+00:00", False, None, "n", "sub1", "timeout")
    text = await _vpn_report(repo, repo.db.config, "5m", now=now)
    assert "Замеров: 2 из 30" in text
    assert "ошибок: 1 (50,0%)" in text
    assert "должно быть 30 зам." in text


def test_server_palette_avoids_signal_colors():
    from services.vpn_charts import _palette, _server_colors

    for count in (1, 2, 4, 8, 24, 80):
        keys = [f"node-{i}" for i in range(count)]
        colors = _palette(keys)
        assert len({colors[key] for key in keys}) == count
        for key in keys:
            r, g, b = colors[key][:3]
            red_orange = r >= 0.72 and b <= 0.40 and g <= 0.55
            yellow = r >= 0.75 and g >= 0.65 and b <= 0.40
            orange = r >= 0.80 and 0.35 <= g <= 0.70 and b <= 0.35
            assert not red_orange, (key, count, r, g, b)
            assert not yellow, (key, count, r, g, b)
            assert not orange, (key, count, r, g, b)
        assert _server_colors(count) == [colors[key] for key in keys]


def test_server_colors_spread_then_pack():
    import math

    from services.vpn_charts import _server_colors

    def min_dist(values: list[tuple[float, float, float]]) -> float:
        return min(
            math.dist(values[i], values[j])
            for i in range(len(values))
            for j in range(i + 1, len(values))
        )

    two = _server_colors(2)
    four = _server_colors(4)
    twenty = _server_colors(20)
    assert min_dist(two) > 0.7
    assert min_dist(two) > min_dist(four)
    assert min_dist(four) > min_dist(twenty)
    assert len(_server_colors(1)) == 1
    assert _server_colors(0) == []


def test_timeline_keeps_vpn_errors_as_no_ping():
    from database.models import VpnLatencySample
    from services.vpn_charts import SIGNAL_NO_PING, samples_to_timeline

    samples = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 120, "s3 | A | n1", "sub3", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 0, None, None, None, "mihomo_unreachable:ClientConnectorError"),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 0, 8000, "s1 | B | n2", "sub1", "timeout"),
    ]
    points = samples_to_timeline(samples, color_by_sub=False)
    assert points[0].signal is None
    assert points[1].signal == SIGNAL_NO_PING
    assert points[2].signal == SIGNAL_NO_PING


def test_parse_vpn_view_availability():
    from handlers.admin import _parse_vpn_chart, _parse_vpn_view

    assert _parse_vpn_view("ad:vpn") == ("24h", "n", False)
    assert _parse_vpn_view("adv:7d:s") == ("7d", "s", False)
    assert _parse_vpn_view("adv:24h:a") == ("24h", "a", False)
    assert _parse_vpn_view("adv:30d:a:r") == ("30d", "a", True)
    assert _parse_vpn_view("adv:5m:n:r") == ("5m", "n", False)
    assert _parse_vpn_chart("advc:7d") == ("7d", False, False)
    assert _parse_vpn_chart("advc:24h:a") == ("24h", True, False)
    assert _parse_vpn_chart("advc:all:a:r") == ("all", True, True)


def test_ping_bucket_key_ranges():
    from services.vpn_charts import PING_0_100, PING_100_500, PING_500_1000, PING_1000, ping_bucket_key

    assert ping_bucket_key(0) == PING_0_100
    assert ping_bucket_key(99.9) == PING_0_100
    assert ping_bucket_key(100) == PING_100_500
    assert ping_bucket_key(499.9) == PING_100_500
    assert ping_bucket_key(500) == PING_500_1000
    assert ping_bucket_key(999.9) == PING_500_1000
    assert ping_bucket_key(1000) == PING_1000
    assert ping_bucket_key(8000) == PING_1000


def test_availability_round_window_grows_then_caps():
    from datetime import timedelta

    from services.vpn_charts import availability_round_window

    step = timedelta(seconds=10)
    five_min = availability_round_window(timedelta(minutes=5), step)
    day = availability_round_window(timedelta(hours=24), step)
    month = availability_round_window(timedelta(days=30), step)
    assert five_min == timedelta(seconds=30)
    assert timedelta(minutes=20) < day < timedelta(minutes=25)
    assert month == timedelta(hours=2)
    assert day < month


def test_samples_to_timeline_color_by_ping():
    from database.models import VpnLatencySample
    from services.vpn_charts import (
        PING_0_100,
        PING_100_500,
        PING_500_1000,
        PING_1000,
        SIGNAL_NO_PING,
        samples_to_timeline,
    )

    samples = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 80, "s3 | A | n1", "sub3", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 1, 120, "s3 | A | n1", "sub3", None),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 1, 600, "s1 | B | n2", "sub1", None),
        VpnLatencySample(4, "2026-08-19T10:00:30+00:00", 1, 1500, "s1 | B | n2", "sub1", None),
        VpnLatencySample(5, "2026-08-19T10:00:40+00:00", 0, None, None, None, "timeout"),
    ]
    points = samples_to_timeline(samples, color_by_sub=False, color_by_ping=True)
    assert [point.color_key for point in points] == [
        PING_0_100,
        PING_100_500,
        PING_500_1000,
        PING_1000,
        SIGNAL_NO_PING,
    ]


def test_round_availability_merges_ping_flicker():
    from datetime import datetime, timedelta, timezone

    from services.vpn_charts import PING_0_100, TimelinePoint, round_availability_colors

    utc = timezone.utc
    start = datetime(2026, 8, 19, 10, tzinfo=utc)
    points = []
    for i in range(60):
        ping = 90.0 if i % 2 == 0 else 110.0
        points.append(TimelinePoint(start + timedelta(seconds=10 * i), ping, "n", None, "x"))
    rounded = round_availability_colors(points, window=timedelta(seconds=30), step=timedelta(seconds=10))
    assert {point.color_key for point in rounded} == {PING_0_100}
    assert rounded[1].ping_ms == 110.0


def test_round_availability_keeps_signals_and_stable_shift():
    import math
    from datetime import datetime, timedelta, timezone

    from services.vpn_charts import (
        PING_0_100,
        PING_500_1000,
        SIGNAL_NO_PING,
        TimelinePoint,
        round_availability_colors,
    )

    utc = timezone.utc
    start = datetime(2026, 8, 19, 10, tzinfo=utc)
    points = [
        TimelinePoint(start, 80.0, "n", None, PING_0_100),
        TimelinePoint(start + timedelta(seconds=10), float("nan"), "n", SIGNAL_NO_PING, SIGNAL_NO_PING),
        TimelinePoint(start + timedelta(seconds=20), 80.0, "n", None, PING_0_100),
    ]
    rounded = round_availability_colors(points, window=timedelta(seconds=30), step=timedelta(seconds=10))
    assert rounded[1].signal == SIGNAL_NO_PING
    assert rounded[1].color_key == SIGNAL_NO_PING
    assert math.isnan(rounded[1].ping_ms)

    shift = []
    for i in range(40):
        ping = 80.0 if i < 20 else 600.0
        shift.append(TimelinePoint(start + timedelta(seconds=10 * i), ping, "n", None, "x"))
    shifted = round_availability_colors(shift, window=timedelta(seconds=30), step=timedelta(seconds=10))
    keys = [point.color_key for point in shifted]
    assert PING_0_100 in keys
    assert PING_500_1000 in keys
    assert keys[0] == PING_0_100
    assert keys[-1] == PING_500_1000
    assert keys.count(PING_0_100) >= 15
    assert keys.count(PING_500_1000) >= 15


def test_ping_bucket_colors_avoid_signal_hues():
    from services.vpn_charts import _PING_BUCKET_COLORS, _PING_BUCKET_KEYS, _palette

    colors = _palette(list(_PING_BUCKET_KEYS))
    for key in _PING_BUCKET_KEYS:
        r, g, b = colors[key][:3]
        assert colors[key] == _PING_BUCKET_COLORS[key]
        red_orange = r >= 0.72 and b <= 0.40 and g <= 0.55
        yellow = r >= 0.75 and g >= 0.65 and b <= 0.40
        orange = r >= 0.80 and 0.35 <= g <= 0.70 and b <= 0.35
        assert not red_orange, (key, r, g, b)
        assert not yellow, (key, r, g, b)
        assert not orange, (key, r, g, b)


def test_render_availability_charts_png():
    from database.models import VpnLatencySample
    from services.vpn_charts import render_availability_charts

    mixed = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 80, "s3 | A | n1", "sub3", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 1, 120, "s3 | A | n1", "sub3", None),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 0, 8000, "s3 | A | n1", "sub3", "timeout"),
        VpnLatencySample(4, "2026-08-19T10:00:30+00:00", 1, 90, "s1 | B | n2", "sub1", None),
        VpnLatencySample(5, "2026-08-19T10:00:40+00:00", 1, 700, "s1 | B | n2", "sub1", None),
    ]
    charts = render_availability_charts(mixed, "последние 5 минут")
    assert len(charts) == 1
    assert charts[0][0].startswith("Доступность")
    assert "округление" not in charts[0][0]
    assert charts[0][1].startswith(b"\x89PNG")
    assert len(charts[0][1]) <= 1_000_000

    rounded = render_availability_charts(mixed, "последние 5 минут", rounded=True)
    assert len(rounded) == 1
    assert "округление" in rounded[0][0]
    assert rounded[0][1].startswith(b"\x89PNG")

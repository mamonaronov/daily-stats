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
    summary = await repo.vpn_latency_summary(start, "2026-08-19T11:00:00+00:00")
    assert summary["total"] == 3
    assert summary["ok_count"] == 2
    assert summary["fail_count"] == 1
    assert round(summary["avg_ms"]) == 2767
    assert int(summary["min_ms"]) == 100
    assert int(summary["max_ms"]) == 8000
    assert summary["lt_100"] == 0
    assert summary["ge_100"] == 3
    assert summary["ge_500"] == 1
    assert summary["ge_1000"] == 1
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
    assert summary["lt_100"] == 2
    assert summary["ge_100"] == 4
    assert summary["ge_500"] == 3
    assert summary["ge_1000"] == 2
    assert int(summary["min_ms"]) == 50
    assert int(summary["max_ms"]) == 8000


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
    assert "advl:24h" in datas
    assert "advc:24h" in datas
    assert all(len(data.encode()) <= 64 for data in datas)

    week = admin_vpn_kb("7d", "s")
    week_datas = [btn.callback_data for row in week.inline_keyboard for btn in row]
    assert "adv:7d:s" in week_datas
    assert "advl:7d" in week_datas
    assert "advc:7d" in week_datas
    labels = [btn.text for row in week.inline_keyboard for btn in row]
    assert any(text and "Логи за неделю" in text for text in labels)
    assert any(text and "Картинки за неделю" in text for text in labels)
    assert any(text and text.startswith("• Подписки") for text in labels)


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
    assert nice_ping_ymax(2300) == 3000
    assert nice_ping_ymax(3000) == 3000
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
    assert high == [1000, 2000, 3000]
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
        SIGNAL_SERVICE_DOWN,
        classify_vpn_signal,
    )

    assert classify_vpn_signal(ok=True, latency_ms=80, error=None) is None
    assert classify_vpn_signal(ok=False, latency_ms=8000, error="timeout") == SIGNAL_NO_PING
    assert classify_vpn_signal(ok=True, latency_ms=None, error=None) == SIGNAL_NO_PING
    assert (
        classify_vpn_signal(ok=False, latency_ms=8000, error="mihomo_unreachable:ClientConnectorError")
        == SIGNAL_SERVICE_DOWN
    )
    assert classify_vpn_signal(ok=True, latency_ms=90, error="mihomo_timeout") == SIGNAL_SERVICE_DOWN
    assert (
        classify_vpn_signal(ok=False, latency_ms=8000, error="mihomo_unreachable:OSError; timeout")
        == SIGNAL_SERVICE_DOWN
    )


def test_fill_server_off_gaps_between_and_around_window():
    from datetime import datetime, timezone

    from database.models import VpnLatencySample
    from services.vpn_charts import SIGNAL_SERVER_OFF, fill_server_off_gaps, samples_to_timeline

    utc = timezone.utc
    samples = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 100, "n1", "sub1", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 1, 110, "n1", "sub1", None),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 1, 90, "n1", "sub1", None),
        VpnLatencySample(4, "2026-08-19T10:02:00+00:00", 1, 95, "n1", "sub1", None),
        VpnLatencySample(5, "2026-08-19T10:02:10+00:00", 1, 80, "n1", "sub1", None),
    ]
    points = samples_to_timeline(samples, color_by_sub=False)
    filled = fill_server_off_gaps(
        points,
        window_start=datetime(2026, 8, 19, 9, 58, tzinfo=utc),
        window_end=datetime(2026, 8, 19, 10, 5, tzinfo=utc),
    )
    off = [point for point in filled if point.signal == SIGNAL_SERVER_OFF]
    assert len(off) >= 4
    assert any(point.time.hour == 9 for point in off)
    assert any(point.time.minute == 0 and point.time.second >= 20 for point in off)
    assert any(point.time.minute >= 2 for point in off)


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


def test_timeline_keeps_service_down_and_no_ping():
    from database.models import VpnLatencySample
    from services.vpn_charts import SIGNAL_NO_PING, SIGNAL_SERVICE_DOWN, samples_to_timeline

    samples = [
        VpnLatencySample(1, "2026-08-19T10:00:00+00:00", 1, 120, "s3 | A | n1", "sub3", None),
        VpnLatencySample(2, "2026-08-19T10:00:10+00:00", 0, None, None, None, "mihomo_unreachable:ClientConnectorError"),
        VpnLatencySample(3, "2026-08-19T10:00:20+00:00", 0, 8000, "s1 | B | n2", "sub1", "timeout"),
    ]
    points = samples_to_timeline(samples, color_by_sub=False)
    assert points[0].signal is None
    assert points[1].signal == SIGNAL_SERVICE_DOWN
    assert points[2].signal == SIGNAL_NO_PING

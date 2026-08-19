from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from services.vpn_monitor import (
    append_vpn_log,
    parse_node,
    prune_vpn_logs,
    sanitize_error,
    subscription_label,
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
    top = await repo.vpn_top_nodes(start, "2026-08-19T11:00:00+00:00")
    assert top[0]["subscription"] == "sub3"
    assert int(top[0]["samples"]) == 2


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


async def test_measure_bot_latency_timeout():
    from services.vpn_monitor import measure_bot_latency

    class SlowBot:
        async def get_me(self):
            await asyncio.sleep(1)

    ok, latency_ms, error = await measure_bot_latency(SlowBot(), timeout=0.05)
    assert ok is False
    assert error == "timeout"
    assert latency_ms >= 50


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


def test_admin_vpn_kb_callback_limit():
    from keyboards.main import admin_vpn_kb

    kb = admin_vpn_kb("24h")
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "adv:24h" in datas
    assert all(len(data.encode()) <= 64 for data in datas)

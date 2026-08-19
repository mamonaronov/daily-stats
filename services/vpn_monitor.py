"""Measure Telegram Bot API RTT and the current mihomo AUTO node."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiogram import Bot

from config import Config
from database.queries import Repo
from utils.logging import TOKEN_RE, log_extra
from utils.time import now_utc, to_iso

logger = logging.getLogger(__name__)

NODE_PREFIX_RE = re.compile(r"^(s\d+)\s+\|")

# Labels match deploy/mihomo/config.yaml proxy-providers.
SUBSCRIPTION_LABELS = {
    "sub1": "VLESS / все конфиги",
    "sub2": "Shadowsocks+All",
    "sub3": "VLESS / mobile",
    "sub4": "CIDR / белые списки",
    "sub5": "SNI / белые списки",
}

_SUMMARY_EVERY = 30  # 30 × 10s ≈ 5 minutes


def parse_node(now: str | None) -> tuple[str | None, str | None]:
    """Return (node_name, subscription like sub3) from mihomo AUTO.now."""
    if not now or not str(now).strip():
        return None, None
    name = str(now).strip()
    match = NODE_PREFIX_RE.match(name)
    if not match:
        return name, "unknown"
    prefix = match.group(1)
    return name, f"sub{int(prefix[1:])}"


def subscription_label(subscription: str | None) -> str:
    if not subscription:
        return "неизвестно"
    return SUBSCRIPTION_LABELS.get(subscription, subscription)


def sanitize_error(exc: BaseException) -> str:
    text = TOKEN_RE.sub("***", f"{type(exc).__name__}: {exc}")
    return text[:300]


def append_vpn_log(log_dir: Path, payload: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    day = str(payload.get("measured_at") or "")[:10]
    if len(day) != 10:
        day = date.today().isoformat()
    path = log_dir / f"{day}.ndjson"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def prune_vpn_logs(log_dir: Path, keep_days: int) -> int:
    if not log_dir.exists():
        return 0
    cutoff = date.today() - timedelta(days=keep_days)
    removed = 0
    for path in log_dir.glob("*.ndjson"):
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


async def fetch_auto_now(config: Config) -> tuple[str | None, str | None]:
    """Current AUTO node from mihomo. Second value is an error, not a subscription."""
    if not config.mihomo_api_secret:
        return None, "mihomo_secret_missing"
    base = config.mihomo_api_url.rstrip("/")
    group = quote(config.mihomo_proxy_group, safe="")
    url = f"{base}/proxies/{group}"
    timeout = aiohttp.ClientTimeout(total=3)
    headers = {"Authorization": f"Bearer {config.mihomo_api_secret}"}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    return None, "mihomo_unauthorized"
                if resp.status != 200:
                    return None, f"mihomo_http_{resp.status}"
                data = await resp.json(content_type=None)
    except asyncio.TimeoutError:
        return None, "mihomo_timeout"
    except aiohttp.ClientError as exc:
        return None, f"mihomo_unreachable:{type(exc).__name__}"
    except Exception as exc:
        return None, sanitize_error(exc)
    now = data.get("now") if isinstance(data, dict) else None
    if not isinstance(now, str) or not now.strip():
        return None, "mihomo_no_now"
    return now.strip(), None


async def measure_bot_latency(bot: Bot, timeout: float) -> tuple[bool, int, str | None]:
    started = time.monotonic()
    try:
        await asyncio.wait_for(bot.get_me(), timeout=timeout)
        latency_ms = int((time.monotonic() - started) * 1000)
        return True, latency_ms, None
    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - started) * 1000)
        return False, latency_ms, "timeout"
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return False, latency_ms, sanitize_error(exc)


class VpnMonitor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._ticks = 0
        self._ok = 0
        self._fail = 0
        self._latencies: list[int] = []
        self._last_prune_day: date | None = None

    def _maybe_prune(self) -> None:
        log_dir = self.config.vpn_log_dir
        if log_dir is None:
            return
        today = date.today()
        if self._last_prune_day == today:
            return
        removed = prune_vpn_logs(log_dir, self.config.vpn_log_keep_days)
        self._last_prune_day = today
        if removed:
            logger.info("VPN log files pruned: %s", removed)

    def _flush_summary(self) -> None:
        if self._ticks % _SUMMARY_EVERY != 0:
            return
        avg = round(sum(self._latencies) / len(self._latencies)) if self._latencies else None
        log_extra(
            logger,
            logging.INFO,
            "vpn_monitor_summary",
            ticks=self._ticks,
            ok=self._ok,
            fail=self._fail,
            avg_ms=avg,
        )
        self._ok = 0
        self._fail = 0
        self._latencies = []

    async def tick(self, bot: Bot, repo: Repo) -> dict:
        self._maybe_prune()
        measured_at = to_iso(now_utc())
        node_raw, mihomo_error = await fetch_auto_now(self.config)
        node_name, subscription = parse_node(node_raw)
        ok, latency_ms, bot_error = await measure_bot_latency(
            bot, self.config.vpn_monitor_timeout_seconds
        )
        error_parts = [part for part in (mihomo_error, bot_error) if part]
        error = "; ".join(error_parts) if error_parts else None
        sample = {
            "measured_at": measured_at,
            "ok": ok,
            "latency_ms": latency_ms,
            "node_name": node_name,
            "subscription": subscription,
            "error": error,
        }
        await repo.insert_vpn_sample(
            measured_at, ok, latency_ms, node_name, subscription, error
        )
        if self.config.vpn_log_dir is not None:
            try:
                append_vpn_log(self.config.vpn_log_dir, sample)
            except Exception:
                logger.exception("VPN ndjson append failed")
        self._ticks += 1
        if ok:
            self._ok += 1
            self._latencies.append(latency_ms)
        else:
            self._fail += 1
        self._flush_summary()
        return sample

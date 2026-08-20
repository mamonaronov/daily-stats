"""Measure Telegram Bot API RTT and the current mihomo AUTO node."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiogram import Bot

from config import Config
from database.models import VpnLatencySample
from database.queries import Repo
from utils.logging import TOKEN_RE, log_extra
from utils.time import format_dt_compact, format_dt_full, now_utc, parse_iso, to_iso
from utils.timeouts import await_or_abandon, reset_bot_session
from utils.uptime import host_uptime_seconds

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


def _iso_in_window(value: str | None, start: str, end: str) -> bool:
    if not value:
        return False
    return start <= value < end


def collect_vpn_log_entries(log_dir: Path | None, start: str, end: str) -> list[dict]:
    """Load ndjson samples whose measured_at is in [start, end)."""
    if log_dir is None or not log_dir.exists():
        return []
    start_day = start[:10] if len(start) >= 10 else ""
    end_day = end[:10] if len(end) >= 10 else ""
    if start_day > end_day:
        start_day, end_day = end_day, start_day
    entries: list[dict] = []
    for path in sorted(log_dir.glob("*.ndjson")):
        day = path.stem
        if len(day) != 10:
            continue
        if start_day and day < start_day:
            continue
        if end_day and day > end_day:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("VPN log read failed: %s", path)
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if _iso_in_window(str(payload.get("measured_at") or ""), start, end):
                entries.append(payload)
    return entries


def _iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_iso(value)
    except ValueError:
        return None


def format_vpn_log(samples: list[dict], start: str, end: str, tz_name: str = "UTC") -> str:
    ok = sum(1 for sample in samples if sample.get("ok") in (True, 1))
    fail = len(samples) - ok
    start_dt = _iso_to_dt(start)
    end_dt = _iso_to_dt(end)
    start_human = format_dt_full(start_dt, tz_name) if start_dt else start
    end_human = format_dt_full(end_dt, tz_name) if end_dt else end
    rows: list[tuple[str, ...]] = []
    for sample in samples:
        raw = str(sample.get("measured_at") or "—")
        measured = _iso_to_dt(raw if raw != "—" else None)
        human = format_dt_compact(measured, tz_name) if measured else "—"
        status = "ok" if sample.get("ok") in (True, 1) else "FAIL"
        ms = sample.get("latency_ms")
        ms_s = f"{ms}ms" if ms is not None else "—"
        error = str(sample.get("error") or "—")
        node = str(sample.get("node_name") or "—")
        sub = str(sample.get("subscription") or "—")
        rows.append((human, status, ms_s, error, node, sub, raw))
    headers = ("время", "статус", "пинг", "ошибка", "нода", "подписка", "ISO")
    widths = [len(title) for title in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    widths[0] = max(widths[0], 19)
    right_align = {2}

    def fmt(cells: tuple[str, ...]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            parts.append(f"{cell:>{widths[i]}}" if i in right_align else f"{cell:<{widths[i]}}")
        return "  ".join(parts)

    lines = [
        f"VPN logs  {start_human} — {end_human}  ({tz_name})",
        f"ISO  {start} .. {end}",
        f"samples: {len(samples)}  ok: {ok}  fail: {fail}",
        "",
        fmt(headers),
    ]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines) + "\n"


def vpn_samples_as_dicts(samples: list[VpnLatencySample]) -> list[dict]:
    result: list[dict] = []
    for sample in samples:
        result.append(
            {
                "measured_at": sample.measured_at,
                "ok": bool(sample.ok),
                "latency_ms": sample.latency_ms,
                "node_name": sample.node_name,
                "subscription": sample.subscription,
                "error": sample.error,
            }
        )
    return result


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


def make_probe_bot(config: Config) -> Bot:
    """Separate Bot/session for getMe probes so a hung ping cannot kill polling."""
    session = None
    if config.telegram_proxy_url:
        from aiogram.client.session.aiohttp import AiohttpSession

        session = AiohttpSession(
            proxy=config.telegram_proxy_url,
            timeout=float(config.vpn_monitor_timeout_seconds),
        )
    return Bot(token=config.bot_token, session=session)


async def measure_bot_latency(bot: Bot, timeout: float) -> tuple[bool, int, str | None]:
    started = time.monotonic()
    try:
        await await_or_abandon(bot.get_me(), timeout, name="bot.get_me")
        latency_ms = int((time.monotonic() - started) * 1000)
        return True, latency_ms, None
    except TimeoutError:
        latency_ms = int((time.monotonic() - started) * 1000)
        await reset_bot_session(bot)
        return False, latency_ms, "timeout"
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return False, latency_ms, sanitize_error(exc)


class VpnMonitor:
    def __init__(self, config: Config, probe_bot: Bot | None = None) -> None:
        self.config = config
        self._probe_bot = probe_bot
        self._ticks = 0
        self._ok = 0
        self._fail = 0
        self._latencies: list[int] = []
        self._last_prune_day: date | None = None

    def _probe(self, bot: Bot) -> Bot:
        return self._probe_bot if self._probe_bot is not None else bot

    async def reset_probe(self) -> None:
        if self._probe_bot is not None:
            await reset_bot_session(self._probe_bot)

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
            self._probe(bot), self.config.vpn_monitor_timeout_seconds
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
            measured_at, ok, latency_ms, node_name, subscription, error, host_uptime_seconds()
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

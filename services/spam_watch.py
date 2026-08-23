"""Watch for joke/spam diary filling and notify the owner. Never blocks the user."""

from __future__ import annotations

import asyncio
import html
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

from aiogram import Bot

from config import Config
from database.models import User
from database.queries import Repo
from keyboards.main import spam_alert_kb
from services.alerts import notify_owner
from utils.formatting import money
from utils.time import day_bounds_utc, format_date_long, local_date_of, to_iso, user_today

logger = logging.getLogger(__name__)

KIND_BUTTONS = "buttons"
KIND_WRITES = "writes"
KIND_DAILY = "daily"
KIND_ROWS = "rows"

REASON_LABELS = {
    KIND_BUTTONS: "спам кнопок",
    KIND_WRITES: "пачка записей за короткое время",
    KIND_DAILY: "нереалистично много записей за день",
    KIND_ROWS: "много строк дневника в базе",
}

_COALESCE_SECONDS = 0.25

_watch: SpamWatch | None = None


class BurstCounter:
    def __init__(self) -> None:
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def hit(self, key: int, now: float, window: float) -> int:
        q = self._hits[key]
        q.append(now)
        return self.count(key, now, window)

    def count(self, key: int, now: float, window: float) -> int:
        q = self._hits.get(key)
        if not q:
            return 0
        cutoff = now - window
        while q and q[0] < cutoff:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
            return 0
        return len(q)


@dataclass
class _Pending:
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    db_user: User | None = None
    last_callback: str | None = None
    last_action: str | None = None
    when: datetime | None = None


@dataclass
class AbuseSnapshot:
    telegram_id: int
    reasons: list[str]
    name: str
    username: str | None
    status: str
    balance_text: str
    timezone: str
    button_count: int
    button_window: int
    button_limit: int
    last_callback: str | None
    write_count: int
    write_window: int
    write_limit: int
    last_action: str | None
    daily_count: int
    daily_limit: int
    daily_label: str
    total_rows: int
    row_limit: int


class SpamWatch:
    def __init__(
        self,
        bot: Bot,
        repo: Repo,
        config: Config,
        *,
        clock=monotonic,
        coalesce: float = _COALESCE_SECONDS,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.config = config
        self._clock = clock
        self._coalesce = max(0.0, coalesce)
        self.buttons = BurstCounter()
        self.writes = BurstCounter()
        self._pending: dict[int, _Pending] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._alerted_at: dict[tuple[int, str], float] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _skip(self, telegram_id: int) -> bool:
        return telegram_id == self.config.owner_id

    def note_button(
        self,
        telegram_id: int,
        callback_data: str | None,
        *,
        username: str | None = None,
        first_name: str | None = None,
        db_user: User | None = None,
    ) -> None:
        if self._skip(telegram_id) or self.config.spam_button_count <= 0:
            return
        now = self._clock()
        self.buttons.hit(telegram_id, now, float(self.config.spam_button_window_seconds))
        self._touch(
            telegram_id,
            username=username,
            first_name=first_name,
            db_user=db_user,
            last_callback=callback_data,
        )
        self._schedule(telegram_id)

    def note_write(self, user: User, action: str, when: datetime | None = None) -> None:
        if self._skip(user.telegram_id):
            return
        if (
            self.config.spam_write_count <= 0
            and self.config.spam_daily_entries <= 0
            and self.config.spam_user_rows <= 0
        ):
            return
        now = self._clock()
        self.writes.hit(user.telegram_id, now, float(self.config.spam_write_window_seconds))
        self._touch(
            user.telegram_id,
            username=user.username,
            first_name=user.first_name,
            db_user=user,
            last_action=action,
            when=when,
        )
        self._schedule(user.telegram_id)

    def _touch(self, telegram_id: int, **fields: Any) -> None:
        current = self._pending.get(telegram_id) or _Pending(telegram_id=telegram_id)
        for key, value in fields.items():
            if value is not None:
                setattr(current, key, value)
        self._pending[telegram_id] = current

    def _schedule(self, telegram_id: int) -> None:
        task = self._tasks.get(telegram_id)
        if task is not None and not task.done():
            return
        self._tasks[telegram_id] = asyncio.create_task(
            self._run(telegram_id),
            name=f"spam_watch:{telegram_id}",
        )

    async def _run(self, telegram_id: int) -> None:
        try:
            if self._coalesce:
                await asyncio.sleep(self._coalesce)
            await self.evaluate(telegram_id)
        except Exception:
            logger.exception("Spam watch failed for %s", telegram_id)
        finally:
            self._tasks.pop(telegram_id, None)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    def _cooling(self, telegram_id: int, kind: str, now: float) -> bool:
        last = self._alerted_at.get((telegram_id, kind))
        if last is None:
            return False
        return (now - last) < self.config.spam_alert_cooldown_minutes * 60

    def _mark(self, telegram_id: int, kinds: list[str], now: float) -> None:
        for kind in kinds:
            self._alerted_at[(telegram_id, kind)] = now

    async def evaluate(self, telegram_id: int) -> AbuseSnapshot | None:
        async with self._locks[telegram_id]:
            pending = self._pending.get(telegram_id)
            if pending is None:
                return None
            snapshot = await self._snapshot(pending)
            if snapshot is None or not snapshot.reasons:
                return None
            now = self._clock()
            fresh = [kind for kind in snapshot.reasons if not self._cooling(telegram_id, kind, now)]
            if not fresh:
                return None
            snapshot.reasons = fresh
            self._mark(telegram_id, fresh, now)
        logger.warning(
            "spam_watch user=%s reasons=%s buttons=%s writes=%s daily=%s rows=%s",
            telegram_id,
            ",".join(snapshot.reasons),
            snapshot.button_count,
            snapshot.write_count,
            snapshot.daily_count,
            snapshot.total_rows,
        )
        await notify_owner(
            self.bot,
            self.config,
            format_spam_alert(snapshot),
            reply_markup=spam_alert_kb(telegram_id),
        )
        return snapshot

    async def _snapshot(self, pending: _Pending) -> AbuseSnapshot | None:
        config = self.config
        now = self._clock()
        user = pending.db_user
        if user is None:
            user = await self.repo.get_user(pending.telegram_id)
        tz = user.timezone if user else config.default_timezone
        day = local_date_of(pending.when, tz) if pending.when else user_today(tz)
        start, end = day_bounds_utc(tz, day)
        daily = 0
        total = 0
        if user is not None:
            daily = await self.repo.count_user_entries_between(
                pending.telegram_id, to_iso(start), to_iso(end)
            )
            total = await self.repo.count_user_entries(pending.telegram_id)
        button_count = self.buttons.count(
            pending.telegram_id, now, float(config.spam_button_window_seconds)
        )
        write_count = self.writes.count(
            pending.telegram_id, now, float(config.spam_write_window_seconds)
        )
        reasons: list[str] = []
        if config.spam_button_count > 0 and button_count >= config.spam_button_count:
            reasons.append(KIND_BUTTONS)
        if config.spam_write_count > 0 and write_count >= config.spam_write_count:
            reasons.append(KIND_WRITES)
        if config.spam_daily_entries > 0 and daily >= config.spam_daily_entries:
            reasons.append(KIND_DAILY)
        if config.spam_user_rows > 0 and total >= config.spam_user_rows:
            reasons.append(KIND_ROWS)
        if not reasons:
            return None
        name = (
            (user.display_name if user else None)
            or pending.first_name
            or pending.username
            or str(pending.telegram_id)
        )
        return AbuseSnapshot(
            telegram_id=pending.telegram_id,
            reasons=reasons,
            name=name,
            username=(user.username if user else pending.username),
            status=user.status if user else "не зарегистрирован",
            balance_text=money(user.balance) if user else "—",
            timezone=tz,
            button_count=button_count,
            button_window=config.spam_button_window_seconds,
            button_limit=config.spam_button_count,
            last_callback=pending.last_callback,
            write_count=write_count,
            write_window=config.spam_write_window_seconds,
            write_limit=config.spam_write_count,
            last_action=pending.last_action,
            daily_count=daily,
            daily_limit=config.spam_daily_entries,
            daily_label=f"{format_date_long(day)} ({tz})",
            total_rows=total,
            row_limit=config.spam_user_rows,
        )


def format_spam_alert(snapshot: AbuseSnapshot) -> str:
    reasons = ", ".join(REASON_LABELS.get(kind, kind) for kind in snapshot.reasons)
    who = html.escape(snapshot.name)
    if snapshot.username:
        who += f" (@{html.escape(snapshot.username)})"
    lines = [
        "⚠️ <b>Подозрительная активность</b>",
        "Пользователь <b>не ограничен</b> — это только уведомление. Решите сами, блокировать ли его.",
        "",
        f"Причина: {html.escape(reasons)}",
        f"Пользователь: {who}",
        f"ID: <code>{snapshot.telegram_id}</code>",
        f"Статус: {html.escape(snapshot.status)}",
        f"Баланс: {html.escape(snapshot.balance_text)}",
        f"Пояс: {html.escape(snapshot.timezone)}",
        "",
        "<b>Детали</b>",
        (
            f"• Нажатий за {snapshot.button_window} с: {snapshot.button_count}"
            f" (порог {snapshot.button_limit or 'выкл.'})"
        ),
    ]
    if snapshot.last_callback:
        lines.append(f"• Последняя кнопка: <code>{html.escape(snapshot.last_callback[:48])}</code>")
    lines.append(
        f"• Записей за {snapshot.write_window} с: {snapshot.write_count}"
        f" (порог {snapshot.write_limit or 'выкл.'})"
    )
    if snapshot.last_action:
        lines.append(f"• Последняя запись: {html.escape(snapshot.last_action)}")
    lines.extend(
        [
            (
                f"• Записей за {html.escape(snapshot.daily_label)}: {snapshot.daily_count}"
                f" (порог {snapshot.daily_limit or 'выкл.'})"
            ),
            (
                f"• Всего записей дневника в БД: {snapshot.total_rows}"
                f" (порог {snapshot.row_limit or 'выкл.'})"
            ),
        ]
    )
    return "\n".join(lines)


def get_spam_watch() -> SpamWatch | None:
    return _watch


def set_spam_watch(watch: SpamWatch | None) -> None:
    global _watch
    _watch = watch


def note_button(
    telegram_id: int,
    callback_data: str | None,
    *,
    username: str | None = None,
    first_name: str | None = None,
    db_user: User | None = None,
) -> None:
    watch = get_spam_watch()
    if watch is not None:
        watch.note_button(
            telegram_id,
            callback_data,
            username=username,
            first_name=first_name,
            db_user=db_user,
        )


def note_write(user: User, action: str, when: datetime | None = None) -> None:
    watch = get_spam_watch()
    if watch is not None:
        watch.note_write(user, action, when)

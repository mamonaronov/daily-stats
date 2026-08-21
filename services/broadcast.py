"""Copy an admin message to a chosen group of users, including photos and albums."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from database.models import User
from database.queries import Repo
from services.users import can_write

logger = logging.getLogger(__name__)

_COPY_TIMEOUT = 20
_MAX_FLOOD_RETRIES = 3
_MAX_FLOOD_SLEEP = 30.0
DEFAULT_SEND_DELAY = 0.05
DEFAULT_ALBUM_WAIT = 0.8

AUDIENCE_LABELS = {
    "all": "все активные",
    "paid": "с доступом",
    "unpaid": "без оплаты",
}


@dataclass(slots=True)
class BroadcastResult:
    total: int
    sent: int
    blocked: int
    failed: int
    audience: str = "all"


class AlbumBuffer:
    """Collect Telegram album parts that arrive as separate updates."""

    def __init__(self, wait: float = DEFAULT_ALBUM_WAIT) -> None:
        self.wait = wait
        self._lock = asyncio.Lock()
        self._groups: dict[str, list[int]] = {}
        self._leaders: set[str] = set()

    async def add(self, group_id: str, message_id: int) -> list[int] | None:
        async with self._lock:
            is_leader = group_id not in self._leaders
            if is_leader:
                self._leaders.add(group_id)
            self._groups.setdefault(group_id, []).append(message_id)
        if not is_leader:
            return None
        if self.wait > 0:
            await asyncio.sleep(self.wait)
        async with self._lock:
            ids = self._groups.pop(group_id, [])
            self._leaders.discard(group_id)
        return sorted(set(ids))


album_buffer = AlbumBuffer()


def normalize_audience(audience: str | None) -> str:
    if audience in AUDIENCE_LABELS:
        return audience
    return "all"


def filter_broadcast_audience(users: list[User], audience: str) -> list[User]:
    key = normalize_audience(audience)
    if key == "paid":
        return [user for user in users if can_write(user)]
    if key == "unpaid":
        return [user for user in users if not can_write(user)]
    return list(users)


def audience_counts(users: list[User]) -> dict[str, int]:
    paid = sum(1 for user in users if can_write(user))
    return {"all": len(users), "paid": paid, "unpaid": len(users) - paid}


def format_broadcast_result(result: BroadcastResult) -> str:
    label = AUDIENCE_LABELS.get(result.audience, AUDIENCE_LABELS["all"])
    return (
        "📢 <b>Рассылка завершена</b>\n\n"
        f"Кому: {label}\n"
        f"Получателей: {result.total}\n"
        f"Доставлено: {result.sent}\n"
        f"Заблокировали бота: {result.blocked}\n"
        f"Не удалось: {result.failed}"
    )


def _blocked_or_gone(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "blocked by the user",
            "user is deactivated",
            "chat not found",
            "bot was kicked",
        )
    )


async def _copy_to_chat(
    bot: Bot,
    chat_id: int,
    from_chat_id: int,
    message_ids: list[int],
) -> None:
    if len(message_ids) == 1:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_ids[0],
            parse_mode=None,
            request_timeout=_COPY_TIMEOUT,
        )
        return
    try:
        await bot.copy_messages(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_ids=message_ids,
            request_timeout=_COPY_TIMEOUT,
        )
    except TelegramBadRequest:
        for message_id in message_ids:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
                parse_mode=None,
                request_timeout=_COPY_TIMEOUT,
            )


async def _deliver(
    bot: Bot,
    repo: Repo,
    chat_id: int,
    from_chat_id: int,
    message_ids: list[int],
) -> str:
    for attempt in range(_MAX_FLOOD_RETRIES + 1):
        try:
            await _copy_to_chat(bot, chat_id, from_chat_id, message_ids)
            return "sent"
        except TelegramRetryAfter as exc:
            if attempt >= _MAX_FLOOD_RETRIES:
                logger.warning("Broadcast flood wait exceeded for %s", chat_id)
                return "failed"
            await asyncio.sleep(min(float(exc.retry_after) + 0.1, _MAX_FLOOD_SLEEP))
        except TelegramForbiddenError:
            await repo.mark_bot_blocked(chat_id)
            return "blocked"
        except TelegramBadRequest as exc:
            if _blocked_or_gone(exc):
                await repo.mark_bot_blocked(chat_id)
                return "blocked"
            logger.warning("Broadcast bad request for %s: %s", chat_id, exc)
            return "failed"
        except Exception:
            logger.exception("Broadcast failed for %s", chat_id)
            return "failed"
    return "failed"


async def send_broadcast(
    bot: Bot,
    repo: Repo,
    *,
    from_chat_id: int,
    message_ids: list[int],
    audience: str = "all",
    include_telegram_id: int | None = None,
    delay: float = DEFAULT_SEND_DELAY,
) -> BroadcastResult:
    ids = sorted(set(message_ids))
    audience = normalize_audience(audience)
    if not ids:
        return BroadcastResult(total=0, sent=0, blocked=0, failed=0, audience=audience)
    users = filter_broadcast_audience(await repo.list_broadcast_users(), audience)
    if include_telegram_id is not None and all(user.telegram_id != include_telegram_id for user in users):
        extra = await repo.get_user(include_telegram_id)
        if extra is not None:
            users = [extra, *users]
    sent = blocked = failed = 0
    for index, user in enumerate(users):
        if delay > 0 and index:
            await asyncio.sleep(delay)
        outcome = await _deliver(bot, repo, user.telegram_id, from_chat_id, ids)
        if outcome == "sent":
            sent += 1
        elif outcome == "blocked":
            blocked += 1
        else:
            failed += 1
    return BroadcastResult(total=len(users), sent=sent, blocked=blocked, failed=failed, audience=audience)

"""Named event markers and optional periods linking two markers."""

from __future__ import annotations

from datetime import datetime

from database.models import EventPeriod, User
from database.queries import Repo
from services.users import write_block_message
from utils.time import parse_iso, to_iso

NAME_MAX = 80
COMMENT_MAX = 500


def normalize_name(raw: str) -> str | None:
    name = " ".join((raw or "").split())
    if not name:
        return None
    return name[:NAME_MAX]


def normalize_comment(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return text[:COMMENT_MAX]


async def add_marker(
    repo: Repo,
    user: User,
    name: str,
    when: datetime,
    comment: str | None = None,
    *,
    as_period_start: bool = False,
    close_period_id: int | None = None,
) -> tuple[int | None, str | None]:
    blocked = write_block_message(user)
    if blocked:
        return None, blocked
    title = normalize_name(name)
    if not title:
        return None, "Нужно название метки."
    if as_period_start and close_period_id is not None:
        return None, "Метка не может быть и началом, и концом сразу."
    note = normalize_comment(comment)
    item_id = await repo.add_marker(user.telegram_id, to_iso(when), title, note)
    if as_period_start:
        await repo.add_period(user.telegram_id, item_id, None)
        return item_id, None
    if close_period_id is not None:
        error = await attach_period_end(repo, user, close_period_id, item_id)
        if error:
            await repo.delete_marker(item_id, user.telegram_id)
            return None, error
    return item_id, None


async def attach_period_end(
    repo: Repo,
    user: User,
    period_id: int,
    end_marker_id: int,
) -> str | None:
    period = await repo.get_period(period_id, user.telegram_id)
    if period is None:
        return "Период не найден."
    if period.end_marker_id is not None:
        return "У этого периода уже есть конец."
    end = await repo.get_marker(end_marker_id, user.telegram_id)
    if end is None:
        return "Метка не найдена."
    if end.id == period.start_marker_id:
        return "Конец совпадает с началом."
    taken = await repo.get_period_for_marker(end.id, user.telegram_id)
    if taken is not None and taken.id != period.id:
        return "Эта метка уже в другом периоде."
    if period.start_at and parse_iso(end.occurred_at) < parse_iso(period.start_at):
        return "Конец должен быть позже начала."
    await repo.set_period_end(period.id, user.telegram_id, end.id)
    return None


async def link_markers(
    repo: Repo,
    user: User,
    first_id: int,
    second_id: int,
) -> tuple[int | None, str | None]:
    blocked = write_block_message(user)
    if blocked:
        return None, blocked
    if first_id == second_id:
        return None, "Нужны две разные метки."
    first = await repo.get_marker(first_id, user.telegram_id)
    second = await repo.get_marker(second_id, user.telegram_id)
    if first is None or second is None:
        return None, "Метка не найдена."
    if await repo.get_period_for_marker(first.id, user.telegram_id):
        return None, "Первая метка уже в периоде."
    if await repo.get_period_for_marker(second.id, user.telegram_id):
        return None, "Вторая метка уже в периоде."
    if parse_iso(first.occurred_at) <= parse_iso(second.occurred_at):
        start, end = first, second
    else:
        start, end = second, first
    period_id = await repo.add_period(user.telegram_id, start.id, end.id)
    return period_id, None


async def unlink_period(repo: Repo, user: User, period_id: int) -> str | None:
    blocked = write_block_message(user)
    if blocked:
        return blocked
    if not await repo.unlink_period(period_id, user.telegram_id):
        return "Период не найден."
    return None


def period_title(period: EventPeriod) -> str:
    start = period.start_name or "Метка"
    if period.is_open:
        return start
    end = period.end_name or start
    if start == end:
        return start
    return f"{start} — {end}"

"""Create/update diary entries after write-access checks."""

from __future__ import annotations

from datetime import datetime

from database.models import User
from database.queries import Repo
from services.users import write_block_message
from utils.time import parse_iso, to_iso


def _duration(bed: str | None, wake: str | None) -> int | None:
    if not bed or not wake:
        return None
    delta = parse_iso(wake) - parse_iso(bed)
    minutes = int(delta.total_seconds() // 60)
    if minutes < 0:
        minutes += 24 * 60
    return minutes


def _elapsed_minutes(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    minutes = int((parse_iso(end) - parse_iso(start)).total_seconds() // 60)
    if minutes < 0:
        return None
    return minutes


async def require_write(user: User) -> str | None:
    return write_block_message(user)


async def add_cigarette(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_cigarette(user.telegram_id, to_iso(when))
    return item_id, None


async def add_fooling(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_fooling(user.telegram_id, to_iso(when))
    return item_id, None


async def add_sleep_bed(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_sleep(user.telegram_id, to_iso(when), None, None, None)
    return item_id, None


async def add_sleep_wake(repo: Repo, user: User, when: datetime, quality: int | None) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    open_rec = await repo.latest_open_sleep(user.telegram_id)
    iso = to_iso(when)
    if open_rec:
        duration = _duration(open_rec.bedtime, iso)
        await repo.update_sleep(open_rec.id, user.telegram_id, wake_time=iso, duration_minutes=duration, quality=quality)
        return open_rec.id, None
    item_id = await repo.add_sleep(user.telegram_id, None, iso, None, quality)
    return item_id, None


async def add_snus_bought(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_snus_pack(user.telegram_id, to_iso(when), None, None)
    return item_id, None


async def add_snus_finished(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    open_rec = await repo.oldest_open_snus(user.telegram_id)
    if open_rec is None:
        return None, "Нет открытой шайбы. Сначала отметьте покупку."
    iso = to_iso(when)
    duration = _elapsed_minutes(open_rec.bought_at, iso)
    if duration is None:
        return None, "Время окончания раньше покупки."
    await repo.update_snus_pack(
        open_rec.id, user.telegram_id, finished_at=iso, duration_minutes=duration
    )
    return open_rec.id, None


async def add_mood(repo: Repo, user: User, score: int, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return await repo.add_mood(user.telegram_id, score, to_iso(when)), None


async def add_wellbeing(repo: Repo, user: User, score: int, comment: str | None, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return await repo.add_wellbeing(user.telegram_id, score, comment, to_iso(when)), None


async def add_caffeine(repo: Repo, user: User, drink_type: str, amount: float | None, unit: str | None, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return await repo.add_caffeine(user.telegram_id, drink_type, amount, unit, to_iso(when)), None


async def add_alcohol(repo: Repo, user: User, drink_type: str, amount: float | None, unit: str | None, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return await repo.add_alcohol(user.telegram_id, drink_type, amount, unit, to_iso(when)), None


async def add_activity(
    repo: Repo,
    user: User,
    activity_type: str,
    duration_minutes: int | None,
    comment: str | None,
    when: datetime,
) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return await repo.add_activity(user.telegram_id, activity_type, duration_minutes, comment, to_iso(when)), None


async def add_note(repo: Repo, user: User, body: str, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return await repo.add_note(user.telegram_id, body, to_iso(when)), None


async def add_custom_value(repo: Repo, user: User, metric_id: int, when: datetime, **values) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_metric_value(user.telegram_id, metric_id, to_iso(when), **values)
    return item_id, None

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


def _sleep_duration(onset: str | None, wake: str | None) -> int | None:
    return _elapsed_minutes(onset, wake)


def _sync_bedtime(phone_in: str | None, phone_away: str | None) -> str | None:
    return phone_in or phone_away


async def add_sleep_phone_in(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    item_id = await repo.add_sleep(
        user.telegram_id,
        bedtime=iso,
        phone_in_bed_at=iso,
    )
    return item_id, None


async def add_sleep_phone_away(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    rec = await repo.latest_sleep(user.telegram_id)
    if rec is not None and rec.phase() == "with_phone":
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            phone_away_at=iso,
            bedtime=_sync_bedtime(rec.phone_in_bed_at, iso),
        )
        return rec.id, None
    item_id = await repo.add_sleep(
        user.telegram_id,
        bedtime=iso,
        phone_away_at=iso,
    )
    return item_id, None


async def add_sleep_wake(repo: Repo, user: User, when: datetime, quality: int | None) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    rec = await repo.latest_sleep(user.telegram_id)
    if rec is not None and rec.wake_time is None and rec.out_of_bed_at is None and rec.phase() in {"with_phone", "no_phone"}:
        duration = _sleep_duration(rec.sleep_onset_at, iso)
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            wake_time=iso,
            duration_minutes=duration,
            quality=quality,
        )
        return rec.id, None
    item_id = await repo.add_sleep(user.telegram_id, wake_time=iso, quality=quality)
    return item_id, None


async def add_sleep_wake_and_up(
    repo: Repo, user: User, when: datetime, quality: int | None
) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    rec = await repo.latest_sleep(user.telegram_id)
    if rec is not None and rec.wake_time is None and rec.out_of_bed_at is None and rec.phase() in {"with_phone", "no_phone"}:
        duration = _sleep_duration(rec.sleep_onset_at, iso)
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            wake_time=iso,
            out_of_bed_at=iso,
            duration_minutes=duration,
            quality=quality,
        )
        return rec.id, None
    item_id = await repo.add_sleep(
        user.telegram_id,
        wake_time=iso,
        quality=quality,
        out_of_bed_at=iso,
    )
    return item_id, None


async def add_sleep_up(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    rec = await repo.latest_sleep(user.telegram_id)
    if rec is None or rec.wake_time is None or rec.out_of_bed_at is not None:
        return None, "Сначала отметьте пробуждение."
    await repo.update_sleep(rec.id, user.telegram_id, out_of_bed_at=to_iso(when))
    return rec.id, None


async def add_sleep_onset(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    rec = await repo.latest_sleep(user.telegram_id)
    if rec is None or rec.sleep_onset_at is not None or rec.out_of_bed_at is None:
        return None, "Сначала отметьте, что встали с кровати."
    iso = to_iso(when)
    if rec.wake_time:
        elapsed = _sleep_duration(iso, rec.wake_time)
        if elapsed is None:
            return None, "Время засыпания позже пробуждения."
    else:
        elapsed = None
    await repo.update_sleep(
        rec.id,
        user.telegram_id,
        sleep_onset_at=iso,
        duration_minutes=elapsed,
    )
    return rec.id, None


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


async def add_custom_value(repo: Repo, user: User, metric_id: int, when: datetime, **values) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_metric_value(user.telegram_id, metric_id, to_iso(when), **values)
    return item_id, None


def _has_sleep_night(rec) -> bool:
    return bool(rec.phone_in_bed_at or rec.phone_away_at or rec.bedtime)


async def undo_entry(repo: Repo, user: User, kind: str, item_id: int) -> str | None:
    """Undo a just-saved action. None means success."""
    blocked = await require_write(user)
    if blocked:
        return blocked
    tid = user.telegram_id
    if kind == "snf":
        rec = await repo.get_snus_pack(item_id, tid)
        if rec is None:
            return "Запись не найдена."
        await repo.update_snus_pack(item_id, tid, finished_at=None, duration_minutes=None)
        return None
    if kind in {"sa", "sw", "su", "so", "wu", "slp"}:
        rec = await repo.get_sleep(item_id, tid)
        if rec is None:
            return "Запись не найдена."
        if kind == "sa":
            if rec.phone_in_bed_at:
                await repo.update_sleep(
                    item_id,
                    tid,
                    phone_away_at=None,
                    bedtime=rec.phone_in_bed_at,
                )
                return None
            await repo.delete_sleep(item_id, tid)
            return None
        if kind == "sw":
            if _has_sleep_night(rec):
                await repo.update_sleep(
                    item_id,
                    tid,
                    wake_time=None,
                    duration_minutes=None,
                    quality=None,
                )
                return None
            await repo.delete_sleep(item_id, tid)
            return None
        if kind == "wu":
            if _has_sleep_night(rec):
                await repo.update_sleep(
                    item_id,
                    tid,
                    wake_time=None,
                    out_of_bed_at=None,
                    duration_minutes=None,
                    quality=None,
                )
                return None
            await repo.delete_sleep(item_id, tid)
            return None
        if kind == "su":
            await repo.update_sleep(item_id, tid, out_of_bed_at=None)
            return None
        if kind == "so":
            await repo.update_sleep(item_id, tid, sleep_onset_at=None, duration_minutes=None)
            return None
        await repo.delete_sleep(item_id, tid)
        return None
    mapping = {
        "cig": repo.delete_cigarette,
        "fool": repo.delete_fooling,
        "snb": repo.delete_snus_pack,
        "sb": repo.delete_sleep,
        "sp": repo.delete_sleep,
        "caf": repo.delete_caffeine,
        "alc": repo.delete_alcohol,
        "act": repo.delete_activity,
        "cm": repo.delete_metric_value,
    }
    fn = mapping.get(kind)
    if fn is None:
        return "Этот тип записи нельзя отменить."
    if not await fn(item_id, tid):
        return "Запись не найдена."
    return None

"""Create/update diary entries after write-access checks."""

from __future__ import annotations

from datetime import datetime

from database.models import User
from database.queries import Repo
from services.spam_watch import note_write
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


def _saved(user: User, action: str, when: datetime, item_id: int) -> tuple[int, None]:
    note_write(user, action, when)
    return item_id, None


async def add_cigarette(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return _saved(user, "сигарета", when, await repo.add_cigarette(user.telegram_id, to_iso(when)))


async def add_fooling(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    return _saved(user, "валять дурака", when, await repo.add_fooling(user.telegram_id, to_iso(when)))


def _sleep_duration(onset: str | None, wake: str | None) -> int | None:
    return _elapsed_minutes(onset, wake)


def _sync_bedtime(phone_in: str | None, phone_away: str | None) -> str | None:
    return phone_in or phone_away


def _has_sleep_night(rec) -> bool:
    return bool(rec.phone_in_bed_at or rec.phone_away_at or rec.bedtime)


def _not_after(earlier: str | None, later: str | None) -> bool:
    if not earlier or not later:
        return True
    return _elapsed_minutes(earlier, later) is not None


def _attach_bed_to_open(rec, iso: str) -> bool:
    if rec is None or _has_sleep_night(rec):
        return False
    if rec.wake_time and _elapsed_minutes(iso, rec.wake_time) is not None:
        return True
    if rec.sleep_onset_at and _elapsed_minutes(iso, rec.sleep_onset_at) is not None:
        return True
    return False


def _onset_before_rise(rec, iso: str) -> bool:
    return _not_after(iso, rec.wake_time) and _not_after(iso, rec.out_of_bed_at)


def _onset_fits(rec, iso: str) -> bool:
    if rec.sleep_onset_at:
        return False
    if rec.wake_time or rec.out_of_bed_at:
        return _onset_before_rise(rec, iso)
    if not _has_sleep_night(rec):
        return False
    bed = rec.phone_away_at or rec.phone_in_bed_at or rec.bedtime
    return _not_after(bed, iso)


def _is_orphan_onset(item, rec_id: int, before_iso: str | None) -> bool:
    if item.id == rec_id or not item.sleep_onset_at:
        return False
    if item.wake_time is not None or item.out_of_bed_at is not None:
        return False
    if _has_sleep_night(item):
        return False
    return bool(before_iso) and _not_after(item.sleep_onset_at, before_iso)


def needs_onset_prompt(rec, records: list) -> bool:
    if rec is None or rec.sleep_onset_at:
        return False
    bound = rec.wake_time or rec.out_of_bed_at
    for other in records:
        if other.id == rec.id:
            continue
        if not other.sleep_onset_at:
            continue
        if rec.wake_time and other.wake_time == rec.wake_time:
            return False
        if _is_orphan_onset(other, rec.id, bound):
            return False
    return True


def _wake_fits(rec, iso: str) -> bool:
    if rec.wake_time is not None or rec.out_of_bed_at is not None:
        return False
    if rec.phase() not in {"with_phone", "no_phone", "asleep"}:
        return False
    bed = rec.phone_away_at or rec.phone_in_bed_at or rec.bedtime
    return _not_after(bed, iso) and _not_after(rec.sleep_onset_at, iso)


def _up_fits_after_wake(rec, iso: str) -> bool:
    return bool(rec.wake_time and rec.out_of_bed_at is None and _not_after(rec.wake_time, iso))


def _up_fits_open_night(rec, iso: str) -> bool:
    if rec.wake_time is not None or rec.out_of_bed_at is not None:
        return False
    if not _has_sleep_night(rec) and not rec.sleep_onset_at:
        return False
    bed = rec.phone_away_at or rec.phone_in_bed_at or rec.bedtime
    return _not_after(bed, iso) and _not_after(rec.sleep_onset_at, iso)


def _away_fits_with_phone(rec, iso: str) -> bool:
    return rec.phase() == "with_phone" and _not_after(rec.phone_in_bed_at, iso)


def _pick_sleep(records: list, pred) -> object | None:
    for rec in records:
        if pred(rec):
            return rec
    return None


async def add_sleep_phone_in(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    records = await repo.list_recent_sleep(user.telegram_id)
    rec = _pick_sleep(records, lambda item: _attach_bed_to_open(item, iso))
    if rec is not None:
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            phone_in_bed_at=iso,
            bedtime=iso,
        )
        return _saved(user, "сон (с телефоном)", when, rec.id)
    item_id = await repo.add_sleep(
        user.telegram_id,
        bedtime=iso,
        phone_in_bed_at=iso,
    )
    return _saved(user, "сон (с телефоном)", when, item_id)


async def add_sleep_phone_away(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    records = await repo.list_recent_sleep(user.telegram_id)
    rec = _pick_sleep(records, lambda item: _away_fits_with_phone(item, iso))
    if rec is not None:
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            phone_away_at=iso,
            bedtime=_sync_bedtime(rec.phone_in_bed_at, iso),
        )
        return _saved(user, "сон (убрал телефон)", when, rec.id)
    rec = _pick_sleep(records, lambda item: _attach_bed_to_open(item, iso))
    if rec is not None:
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            phone_away_at=iso,
            bedtime=iso,
        )
        return _saved(user, "сон (без телефона)", when, rec.id)
    item_id = await repo.add_sleep(
        user.telegram_id,
        bedtime=iso,
        phone_away_at=iso,
    )
    return _saved(user, "сон (без телефона)", when, item_id)


async def add_sleep_wake(
    repo: Repo, user: User, when: datetime, quality: int | None, wake_kind: str | None = None
) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    rec = _pick_sleep(await repo.list_recent_sleep(user.telegram_id), lambda item: _wake_fits(item, iso))
    if rec is not None:
        duration = _sleep_duration(rec.sleep_onset_at, iso)
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            wake_time=iso,
            duration_minutes=duration,
            quality=quality,
            wake_kind=wake_kind,
        )
        return _saved(user, "сон (проснулся)", when, rec.id)
    item_id = await repo.add_sleep(user.telegram_id, wake_time=iso, quality=quality, wake_kind=wake_kind)
    return _saved(user, "сон (проснулся)", when, item_id)


async def add_sleep_wake_and_up(
    repo: Repo,
    user: User,
    when: datetime,
    quality: int | None,
    wake_kind: str | None = None,
) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    rec = _pick_sleep(await repo.list_recent_sleep(user.telegram_id), lambda item: _wake_fits(item, iso))
    if rec is not None:
        duration = _sleep_duration(rec.sleep_onset_at, iso)
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            wake_time=iso,
            out_of_bed_at=iso,
            duration_minutes=duration,
            quality=quality,
            wake_kind=wake_kind,
        )
        return _saved(user, "сон (проснулся и встал)", when, rec.id)
    item_id = await repo.add_sleep(
        user.telegram_id,
        wake_time=iso,
        quality=quality,
        out_of_bed_at=iso,
        wake_kind=wake_kind,
    )
    return _saved(user, "сон (проснулся и встал)", when, item_id)


async def _absorb_orphan_onset(repo: Repo, user: User, rec, before_iso: str) -> str | None:
    if rec.sleep_onset_at:
        return rec.sleep_onset_at
    orphan = _pick_sleep(
        await repo.list_recent_sleep(user.telegram_id),
        lambda item: _is_orphan_onset(item, rec.id, before_iso),
    )
    if orphan is None:
        return None
    await repo.delete_sleep(orphan.id, user.telegram_id)
    return orphan.sleep_onset_at


async def add_sleep_up(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    records = await repo.list_recent_sleep(user.telegram_id)
    rec = _pick_sleep(records, lambda item: _up_fits_after_wake(item, iso))
    if rec is not None:
        fields: dict = {"out_of_bed_at": iso}
        onset = await _absorb_orphan_onset(repo, user, rec, rec.wake_time or iso)
        if onset and onset != rec.sleep_onset_at:
            fields["sleep_onset_at"] = onset
            fields["duration_minutes"] = _sleep_duration(onset, rec.wake_time)
        await repo.update_sleep(rec.id, user.telegram_id, **fields)
        return _saved(user, "сон (встал)", when, rec.id)
    rec = _pick_sleep(records, lambda item: _up_fits_open_night(item, iso))
    if rec is not None:
        duration = _sleep_duration(rec.sleep_onset_at, iso)
        await repo.update_sleep(
            rec.id,
            user.telegram_id,
            wake_time=iso,
            out_of_bed_at=iso,
            duration_minutes=duration,
        )
        return _saved(user, "сон (встал)", when, rec.id)
    item_id = await repo.add_sleep(user.telegram_id, wake_time=iso, out_of_bed_at=iso)
    rec = await repo.get_sleep(item_id, user.telegram_id)
    if rec is not None:
        onset = await _absorb_orphan_onset(repo, user, rec, iso)
        if onset:
            await repo.update_sleep(
                item_id,
                user.telegram_id,
                sleep_onset_at=onset,
                duration_minutes=_sleep_duration(onset, iso),
            )
    return _saved(user, "сон (встал)", when, item_id)


def _preferred_onset_record(records: list, prefer_id: int | None):
    if prefer_id is None:
        return None
    rec = next((item for item in records if item.id == prefer_id), None)
    if rec is None or rec.sleep_onset_at:
        return None
    return rec


async def add_sleep_onset(
    repo: Repo, user: User, when: datetime, *, prefer_id: int | None = None
) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    iso = to_iso(when)
    records = await repo.list_recent_sleep(user.telegram_id)
    rec = _preferred_onset_record(records, prefer_id)
    if rec is None:
        rec = _pick_sleep(records, lambda item: _onset_fits(item, iso))
    elif rec.wake_time or rec.out_of_bed_at:
        if not _onset_before_rise(rec, iso):
            return None, "Время засыпания позже пробуждения."
    elif _has_sleep_night(rec):
        bed = rec.phone_away_at or rec.phone_in_bed_at or rec.bedtime
        if not _not_after(bed, iso):
            rec = _pick_sleep(records, lambda item: _onset_fits(item, iso))
    if rec is None:
        item_id = await repo.add_sleep(user.telegram_id, sleep_onset_at=iso)
        return _saved(user, "сон (заснул)", when, item_id)
    elapsed = _sleep_duration(iso, rec.wake_time) if rec.wake_time else None
    if rec.wake_time and elapsed is None:
        return None, "Время засыпания позже пробуждения."
    await repo.update_sleep(
        rec.id,
        user.telegram_id,
        sleep_onset_at=iso,
        duration_minutes=elapsed,
    )
    return _saved(user, "сон (заснул)", when, rec.id)


async def add_snus_bought(repo: Repo, user: User, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_snus_pack(user.telegram_id, to_iso(when), None, None)
    return _saved(user, "снюс (покупка)", when, item_id)


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
    return _saved(user, "снюс (закончилась)", when, open_rec.id)


async def add_caffeine(repo: Repo, user: User, drink_type: str, amount: float | None, unit: str | None, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_caffeine(user.telegram_id, drink_type, amount, unit, to_iso(when))
    return _saved(user, "кофеин", when, item_id)


async def add_alcohol(repo: Repo, user: User, drink_type: str, amount: float | None, unit: str | None, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_alcohol(user.telegram_id, drink_type, amount, unit, to_iso(when))
    return _saved(user, "алкоголь", when, item_id)


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
    item_id = await repo.add_activity(
        user.telegram_id, activity_type, duration_minutes, comment, to_iso(when)
    )
    return _saved(user, "активность", when, item_id)


async def upsert_steps(
    repo: Repo, user: User, day, steps: int
) -> tuple[int | None, str | None, bool]:
    from datetime import date as date_type

    from utils.time import combine_local

    blocked = await require_write(user)
    if blocked:
        return None, blocked, False
    if not isinstance(day, date_type):
        day = date_type.fromisoformat(str(day))
    when = combine_local(user.timezone, day, 0, 0)
    item_id, updated = await repo.upsert_steps(
        user.telegram_id, day.isoformat(), steps, to_iso(when)
    )
    action = "шаги (обновление)" if updated else "шаги"
    note_write(user, action, when)
    return item_id, None, updated


async def upsert_daily_score(
    repo: Repo, user: User, day, kind: str, score: int
) -> tuple[int | None, str | None, bool]:
    from datetime import date as date_type

    from services.daily_scores import SCORE_BY_KEY, spec_of
    from utils.time import combine_local

    blocked = await require_write(user)
    if blocked:
        return None, blocked, False
    if kind not in SCORE_BY_KEY:
        return None, "Неизвестная оценка.", False
    if score < 1 or score > 5:
        return None, "Оценка от 1 до 5.", False
    if not isinstance(day, date_type):
        day = date_type.fromisoformat(str(day))
    when = combine_local(user.timezone, day, 0, 0)
    item_id, updated = await repo.upsert_daily_score(
        user.telegram_id, day.isoformat(), kind, score, to_iso(when)
    )
    label = spec_of(kind).label.lower()
    action = f"{label} (обновление)" if updated else label
    note_write(user, action, when)
    return item_id, None, updated


async def clear_daily_score(repo: Repo, user: User, day, kind: str) -> str | None:
    from datetime import date as date_type

    from services.daily_scores import SCORE_BY_KEY, spec_of
    from utils.time import combine_local

    blocked = await require_write(user)
    if blocked:
        return blocked
    if kind not in SCORE_BY_KEY:
        return "Неизвестная оценка."
    if not isinstance(day, date_type):
        day = date_type.fromisoformat(str(day))
    rec = await repo.get_daily_score_by_day(user.telegram_id, day.isoformat(), kind)
    if rec is None:
        return None
    if not await repo.delete_daily_score(rec.id, user.telegram_id):
        return "Запись не найдена."
    note_write(user, f"{spec_of(kind).label.lower()} (снято)", combine_local(user.timezone, day, 0, 0))
    return None


async def add_weight(repo: Repo, user: User, kilograms: float, when: datetime) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_weight(user.telegram_id, kilograms, to_iso(when))
    return _saved(user, "вес", when, item_id)


async def add_custom_value(repo: Repo, user: User, metric_id: int, when: datetime, **values) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    item_id = await repo.add_metric_value(user.telegram_id, metric_id, to_iso(when), **values)
    return _saved(user, "кастомная метрика", when, item_id)


async def start_metric_period(
    repo: Repo, user: User, metric_id: int, when: datetime
) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    metric = await repo.get_metric(metric_id, user.telegram_id)
    if metric is None or metric.data_type != "period" or not metric.enabled:
        return None, "Метрика недоступна"
    if await repo.get_open_metric_value(user.telegram_id, metric_id):
        return None, "Уже идёт — сначала закончите."
    item_id = await repo.add_metric_value(
        user.telegram_id, metric_id, to_iso(when), value_bool=1
    )
    return _saved(user, "кастомный интервал", when, item_id)


async def end_metric_period(
    repo: Repo,
    user: User,
    metric_id: int,
    when: datetime,
    *,
    start_at: datetime | None = None,
) -> tuple[int | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    metric = await repo.get_metric(metric_id, user.telegram_id)
    if metric is None or metric.data_type != "period" or not metric.enabled:
        return None, "Метрика недоступна"
    end_iso = to_iso(when)
    if start_at is not None:
        minutes = _elapsed_minutes(to_iso(start_at), end_iso)
        if minutes is None:
            return None, "Конец должен быть позже начала."
        item_id = await repo.add_metric_value(
            user.telegram_id,
            metric_id,
            to_iso(start_at),
            value_number=float(minutes),
            value_text=end_iso,
            value_bool=0,
        )
        return _saved(user, "кастомный интервал", when, item_id)
    open_rec = await repo.get_open_metric_value(user.telegram_id, metric_id)
    if open_rec is None:
        return None, "Сначала отметьте начало."
    minutes = _elapsed_minutes(open_rec.occurred_at, end_iso)
    if minutes is None:
        return None, "Конец должен быть позже начала."
    await repo.update_metric_value(
        open_rec.id,
        user.telegram_id,
        value_number=float(minutes),
        value_text=end_iso,
        value_bool=0,
    )
    return _saved(user, "кастомный интервал", when, open_rec.id)


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
    if kind == "cme":
        rec = await repo.get_metric_value(item_id, tid)
        if rec is None:
            return "Запись не найдена."
        await repo.update_metric_value(
            item_id, tid, value_number=None, value_text=None, value_bool=1
        )
        return None
    if kind in {"sa", "sw", "su", "so", "wu", "slp", "sp"}:
        rec = await repo.get_sleep(item_id, tid)
        if rec is None:
            return "Запись не найдена."
        if kind == "sp":
            if rec.wake_time or rec.out_of_bed_at or rec.sleep_onset_at or rec.phone_away_at:
                await repo.update_sleep(
                    item_id,
                    tid,
                    phone_in_bed_at=None,
                    bedtime=rec.phone_away_at,
                )
                return None
            await repo.delete_sleep(item_id, tid)
            return None
        if kind == "sa":
            if rec.phone_in_bed_at:
                await repo.update_sleep(
                    item_id,
                    tid,
                    phone_away_at=None,
                    bedtime=rec.phone_in_bed_at,
                )
                return None
            if rec.wake_time or rec.out_of_bed_at or rec.sleep_onset_at:
                await repo.update_sleep(item_id, tid, phone_away_at=None, bedtime=None)
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
                    wake_kind=None,
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
                    wake_kind=None,
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
        "caf": repo.delete_caffeine,
        "alc": repo.delete_alcohol,
        "act": repo.delete_activity,
        "stp": repo.delete_steps,
        "wgt": repo.delete_weight,
        "dsc": repo.delete_daily_score,
        "cm": repo.delete_metric_value,
        "mk": repo.delete_marker,
    }
    fn = mapping.get(kind)
    if fn is None:
        return "Этот тип записи нельзя отменить."
    if not await fn(item_id, tid):
        return "Запись не найдена."
    return None

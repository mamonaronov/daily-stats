"""Date-bound good decisions: close schedule days in order, never past today."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from database.models import CustomMetric, User
from database.queries import Repo
from services.entries import require_write
from services.spam_watch import note_write
from utils.time import (
    combine_local,
    format_date,
    format_date_long,
    local_date_of,
    parse_iso,
    to_iso,
    user_now,
    user_today,
)

ALL_WEEKDAYS = 127
_DAY_NAMES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_AHEAD_HORIZON = 400


@dataclass(frozen=True, slots=True)
class PledgeProgress:
    today: date
    start: date
    end: date | None
    weekdays: int
    due: tuple[date, ...]
    open_dates: tuple[date, ...]
    closed_slots: tuple[date, ...]
    next_open: date | None
    last_closed: date | None
    next_future: date | None
    done_due: int
    due_count: int
    done_total: int
    total_count: int | None

    @property
    def caught_up(self) -> bool:
        return not self.open_dates


def weekday_bit(index: int) -> int:
    return 1 << index


def format_weekdays(mask: int) -> str:
    bits = mask & ALL_WEEKDAYS
    if bits == ALL_WEEKDAYS:
        return "каждый день"
    names = [_DAY_NAMES[i] for i in range(7) if bits & weekday_bit(i)]
    if not names:
        return "дни не выбраны"
    return " ".join(names)


def scheduled_dates(start: date, end: date | None, weekdays: int, *, until: date) -> list[date]:
    bits = weekdays & ALL_WEEKDAYS
    if bits == 0:
        return []
    last = until if end is None else min(end, until)
    if last < start:
        return []
    days: list[date] = []
    current = start
    while current <= last:
        if bits & weekday_bit(current.weekday()):
            days.append(current)
        current += timedelta(days=1)
    return days


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def marks_by_day(values, tz: str) -> dict[date, list]:
    grouped: dict[date, list] = defaultdict(list)
    for rec in values:
        if not rec.value_bool:
            continue
        grouped[local_date_of(parse_iso(rec.occurred_at), tz)].append(rec)
    return grouped


def progress_for(metric: CustomMetric, values, today: date, tz: str) -> PledgeProgress:
    start = _parse_day(metric.starts_on) or today
    end = _parse_day(metric.ends_on)
    weekdays = int(metric.weekdays or ALL_WEEKDAYS) & ALL_WEEKDAYS
    closed = set(marks_by_day(values, tz))
    cap = end if end is not None else today
    if closed:
        cap = max(cap, max(closed))
    if end is None:
        cap = max(cap, today + timedelta(days=_AHEAD_HORIZON))
    all_days = scheduled_dates(start, end, weekdays, until=cap)
    schedule = set(all_days)
    closed_slots = tuple(day for day in all_days if day in closed and day in schedule)
    due = tuple(day for day in all_days if day <= today)
    due_set = set(due)
    open_dates = tuple(day for day in due if day not in closed)
    upcoming = [day for day in all_days if day > today and day not in closed]
    full = scheduled_dates(start, end, weekdays, until=end) if end is not None else None
    done_total = len([day for day in (full or ()) if day in closed]) if full is not None else len(closed_slots)
    return PledgeProgress(
        today=today,
        start=start,
        end=end,
        weekdays=weekdays,
        due=due,
        open_dates=open_dates,
        closed_slots=closed_slots,
        next_open=open_dates[0] if open_dates else None,
        last_closed=closed_slots[-1] if closed_slots else None,
        next_future=upcoming[0] if upcoming else None,
        done_due=sum(1 for day in due if day in closed and day in due_set),
        due_count=len(due),
        done_total=done_total,
        total_count=None if full is None else len(full),
    )


def schedule_overlaps(metric: CustomMetric, start: date, end: date) -> bool:
    if getattr(metric, "data_type", None) != "pledge" or not metric.starts_on:
        return False
    metric_start = _parse_day(metric.starts_on)
    if metric_start is None or metric_start > end:
        return False
    metric_end = _parse_day(metric.ends_on)
    if metric_end is not None and metric_end < start:
        return False
    window_start = max(metric_start, start)
    window_end = end if metric_end is None else min(metric_end, end)
    if window_end < window_start:
        return False
    return bool(
        scheduled_dates(window_start, metric_end, int(metric.weekdays or ALL_WEEKDAYS), until=window_end)
    )


def format_day_list(days: list[date] | tuple[date, ...]) -> str:
    if not days:
        return ""
    if len(days) == 1:
        return format_date(days[0])
    if len(days) > 6:
        return f"{format_date(days[0])} — {format_date(days[-1])}, {len(days)} дат"
    return ", ".join(format_date(day) for day in days)


def schedule_phrase(progress: PledgeProgress) -> str:
    days = format_weekdays(progress.weekdays)
    if days == "каждый день":
        days = "Каждый день"
    start = format_date_long(progress.start)
    if progress.end is None:
        return f"{days} · с {start}"
    return f"{days} · {start} — {format_date_long(progress.end)}"


def caught_up_text(progress: PledgeProgress) -> str:
    if progress.start > progress.today:
        return f"Начнётся {format_date(progress.start)}."
    if progress.next_future is not None:
        return "До сегодня всё закрыто."
    if progress.total_count == 0:
        return "В эти дни на выбранном сроке нет дат."
    return "График закрыт."


def pledge_card_text(metric: CustomMetric, progress: PledgeProgress) -> str:
    status = "включено" if metric.enabled else "выключено"
    lines = [
        f"📆 <b>{metric.name}</b>",
        "",
        schedule_phrase(progress),
        f"Статус: {status}",
        "",
    ]
    if progress.due_count:
        lines.append(f"К сегодня сделано {progress.done_due} из {progress.due_count}.")
    if progress.total_count is not None:
        lines.append(f"До конца срока — {progress.done_total} из {progress.total_count}.")
    if progress.open_dates:
        lines.append(f"Ещё не отмечено: {format_day_list(progress.open_dates)}.")
        lines.append("")
        lines.append(f"«Отметить {format_date(progress.open_dates[0])}» засчитывает этот день.")
        if len(progress.open_dates) > 1:
            lines.append("«Отметить все до сегодня» закрывает такие дни разом.")
        if progress.last_closed is not None:
            lines.append(f"«Убрать отметку» снимает {format_date(progress.last_closed)}.")
    else:
        lines.append("")
        lines.append(caught_up_text(progress))
        if progress.next_future is not None and progress.start <= progress.today:
            lines.append(f"Следующий день — {format_date(progress.next_future)}, его ещё рано отмечать.")
    return "\n".join(lines)


def pledge_today_line(name: str, progress: PledgeProgress) -> str:
    if progress.open_dates:
        return f"📆 {name} — открыто {format_day_list(progress.open_dates)}"
    if progress.start > progress.today:
        return f"📆 {name} — с {format_date(progress.start)}"
    if progress.next_future is not None:
        return f"📆 {name} — до сегодня закрыто"
    return f"📆 {name} — график закрыт"


def pledge_period_text(metric: CustomMetric, progress: PledgeProgress, start: date, end: date) -> str:
    metric_end = progress.end
    window_start = max(progress.start, start)
    window_end = end if metric_end is None else min(metric_end, end)
    in_period = []
    if window_end >= window_start:
        in_period = scheduled_dates(window_start, metric_end, progress.weekdays, until=window_end)
    closed = set(progress.closed_slots)
    closed_in = [day for day in in_period if day in closed]
    lines = [f"📆 <b>{metric.name}</b>", schedule_phrase(progress)]
    if in_period:
        lines.append(f"В периоде закрыто {len(closed_in)} из {len(in_period)}")
    else:
        lines.append("В этом периоде дней графика нет.")
    if progress.open_dates:
        lines.append(f"К сегодня открыто: {format_day_list(progress.open_dates)}")
    else:
        lines.append(caught_up_text(progress))
    return "\n".join(lines)


def closed_notice(days: list[date]) -> str:
    if len(days) == 1:
        return f"Закрыто {format_date(days[0])}"
    if len(days) <= 3:
        return "Закрыто " + ", ".join(format_date(day) for day in days)
    return f"Закрыто {len(days)} дат, с {format_date(days[0])} по {format_date(days[-1])}"


def _reject_days(progress: PledgeProgress, days: list[date]) -> str | None:
    if not days:
        return caught_up_text(progress)
    if any(day > progress.today for day in days):
        return "Эту дату ещё нельзя закрыть."
    if list(progress.open_dates[: len(days)]) != days:
        return "Сначала закройте более раннюю дату."
    return None


async def load_progress(repo: Repo, user: User, metric: CustomMetric, today: date | None = None) -> PledgeProgress:
    values = [rec for rec in await repo.list_pledge_values(user.telegram_id) if rec.metric_id == metric.id]
    return progress_for(metric, values, today or user_today(user.timezone), user.timezone)


async def pledge_edge_dates(
    repo: Repo, user: User, metrics
) -> tuple[dict[int, date], dict[int, date]]:
    """Next open day and latest closed day for each scheduled pledge."""
    pledges = [
        metric
        for metric in metrics
        if getattr(metric, "data_type", None) == "pledge" and getattr(metric, "starts_on", None)
    ]
    if not pledges:
        return {}, {}
    grouped: dict[int, list] = defaultdict(list)
    for rec in await repo.list_pledge_values(user.telegram_id):
        grouped[rec.metric_id].append(rec)
    today = user_today(user.timezone)
    nxt: dict[int, date] = {}
    undo: dict[int, date] = {}
    for metric in pledges:
        progress = progress_for(metric, grouped.get(metric.id, []), today, user.timezone)
        if progress.next_open is not None:
            nxt[metric.id] = progress.next_open
        if progress.last_closed is not None:
            undo[metric.id] = progress.last_closed
    return nxt, undo


async def next_open_dates(repo: Repo, user: User, metrics) -> dict[int, date]:
    nxt, _undo = await pledge_edge_dates(repo, user, metrics)
    return nxt


async def pledge_today_lines(repo: Repo, user: User) -> tuple[str, ...]:
    metrics = [
        metric
        for metric in await repo.list_metrics(user.telegram_id, enabled_only=True)
        if metric.data_type == "pledge" and metric.starts_on
    ]
    if not metrics:
        return ()
    grouped: dict[int, list] = defaultdict(list)
    for rec in await repo.list_pledge_values(user.telegram_id):
        grouped[rec.metric_id].append(rec)
    today = user_today(user.timezone)
    lines: list[str] = []
    for metric in metrics:
        progress = progress_for(metric, grouped.get(metric.id, []), today, user.timezone)
        lines.append(pledge_today_line(metric.name, progress))
    return tuple(lines)


async def close_pledge(
    repo: Repo,
    user: User,
    metric: CustomMetric,
    days: list[date],
    *,
    today: date | None = None,
) -> tuple[list[date], str | None]:
    blocked = await require_write(user)
    if blocked:
        return [], blocked
    progress = await load_progress(repo, user, metric, today)
    error = _reject_days(progress, days)
    if error:
        return [], error
    for day in days:
        when = combine_local(user.timezone, day, 12, 0)
        await repo.add_metric_value(user.telegram_id, metric.id, to_iso(when), value_bool=1)
    if days:
        note_write(user, "хорошее решение", user_now(user.timezone))
    return days, None


async def close_next_pledge(
    repo: Repo, user: User, metric: CustomMetric, *, today: date | None = None
) -> tuple[list[date], str | None]:
    progress = await load_progress(repo, user, metric, today)
    if progress.next_open is None:
        blocked = await require_write(user)
        return [], blocked or caught_up_text(progress)
    return await close_pledge(repo, user, metric, [progress.next_open], today=progress.today)


async def close_open_pledge(
    repo: Repo, user: User, metric: CustomMetric, *, today: date | None = None
) -> tuple[list[date], str | None]:
    progress = await load_progress(repo, user, metric, today)
    if not progress.open_dates:
        blocked = await require_write(user)
        return [], blocked or caught_up_text(progress)
    return await close_pledge(repo, user, metric, list(progress.open_dates), today=progress.today)


async def undo_last_pledge(
    repo: Repo, user: User, metric: CustomMetric, *, today: date | None = None
) -> tuple[date | None, str | None]:
    blocked = await require_write(user)
    if blocked:
        return None, blocked
    values = [rec for rec in await repo.list_pledge_values(user.telegram_id) if rec.metric_id == metric.id]
    progress = progress_for(metric, values, today or user_today(user.timezone), user.timezone)
    if progress.last_closed is None:
        return None, "Пока нечего снимать."
    grouped = marks_by_day(values, user.timezone)
    removed = False
    for rec in grouped.get(progress.last_closed, []):
        removed = await repo.delete_metric_value(rec.id, user.telegram_id) or removed
    if not removed:
        return None, "Пока нечего снимать."
    note_write(user, "хорошее решение (снятие)", user_now(user.timezone))
    return progress.last_closed, None

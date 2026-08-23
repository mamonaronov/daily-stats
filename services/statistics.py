"""Diary statistics. Correlation is statistical, not causal."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import mean

from database.models import User
from database.queries import Repo
from utils.formatting import (
    ACTIVITY_TYPES,
    ALCOHOL_TYPES,
    CAFFEINE_TYPES,
    duration_human,
    score_text,
)
from utils.quantity import format_volume_ml, milliliters_of
from utils.time import (
    circular_mean_minutes,
    daterange,
    day_bounds_utc,
    format_date,
    minutes_of_day,
    minutes_to_hhmm,
    parse_iso,
    range_bounds_utc,
    to_iso,
    to_user,
)

METRIC_KEYS = [
    "cigarettes",
    "fooling",
    "snus",
    "sleep",
    "mood",
    "wellbeing",
    "caffeine",
    "alcohol",
    "activity",
]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mean_x, mean_y = mean(xs), mean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _intervals(minutes: list[int]) -> list[int]:
    if len(minutes) < 2:
        return []
    ordered = sorted(minutes)
    return [b - a for a, b in zip(ordered, ordered[1:])]


async def load_period(repo: Repo, user: User, start: date, end: date) -> dict:
    start_utc, end_utc = range_bounds_utc(user.timezone, start, end)
    a, b = to_iso(start_utc), to_iso(end_utc)
    tid = user.telegram_id
    return {
        "start": start,
        "end": end,
        "cigarettes": await repo.list_cigarettes(tid, a, b),
        "fooling": await repo.list_fooling(tid, a, b),
        "snus": await repo.list_snus_packs(tid, a, b),
        "sleep": await repo.list_sleep(tid, a, b),
        "mood": await repo.list_mood(tid, a, b),
        "wellbeing": await repo.list_wellbeing(tid, a, b),
        "caffeine": await repo.list_caffeine(tid, a, b),
        "alcohol": await repo.list_alcohol(tid, a, b),
        "activity": await repo.list_activity(tid, a, b),
        "notes": await repo.list_notes(tid, a, b),
        "custom": await repo.list_metric_values(tid, a, b),
    }


def daily_event_counts(user: User, items, start: date, end: date) -> dict[date, int]:
    counts = {day: 0 for day in daterange(start, end)}
    for item in items:
        local = to_user(parse_iso(item.occurred_at), user.timezone).date()
        if local in counts:
            counts[local] += 1
    return counts


def timestamp_count_stats(
    title: str,
    empty: str,
    user: User,
    items,
    start: date,
    end: date,
    *,
    first_label: str = "Среднее время первой",
    last_label: str = "Среднее время последней",
) -> str:
    if not items:
        return empty
    by_day = daily_event_counts(user, items, start, end)
    values = list(by_day.values())
    times = [to_user(parse_iso(i.occurred_at), user.timezone) for i in items]
    firsts: list[int] = []
    lasts: list[int] = []
    all_intervals: list[int] = []
    hour_hist = Counter()
    grouped: dict[date, list[datetime]] = defaultdict(list)
    for local in times:
        grouped[local.date()].append(local)
        hour_hist[local.hour] += 1
    for day_times in grouped.values():
        ordered = sorted(day_times)
        firsts.append(ordered[0].hour * 60 + ordered[0].minute)
        lasts.append(ordered[-1].hour * 60 + ordered[-1].minute)
        mins = [t.hour * 60 + t.minute for t in ordered]
        all_intervals.extend(_intervals(mins))

    peak_hour = hour_hist.most_common(1)[0][0] if hour_hist else None
    lines = [
        title,
        f"Всего: {len(items)}",
        f"В среднем в день: {mean(values):.1f}",
        f"Минимум за день: {min(values)}",
        f"Максимум за день: {max(values)}",
    ]
    avg_first = circular_mean_minutes(firsts)
    avg_last = circular_mean_minutes(lasts)
    if avg_first is not None:
        lines.append(f"{first_label}: {minutes_to_hhmm(avg_first)}")
    if avg_last is not None:
        lines.append(f"{last_label}: {minutes_to_hhmm(avg_last)}")
    if all_intervals:
        lines.append(f"Средний интервал: {duration_human(int(mean(all_intervals)))}")
        lines.append(f"Самый короткий интервал: {duration_human(min(all_intervals))}")
        lines.append(f"Самый длинный интервал: {duration_human(max(all_intervals))}")
    if peak_hour is not None:
        lines.append(f"Чаще всего в {peak_hour:02d}:00–{peak_hour:02d}:59")
    top_days = sorted(by_day.items(), key=lambda kv: kv[1], reverse=True)[:3]
    lines.append("По дням: " + ", ".join(f"{format_date(d)} — {c}" for d, c in top_days if c))
    return "\n".join(lines)


def cigarette_stats(user: User, items, start: date, end: date) -> str:
    return timestamp_count_stats(
        "🚬 <b>Сигареты</b>",
        "🚬 Сигареты: нет данных за период.",
        user,
        items,
        start,
        end,
    )


def fooling_stats(user: User, items, start: date, end: date) -> str:
    return timestamp_count_stats(
        "🤌 <b>Валять дурака</b>",
        "🤌 Валять дурака: нет данных за период.",
        user,
        items,
        start,
        end,
        first_label="Среднее время первого",
        last_label="Среднее время последнего",
    )


def snus_stats(user: User, items, start: date, end: date) -> str:
    completed = [i for i in items if i.duration_minutes is not None and i.finished_at]
    open_count = sum(1 for i in items if i.bought_at and not i.finished_at)
    if not completed and not open_count:
        return "🟢 Снюс: нет данных за период."
    lines = ["🟢 <b>Снюс</b>"]
    if completed:
        durs = [i.duration_minutes for i in completed if i.duration_minutes is not None]
        lines.extend(
            [
                f"Законченных шайб: {len(completed)}",
                f"В среднем хватает: {duration_human(int(mean(durs)))}",
                f"Минимум: {duration_human(min(durs))}",
                f"Максимум: {duration_human(max(durs))}",
            ]
        )
        days = max(1, (end - start).days + 1)
        per_week = len(completed) * 7 / days
        if days >= 7:
            lines.append(f"Шайб в неделю: {per_week:.1f}")
        finished_days = [
            to_user(parse_iso(i.finished_at), user.timezone).date()
            for i in completed
            if i.finished_at
        ]
        if finished_days:
            last = max(finished_days)
            lines.append(f"Последняя закончилась: {format_date(last)}")
    else:
        lines.append("Законченных шайб за период нет.")
    if open_count:
        lines.append(f"Открытых: {open_count}")
    return "\n".join(lines)


def sleep_stats(user: User, items, start: date, end: date) -> str:
    completed = [i for i in items if i.duration_minutes is not None]
    if not completed:
        return "😴 Сон: нет завершённых записей за период."
    durs = [i.duration_minutes for i in completed if i.duration_minutes is not None]
    beds = [minutes_of_day(parse_iso(i.bedtime), user.timezone) for i in completed if i.bedtime]
    wakes = [minutes_of_day(parse_iso(i.wake_time), user.timezone) for i in completed if i.wake_time]
    qualities = [i.quality for i in completed if i.quality]
    lines = [
        "😴 <b>Сон</b>",
        f"Ночей: {len(completed)}",
        f"Средняя длительность: {duration_human(int(mean(durs)))}",
        f"Минимум: {duration_human(min(durs))}",
        f"Максимум: {duration_human(max(durs))}",
    ]
    avg_bed = circular_mean_minutes(beds)
    avg_wake = circular_mean_minutes(wakes)
    if avg_bed is not None:
        lines.append(f"Среднее отход ко сну: {minutes_to_hhmm(avg_bed)}")
    if avg_wake is not None:
        lines.append(f"Среднее пробуждение: {minutes_to_hhmm(avg_wake)}")
    if qualities:
        lines.append(f"Среднее качество: {score_text(int(round(mean(qualities))))}")
        dist = Counter(qualities)
        lines.append(
            "Качество: "
            + ", ".join(f"{score_text(k)} — {dist[k]}" for k in sorted(dist))
        )
    return "\n".join(lines)


def score_stats(title: str, user: User, items, attr: str = "score") -> str:
    if not items:
        return f"{title}: нет данных за период."
    scores = [getattr(i, attr) for i in items]
    by_day: dict[date, list[int]] = defaultdict(list)
    for item in items:
        local = to_user(parse_iso(item.occurred_at), user.timezone).date()
        by_day[local].append(getattr(item, attr))
    day_avg = {d: mean(v) for d, v in by_day.items()}
    best = max(day_avg.items(), key=lambda kv: kv[1]) if day_avg else None
    worst = min(day_avg.items(), key=lambda kv: kv[1]) if day_avg else None
    dist = Counter(scores)
    lines = [
        title,
        f"Оценок: {len(scores)}",
        f"Среднее: {mean(scores):.2f} ({score_text(int(round(mean(scores))))})",
        f"Минимум: {score_text(min(scores))}",
        f"Максимум: {score_text(max(scores))}",
        "Распределение: " + ", ".join(f"{score_text(k)} — {dist[k]}" for k in sorted(dist)),
    ]
    if best:
        lines.append(f"Лучший день: {format_date(best[0])} ({best[1]:.1f})")
    if worst:
        lines.append(f"Худший день: {format_date(worst[0])} ({worst[1]:.1f})")
    return "\n".join(lines)


def _item_milliliters(item) -> float | None:
    return milliliters_of(getattr(item, "amount", None), getattr(item, "unit", None))


def daily_volume_ml(user: User, items, start: date, end: date) -> dict[date, float]:
    series = {day: 0.0 for day in daterange(start, end)}
    for item in items:
        local = to_user(parse_iso(item.occurred_at), user.timezone).date()
        milliliters = _item_milliliters(item)
        if local in series and milliliters:
            series[local] += milliliters
    return series


def drink_stats(
    title: str,
    user: User,
    items,
    type_attr: str,
    labels: dict[str, str],
    start: date,
    end: date,
) -> str:
    base = event_count_stats(title, user, items, type_attr, labels, start, end)
    if not items:
        return base
    total_ml = sum(_item_milliliters(item) or 0.0 for item in items)
    if not total_ml:
        return base
    by_day = daily_volume_ml(user, items, start, end)
    days = max(1, (end - start).days + 1)
    type_ml: dict[str, float] = defaultdict(float)
    type_count = Counter()
    for item in items:
        kind = getattr(item, type_attr)
        type_count[kind] += 1
        milliliters = _item_milliliters(item)
        if milliliters:
            type_ml[kind] += milliliters
    extra = [
        f"Объём: {format_volume_ml(total_ml)}",
        f"Средний объём в день: {format_volume_ml(total_ml / days)}",
        f"Максимум за день: {format_volume_ml(max(by_day.values()))}",
    ]
    if type_ml:
        extra.append(
            "По объёму: "
            + ", ".join(
                f"{labels.get(k, k)} — {format_volume_ml(v)} ({type_count[k]})"
                for k, v in sorted(type_ml.items(), key=lambda kv: kv[1], reverse=True)
            )
        )
    return base + "\n" + "\n".join(extra)


def event_count_stats(title: str, user: User, items, type_attr: str | None, labels: dict[str, str] | None, start: date, end: date) -> str:
    if not items:
        return f"{title}: нет данных за период."
    by_day = {day: 0 for day in daterange(start, end)}
    hour_hist = Counter()
    types = Counter()
    for item in items:
        local = to_user(parse_iso(item.occurred_at), user.timezone)
        if local.date() in by_day:
            by_day[local.date()] += 1
        hour_hist[local.hour] += 1
        if type_attr:
            types[getattr(item, type_attr)] += 1
    values = list(by_day.values())
    lines = [
        title,
        f"Всего: {len(items)}",
        f"В среднем в день: {mean(values):.1f}",
    ]
    if hour_hist:
        peak = hour_hist.most_common(1)[0][0]
        lines.append(f"Чаще всего в {peak:02d}:00–{peak:02d}:59")
    if types and labels:
        lines.append(
            "По типам: "
            + ", ".join(f"{labels.get(k, k)} — {v}" for k, v in types.most_common())
        )
    return "\n".join(lines)


def activity_stats(user: User, items, start: date, end: date) -> str:
    base = event_count_stats("🏃 <b>Активность</b>", user, items, "activity_type", ACTIVITY_TYPES, start, end)
    durs = [i.duration_minutes for i in items if i.duration_minutes]
    if not durs:
        return base
    extra = [
        f"Суммарно: {duration_human(sum(durs))}",
        f"Средняя длительность: {duration_human(int(mean(durs)))}",
    ]
    return base + "\n" + "\n".join(extra)


def daily_series(user: User, items, start: date, end: date, value_fn) -> dict[date, float]:
    series = {day: 0.0 for day in daterange(start, end)}
    buckets: dict[date, list] = defaultdict(list)
    for item in items:
        local = to_user(parse_iso(item.occurred_at), user.timezone).date()
        if start <= local <= end:
            buckets[local].append(item)
    for day in series:
        series[day] = value_fn(buckets.get(day, []))
    return series


def compare_metrics(user: User, data: dict, left: str, right: str) -> str | None:
    start, end = data["start"], data["end"]
    builders = {
        "cigarettes": lambda items: daily_series(user, items, start, end, lambda xs: float(len(xs))),
        "fooling": lambda items: daily_series(user, items, start, end, lambda xs: float(len(xs))),
        "snus": lambda items: _snus_series(user, items, start, end),
        "mood": lambda items: daily_series(user, items, start, end, lambda xs: mean([i.score for i in xs]) if xs else 0.0),
        "wellbeing": lambda items: daily_series(user, items, start, end, lambda xs: mean([i.score for i in xs]) if xs else 0.0),
        "caffeine": lambda items: daily_volume_ml(user, items, start, end),
        "alcohol": lambda items: daily_volume_ml(user, items, start, end),
        "sleep": lambda items: _sleep_series(user, items, start, end),
        "activity": lambda items: daily_series(user, items, start, end, lambda xs: float(sum(i.duration_minutes or 0 for i in xs))),
    }
    if left not in builders or right not in builders:
        return None
    a = builders[left](data[left])
    b = builders[right](data[right])
    days = [d for d in a if a[d] or b[d]]
    if len(days) < 3:
        return None
    corr = pearson([a[d] for d in days], [b[d] for d in days])
    if corr is None:
        return None
    names = {
        "cigarettes": "сигареты",
        "fooling": "валять дурака",
        "snus": "снюс",
        "sleep": "сон",
        "mood": "настроение",
        "wellbeing": "самочувствие",
        "caffeine": "кофеин",
        "alcohol": "алкоголь",
        "activity": "активность",
    }
    return (
        f"Связь {names[left]} ↔ {names[right]}: корреляция Пирсона {corr:.2f}.\n"
        "Это статистическая связь, а не доказанная причина."
    )


def _sleep_series(user: User, items, start: date, end: date) -> dict[date, float]:
    series = {day: 0.0 for day in daterange(start, end)}
    for item in items:
        if item.wake_time and item.duration_minutes:
            day = to_user(parse_iso(item.wake_time), user.timezone).date()
            if day in series:
                series[day] = float(item.duration_minutes)
    return series


def _snus_series(user: User, items, start: date, end: date) -> dict[date, float]:
    series = {day: 0.0 for day in daterange(start, end)}
    for item in items:
        if item.finished_at and item.duration_minutes:
            day = to_user(parse_iso(item.finished_at), user.timezone).date()
            if day in series:
                series[day] = float(item.duration_minutes)
    return series


PAIRS = [
    ("sleep", "cigarettes"),
    ("sleep", "fooling"),
    ("sleep", "mood"),
    ("sleep", "wellbeing"),
    ("cigarettes", "mood"),
    ("fooling", "mood"),
    ("fooling", "cigarettes"),
    ("snus", "cigarettes"),
    ("snus", "mood"),
    ("caffeine", "sleep"),
    ("alcohol", "sleep"),
]


async def render_stats(repo: Repo, user: User, start: date, end: date, selected: list[str]) -> str:
    data = await load_period(repo, user, start, end)
    parts = [f"📊 <b>Статистика</b>\n{format_date(start)} — {format_date(end)}"]
    if "cigarettes" in selected:
        parts.append(cigarette_stats(user, data["cigarettes"], start, end))
    if "fooling" in selected:
        parts.append(fooling_stats(user, data["fooling"], start, end))
    if "snus" in selected:
        parts.append(snus_stats(user, data["snus"], start, end))
    if "sleep" in selected:
        parts.append(sleep_stats(user, data["sleep"], start, end))
    if "mood" in selected:
        parts.append(score_stats("🙂 <b>Настроение</b>", user, data["mood"]))
    if "wellbeing" in selected:
        parts.append(score_stats("❤️ <b>Самочувствие</b>", user, data["wellbeing"]))
    if "caffeine" in selected:
        parts.append(
            drink_stats("☕ <b>Кофеин</b>", user, data["caffeine"], "drink_type", CAFFEINE_TYPES, start, end)
        )
    if "alcohol" in selected:
        parts.append(
            drink_stats("🍺 <b>Алкоголь</b>", user, data["alcohol"], "drink_type", ALCOHOL_TYPES, start, end)
        )
    if "activity" in selected:
        parts.append(activity_stats(user, data["activity"], start, end))
    selected_set = set(selected)
    comparisons = []
    for left, right in PAIRS:
        if left in selected_set and right in selected_set:
            text = compare_metrics(user, data, left, right)
            if text:
                comparisons.append(text)
    if comparisons:
        parts.append("🔗 <b>Сравнения</b>\n" + "\n\n".join(comparisons))
    return "\n\n".join(parts)

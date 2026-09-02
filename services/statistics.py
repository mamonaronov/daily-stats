"""Diary statistics. Correlation is statistical, not causal."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import mean

from database.models import User
from database.queries import Repo
from services.daily_scores import DAILY_SCORE_KEYS, spec_of
from utils.formatting import (
    ACTIVITY_TYPES,
    ALCOHOL_TYPES,
    CAFFEINE_TYPES,
    duration_human,
    format_int_spaces,
    format_kg,
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
    "caffeine",
    "alcohol",
    "activity",
    "steps",
    "weight",
    *DAILY_SCORE_KEYS,
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
    scores = await repo.list_daily_scores(tid, a, b)
    data = {
        "start": start,
        "end": end,
        "cigarettes": await repo.list_cigarettes(tid, a, b),
        "fooling": await repo.list_fooling(tid, a, b),
        "snus": await repo.list_snus_packs(tid, a, b),
        "sleep": await repo.list_sleep(tid, a, b),
        "caffeine": await repo.list_caffeine(tid, a, b),
        "alcohol": await repo.list_alcohol(tid, a, b),
        "activity": await repo.list_activity(tid, a, b),
        "steps": await repo.list_steps(tid, a, b),
        "weight": await repo.list_weight(tid, a, b),
        "daily_scores": scores,
        "custom": await repo.list_metric_values(tid, a, b),
        "markers": await repo.list_markers(tid, a, b),
        "periods": await repo.list_periods_overlapping(tid, a, b),
    }
    for key in DAILY_SCORE_KEYS:
        data[key] = [row for row in scores if row.kind == key]
    return data


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

    def _tod(value: str | None) -> int | None:
        if not value:
            return None
        return minutes_of_day(parse_iso(value), user.timezone)

    phones_in = [m for i in items if (m := _tod(i.phone_in_bed_at)) is not None]
    phones_away = [m for i in items if (m := _tod(i.phone_away_at or i.bedtime)) is not None]
    onsets = [m for i in items if (m := _tod(i.sleep_onset_at)) is not None]
    wakes = [m for i in items if (m := _tod(i.wake_time)) is not None]
    ups = [m for i in items if (m := _tod(i.out_of_bed_at)) is not None]
    qualities = [i.quality for i in completed if i.quality]
    lines = [
        "😴 <b>Сон</b>",
        f"Ночей: {len(completed)}",
        f"Средняя длительность: {duration_human(int(mean(durs)))}",
        f"Минимум: {duration_human(min(durs))}",
        f"Максимум: {duration_human(max(durs))}",
    ]
    avg_phone_in = circular_mean_minutes(phones_in)
    avg_away = circular_mean_minutes(phones_away)
    avg_onset = circular_mean_minutes(onsets)
    avg_wake = circular_mean_minutes(wakes)
    avg_up = circular_mean_minutes(ups)
    if avg_phone_in is not None:
        lines.append(f"Среднее лёг с телефоном: {minutes_to_hhmm(avg_phone_in)}")
    if avg_away is not None:
        lines.append(f"Среднее без телефона: {minutes_to_hhmm(avg_away)}")
    if avg_onset is not None:
        lines.append(f"Среднее засыпание: {minutes_to_hhmm(avg_onset)}")
    if avg_wake is not None:
        lines.append(f"Среднее пробуждение: {minutes_to_hhmm(avg_wake)}")
    if avg_up is not None:
        lines.append(f"Среднее подъём: {minutes_to_hhmm(avg_up)}")
    if qualities:
        lines.append(f"Среднее качество: {score_text(int(round(mean(qualities))))}")
        dist = Counter(qualities)
        lines.append(
            "Качество: "
            + ", ".join(f"{score_text(k)} — {dist[k]}" for k in sorted(dist))
        )
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


def steps_stats(user: User, items, start: date, end: date) -> str:
    if not items:
        return "🚶 <b>Шаги</b>\nНет записей за период."
    values = [int(item.steps) for item in items]
    lines = [
        "🚶 <b>Шаги</b>",
        f"Дней с записью: {len(values)}",
        f"Сумма: {format_int_spaces(sum(values))}",
        f"В среднем за день с записью: {format_int_spaces(int(round(mean(values))))}",
        f"Минимум: {format_int_spaces(min(values))} · Максимум: {format_int_spaces(max(values))}",
    ]
    top = sorted(items, key=lambda rec: rec.steps, reverse=True)[:3]
    lines.append(
        "Больше всего: "
        + ", ".join(
            f"{format_date(to_user(parse_iso(rec.occurred_at), user.timezone).date())} — {format_int_spaces(rec.steps)}"
            for rec in top
        )
    )
    return "\n".join(lines)


def daily_score_stats(user: User, kind: str, items, start: date, end: date) -> str:
    spec = spec_of(kind)
    rows = [item for item in items if item.kind == kind]
    title = f"{spec.emoji} <b>{spec.label}</b>"
    if not rows:
        return f"{title}\nНет записей за период."
    values = [int(item.score) for item in rows]
    dist = Counter(values)
    lines = [
        title,
        f"Дней с записью: {len(values)}",
        f"Среднее: {mean(values):.1f} · {score_text(int(round(mean(values))))}",
        f"Минимум: {score_text(min(values))} · Максимум: {score_text(max(values))}",
        "Оценки: " + ", ".join(f"{score_text(k)} — {dist[k]}" for k in sorted(dist)),
    ]
    return "\n".join(lines)


def weight_stats(user: User, items, start: date, end: date) -> str:
    if not items:
        return "⚖️ <b>Вес</b>\nНет записей за период."
    values = [float(item.kilograms) for item in items]
    first, last = values[0], values[-1]
    delta = last - first
    sign = "+" if delta > 0 else ""
    lines = [
        "⚖️ <b>Вес</b>",
        f"Замеров: {len(values)}",
        f"Последний: {format_kg(last)}",
        f"Среднее: {format_kg(mean(values))}",
        f"Мин: {format_kg(min(values))} · Макс: {format_kg(max(values))}",
    ]
    if len(values) >= 2:
        lines.append(f"Изменение за период: {sign}{format_kg(delta)}")
    return "\n".join(lines)


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
        "caffeine": lambda items: daily_volume_ml(user, items, start, end),
        "alcohol": lambda items: daily_volume_ml(user, items, start, end),
        "sleep": lambda items: _sleep_series(user, items, start, end),
        "activity": lambda items: daily_series(user, items, start, end, lambda xs: float(sum(i.duration_minutes or 0 for i in xs))),
        "steps": lambda items: daily_series(user, items, start, end, lambda xs: float(xs[0].steps if xs else 0)),
        **{key: (lambda items: _daily_score_series(items, start, end)) for key in DAILY_SCORE_KEYS},
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
        "caffeine": "кофеин",
        "alcohol": "алкоголь",
        "activity": "активность",
        "steps": "шаги",
        **{key: spec_of(key).label.lower() for key in DAILY_SCORE_KEYS},
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


def _daily_score_series(items, start: date, end: date) -> dict[date, float]:
    series = {day: 0.0 for day in daterange(start, end)}
    for item in items:
        day = date.fromisoformat(item.day)
        if day in series:
            series[day] = float(item.score)
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
    ("fooling", "cigarettes"),
    ("snus", "cigarettes"),
    ("caffeine", "sleep"),
    ("alcohol", "sleep"),
    ("sleep", "steps"),
    ("sleep", "mood"),
    ("sleep", "energy"),
    ("sleep", "wellbeing"),
    ("mood", "productivity"),
    ("energy", "productivity"),
    ("mood", "day_rating"),
]


def marker_stats(user: User, markers, periods) -> str:
    if not markers and not periods:
        return ""
    from services.markers import period_title
    from utils.formatting import duration_human

    lines = ["🔖 <b>Метки</b>"]
    for marker in markers:
        stamp = format_date(to_user(parse_iso(marker.occurred_at), user.timezone).date())
        time_s = to_user(parse_iso(marker.occurred_at), user.timezone).strftime("%H:%M")
        role = ""
        if marker.period_role == "start":
            role = " · начало"
        elif marker.period_role == "end":
            role = " · конец"
        extra = f" — {marker.comment}" if marker.comment else ""
        lines.append(f"{stamp} {time_s} {marker.name}{role}{extra}")
    for period in periods:
        if not period.start_at:
            continue
        start_day = format_date(to_user(parse_iso(period.start_at), user.timezone).date())
        title = period_title(period)
        if period.is_open:
            lines.append(f"▶️ {title}: с {start_day}, ещё открыт")
            continue
        if not period.end_at:
            continue
        end_day = format_date(to_user(parse_iso(period.end_at), user.timezone).date())
        minutes = int((parse_iso(period.end_at) - parse_iso(period.start_at)).total_seconds() // 60)
        span = duration_human(minutes) if minutes >= 0 else ""
        tail = f" ({span})" if span else ""
        lines.append(f"▶️ {title}: {start_day} — {end_day}{tail}")
    return "\n".join(lines)


def _custom_metric_stats(items: list) -> str:
    if not items:
        return "📌 Нет данных по выбранной метрике."
    name = items[0].metric_name or "Метрика"
    if items[0].data_type == "period":
        durs = [int(item.value_number) for item in items if item.value_number is not None]
        open_n = sum(1 for item in items if item.value_bool == 1)
        lines = [f"📌 <b>{name}</b>", f"Записей: {len(items)}"]
        if open_n:
            lines.append(f"Ещё открыто: {open_n}")
        if durs:
            lines.append(f"Суммарно: {duration_human(sum(durs))}")
            lines.append(f"Средняя длительность: {duration_human(int(mean(durs)))}")
            lines.append(f"Мин: {duration_human(min(durs))} · Макс: {duration_human(max(durs))}")
        return "\n".join(lines)
    numbers = [item.value_number for item in items if item.value_number is not None]
    lines = [f"📌 <b>{name}</b>", f"Записей: {len(items)}"]
    if numbers:
        lines.append(f"Среднее: {mean(numbers):g}")
        lines.append(f"Мин: {min(numbers):g} · Макс: {max(numbers):g}")
    return "\n".join(lines)


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
    if "steps" in selected:
        parts.append(steps_stats(user, data["steps"], start, end))
    if "weight" in selected:
        parts.append(weight_stats(user, data["weight"], start, end))
    for key in DAILY_SCORE_KEYS:
        if key in selected:
            parts.append(daily_score_stats(user, key, data["daily_scores"], start, end))
    custom_ids = [int(key[1:]) for key in selected if key.startswith("m") and key[1:].isdigit()]
    if custom_ids:
        from collections import defaultdict as _dd

        by_id: dict[int, list] = _dd(list)
        for value in data["custom"]:
            if value.metric_id in custom_ids:
                by_id[value.metric_id].append(value)
        for metric_id in custom_ids:
            parts.append(_custom_metric_stats(by_id.get(metric_id, [])))
    marker_block = marker_stats(user, data["markers"], data["periods"])
    if marker_block:
        parts.append(marker_block)
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

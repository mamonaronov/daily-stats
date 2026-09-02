"""Button-click analytics: classify taps, persist them, summarize for admin and UX."""

from __future__ import annotations

import html
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aiogram.types import CallbackQuery

from config import Config
from database.queries import Repo
from utils.formatting import format_int_spaces
from utils.time import (
    day_bounds_utc,
    format_date,
    format_dt_full,
    now_utc,
    parse_iso,
    range_bounds_utc,
    to_iso,
    to_user,
)

SKIP_CALLBACKS = frozenset({"noop"})

KIND_LABELS: dict[str, str] = {
    "menu": "Меню",
    "back": "Назад",
    "cancel": "Отмена",
    "settings": "Настройки",
    "history": "История",
    "stats": "Статистика",
    "admin": "Админка",
    "custom": "Кастом",
    "markers": "Метки",
    "balance": "Баланс",
    "guide": "Гайд",
    "cigarettes": "Сигареты",
    "fooling": "Валять дурака",
    "snus": "Снюс",
    "sleep": "Сон",
    "caffeine": "Кофеин",
    "alcohol": "Алкоголь",
    "activity": "Активность",
    "steps": "Шаги",
    "weight": "Вес",
    "daily_scores": "Оценки дня",
    "timezone": "Часовой пояс",
    "legal": "Документы",
    "onboarding": "Онбординг",
    "export": "Выгрузка",
    "undo": "Отмена записи",
    "delete": "Удаление",
    "edit": "Правка",
    "skip": "Пропуск",
    "calendar": "Календарь",
    "time": "Время",
    "unknown": "Другое",
}

_EXACT_KIND: dict[str, str] = {
    "n:m": "menu",
    "n:s": "settings",
    "n:h": "history",
    "n:st": "stats",
    "n:a": "admin",
    "n:cm": "custom",
    "n:mk": "markers",
    "n:bal": "balance",
    "n:g": "guide",
    "n:c": "cancel",
    "n:b": "back",
    "e:cig": "cigarettes",
    "e:fool": "fooling",
    "e:sns": "snus",
    "e:slp": "sleep",
    "e:caf": "caffeine",
    "e:alc": "alcohol",
    "e:act": "activity",
    "e:stp": "steps",
    "e:wgt": "weight",
    "e:ds": "daily_scores",
    "stp:today": "steps",
    "stp:yesterday": "stats",
    "stp:7": "stats",
    "stp:14": "stats",
    "stp:30": "stats",
    "stp:all": "stats",
    "stp:range": "stats",
}

_PREFIX_KIND: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("adx:", "admin"),
            ("ads:", "admin"),
            ("advc:", "admin"),
            ("advl:", "admin"),
            ("adclkc:", "admin"),
            ("adclk:", "admin"),
            ("adv:", "admin"),
            ("ad:", "admin"),
            ("cig:", "cigarettes"),
            ("fool:", "fooling"),
            ("sns:", "snus"),
            ("slp:", "sleep"),
            ("slo:", "sleep"),
            ("slw:", "sleep"),
            ("slu:", "sleep"),
            ("slb:", "sleep"),
            ("sln:", "sleep"),
            ("caft:", "caffeine"),
            ("caf:", "caffeine"),
            ("alct:", "alcohol"),
            ("alc:", "alcohol"),
            ("actt:", "activity"),
            ("act:", "activity"),
            ("wgt:", "weight"),
            ("stp:", "steps"),
            ("dscalm:", "daily_scores"),
            ("dscal:", "daily_scores"),
            ("ds:", "daily_scores"),
            ("hist:", "history"),
            ("hdt:", "time"),
            ("hr:", "time"),
            ("mn:", "time"),
            ("h:", "history"),
            ("stm:", "stats"),
            ("stv:", "stats"),
            ("cm:", "custom"),
            ("mk:", "markers"),
            ("set:", "settings"),
            ("tz:", "timezone"),
            ("lg:", "legal"),
            ("onb:", "onboarding"),
            ("g:", "guide"),
            ("bal:", "balance"),
            ("unok:", "undo"),
            ("un:", "undo"),
            ("rmok:", "delete"),
            ("rm:", "delete"),
            ("ed:", "edit"),
            ("sv:", "edit"),
            ("wb:", "skip"),
            ("exp:", "export"),
            ("stpcalm:", "steps"),
            ("stpcal:", "steps"),
            ("calm:", "calendar"),
            ("cal:", "calendar"),
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

CLICK_PERIODS: dict[str, str] = {
    "today": "сегодня",
    "7": "7 дней",
    "30": "30 дней",
    "all": "всё время",
}


def classify_button(callback_data: str | None) -> str:
    data = (callback_data or "").strip()
    if not data:
        return "unknown"
    kind = _EXACT_KIND.get(data)
    if kind:
        return kind
    for prefix, mapped in _PREFIX_KIND:
        if data.startswith(prefix):
            return mapped
    return "unknown"


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind or KIND_LABELS["unknown"])


def callback_button_text(event: CallbackQuery) -> str | None:
    data = event.data
    message = event.message
    markup = getattr(message, "reply_markup", None) if message is not None else None
    if not data or markup is None:
        return None
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data == data:
                text = (btn.text or "").strip()
                return text[:80] or None
    return None


async def record_callback_click(repo: Repo, config: Config, event: CallbackQuery) -> None:
    data = event.data or ""
    if not data or data in SKIP_CALLBACKS:
        return
    clicks = repo.db.clicks_db
    if clicks is None:
        return
    user = event.from_user
    if user is None:
        return
    await clicks.record(
        telegram_id=user.id,
        clicked_at=to_iso(now_utc()),
        button_kind=classify_button(data),
        callback_data=data[:64],
        button_text=callback_button_text(event),
        is_owner=user.id == config.owner_id,
    )


def click_period_key(raw: str | None) -> str:
    key = (raw or "today").strip()
    return key if key in CLICK_PERIODS else "today"


def click_window(
    period_key: str,
    tz_name: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    key = click_period_key(period_key)
    title = CLICK_PERIODS[key]
    now = now or now_utc()
    today = to_user(now, tz_name).date()
    if key == "today":
        start, end = day_bounds_utc(tz_name, today)
        return start, end, title
    if key == "all":
        return datetime(2000, 1, 1, tzinfo=timezone.utc), now + timedelta(seconds=1), title
    days = int(key)
    start_day = today - timedelta(days=days - 1)
    start, end = range_bounds_utc(tz_name, start_day, today)
    return start, end, title


async def admin_click_summary_lines(
    repo: Repo,
    tz_name: str,
    now: datetime | None = None,
) -> list[str]:
    clicks = repo.db.clicks_db
    if clicks is None:
        return []
    now = now or now_utc()
    today = to_user(now, tz_name).date()
    day_start, _ = day_bounds_utc(tz_name, today)
    stats = await clicks.overview(to_iso(day_start))
    last = stats.get("last_user")
    if last and last.get("clicked_at"):
        when = format_dt_full(parse_iso(str(last["clicked_at"])), tz_name)
        telegram_id = int(last["telegram_id"])
        user = await repo.get_user(telegram_id)
        who = html.escape(user.display_name) if user else str(telegram_id)
        last_line = f"Последнее нажатие пользователя: {when} ({who})"
    else:
        last_line = "Последнее нажатие пользователя: —"
    return [
        f"Нажатий пользователей: {format_int_spaces(int(stats['users_total']))}",
        (
            f"Мои нажатия: сегодня {format_int_spaces(int(stats['owner_today']))}"
            f" · всего {format_int_spaces(int(stats['owner_total']))}"
        ),
        last_line,
    ]


def bucket_clicks_by_day(timestamps: list[str], tz_name: str) -> list[tuple[date, int]]:
    counts: Counter[date] = Counter()
    for stamp in timestamps:
        try:
            counts[to_user(parse_iso(stamp), tz_name).date()] += 1
        except ValueError:
            continue
    if not counts:
        return []
    start = min(counts)
    end = max(counts)
    days: list[tuple[date, int]] = []
    cursor = start
    while cursor <= end:
        days.append((cursor, counts[cursor]))
        cursor += timedelta(days=1)
    return days


def bucket_clicks_by_hour(timestamps: list[str], tz_name: str) -> list[int]:
    hours = [0] * 24
    for stamp in timestamps:
        try:
            hours[to_user(parse_iso(stamp), tz_name).hour] += 1
        except ValueError:
            continue
    return hours


def ux_kind_share(kind_rows: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Normalized kind mix for later UX work (what people actually tap)."""
    total = sum(count for _, count in kind_rows)
    rows = []
    for kind, count in kind_rows:
        share = (count / total) if total else 0.0
        rows.append(
            {
                "kind": kind,
                "label": kind_label(kind),
                "count": count,
                "share": share,
            }
        )
    return rows


async def render_click_report(
    repo: Repo,
    *,
    start: datetime,
    end: datetime,
    title: str,
    tz_name: str,
) -> str:
    clicks = repo.db.clicks_db
    if clicks is None:
        return "🖱 <b>Нажатия кнопок</b>\n\nБаза нажатий не подключена."
    start_iso, end_iso = to_iso(start), to_iso(end)
    summary = await clicks.period_user_summary(start_iso, end_iso)
    kinds = await clicks.kind_counts(start_iso, end_iso)
    top = await clicks.top_callbacks(start_iso, end_iso)
    lines = [
        "🖱 <b>Нажатия кнопок</b>",
        "",
        f"За {title} — только пользователи, без вас.",
        (
            f"Нажатий: {format_int_spaces(summary['taps'])}"
            f" · людей: {format_int_spaces(summary['people'])}"
        ),
    ]
    if kinds:
        lines.append("")
        lines.append("Чаще всего:")
        for kind, count in kinds[:10]:
            lines.append(f"• {html.escape(kind_label(kind))} — {format_int_spaces(count)}")
    if top:
        lines.append("")
        lines.append("Конкретные кнопки:")
        for row in top[:8]:
            label = (row.get("button_text") or "").strip() or kind_label(str(row.get("button_kind") or ""))
            callback = str(row.get("callback_data") or "")
            lines.append(
                f"• {html.escape(label)} (<code>{html.escape(callback)}</code>)"
                f" — {format_int_spaces(int(row['c']))}"
            )
    lines.append("")
    lines.append("Хранится отдельно от дневника и не попадает в бэкап.")
    return "\n".join(lines)


def day_axis_label(day: date) -> str:
    return format_date(day)

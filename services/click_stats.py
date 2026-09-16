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
    format_dt,
    format_dt_compact,
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
    "stp:since": "stats",
    "stp:marker": "stats",
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
            ("adclkj:", "admin"),
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
            ("stmkp:", "stats"),
            ("stmk:", "stats"),
            ("stm:", "stats"),
            ("stv:", "stats"),
            ("hmkp:", "history"),
            ("hmk:", "history"),
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

REPORT_RECENT_LIMIT = 12
REPORT_PEOPLE_LIMIT = 8
USER_CLICK_LIMIT = 15
JOURNAL_FILE_LIMIT = 3000


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
        who = await _click_who(repo, telegram_id)
        last_line = f"Последнее нажатие: {when} · {who} · {_click_button_html(last)}"
    else:
        last_line = "Последнее нажатие: —"
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


def click_button_label(row: dict[str, Any]) -> str:
    text = (row.get("button_text") or "").strip()
    return text or kind_label(str(row.get("button_kind") or ""))


def _click_button_html(row: dict[str, Any]) -> str:
    label = html.escape(click_button_label(row))
    callback = html.escape(str(row.get("callback_data") or ""))
    if callback:
        return f"{label} (<code>{callback}</code>)"
    return label


def _click_when(row: dict[str, Any], tz_name: str) -> str:
    raw = str(row.get("clicked_at") or "")
    try:
        return format_dt(parse_iso(raw), tz_name)
    except ValueError:
        return raw or "—"


async def _click_who(repo: Repo, telegram_id: int) -> str:
    user = await repo.get_user(telegram_id)
    return html.escape(user.display_name) if user else str(telegram_id)


async def _click_names(repo: Repo, telegram_ids: list[int]) -> dict[int, str]:
    names: dict[int, str] = {}
    for telegram_id in dict.fromkeys(telegram_ids):
        names[telegram_id] = await _click_who(repo, telegram_id)
    return names


def _plain_who(html_name: str, telegram_id: int) -> str:
    return html.unescape(html_name) if html_name != str(telegram_id) else str(telegram_id)


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
    people = await clicks.top_people(start_iso, end_iso, limit=REPORT_PEOPLE_LIMIT)
    recent = await clicks.recent_clicks(start_iso, end_iso, limit=REPORT_RECENT_LIMIT)
    names = await _click_names(
        repo,
        [int(row["telegram_id"]) for row in people] + [int(row["telegram_id"]) for row in recent],
    )
    lines = [
        "🖱 <b>Нажатия кнопок</b>",
        "",
        f"За {title} — только пользователи, без вас.",
        (
            f"Нажатий: {format_int_spaces(summary['taps'])}"
            f" · людей: {format_int_spaces(summary['people'])}"
        ),
    ]
    if people:
        lines.append("")
        lines.append("Кто нажимал:")
        for row in people:
            telegram_id = int(row["telegram_id"])
            lines.append(
                f"• {names.get(telegram_id, str(telegram_id))}"
                f" — {format_int_spaces(int(row['c']))}"
            )
    if recent:
        lines.append("")
        lines.append("Последние:")
        for row in recent:
            telegram_id = int(row["telegram_id"])
            lines.append(
                f"• {_click_when(row, tz_name)} · {names.get(telegram_id, str(telegram_id))}"
                f" · {_click_button_html(row)}"
            )
    if kinds:
        lines.append("")
        lines.append("Чаще всего:")
        for kind, count in kinds[:8]:
            lines.append(f"• {html.escape(kind_label(kind))} — {format_int_spaces(count)}")
    if top:
        lines.append("")
        lines.append("Конкретные кнопки:")
        for row in top[:6]:
            lines.append(
                f"• {_click_button_html(row)} — {format_int_spaces(int(row['c']))}"
            )
    lines.append("")
    lines.append("Хранится отдельно от дневника и не попадает в бэкап.")
    return "\n".join(lines)


async def render_user_click_log(
    repo: Repo,
    *,
    telegram_id: int,
    tz_name: str,
) -> str:
    clicks = repo.db.clicks_db
    user = await repo.get_user(telegram_id)
    who = html.escape(user.display_name) if user else str(telegram_id)
    if clicks is None:
        return f"🖱 <b>Нажатия {who}</b>\n\nБаза нажатий не подключена."
    start, end, _title = click_window("all", tz_name)
    start_iso, end_iso = to_iso(start), to_iso(end)
    summary = await clicks.recent_clicks(
        start_iso,
        end_iso,
        limit=USER_CLICK_LIMIT,
        telegram_id=telegram_id,
        include_owner=True,
    )
    total = await clicks.person_click_count(telegram_id, start_iso, end_iso)
    lines = [
        f"🖱 <b>Нажатия {who}</b>",
        "",
        f"Всего: {format_int_spaces(total)}",
    ]
    if not summary:
        lines.append("Пока нет нажатий.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Последние:")
    for item in summary:
        lines.append(f"• {_click_when(item, tz_name)} · {_click_button_html(item)}")
    return "\n".join(lines)


async def render_click_journal(
    repo: Repo,
    *,
    start: datetime,
    end: datetime,
    title: str,
    tz_name: str,
) -> str | None:
    clicks = repo.db.clicks_db
    if clicks is None:
        return None
    rows = await clicks.recent_clicks(
        to_iso(start),
        to_iso(end),
        limit=JOURNAL_FILE_LIMIT,
    )
    if not rows:
        return None
    names = await _click_names(repo, [int(row["telegram_id"]) for row in rows])
    lines = [
        f"Нажатия кнопок за {title}",
        "Только пользователи, без владельца.",
        f"Записей: {len(rows)}"
        + (" (последние)" if len(rows) >= JOURNAL_FILE_LIMIT else ""),
        "",
    ]
    for row in rows:
        telegram_id = int(row["telegram_id"])
        who = _plain_who(names.get(telegram_id, str(telegram_id)), telegram_id)
        raw_when = str(row.get("clicked_at") or "")
        try:
            when = format_dt_compact(parse_iso(raw_when), tz_name)
        except ValueError:
            when = raw_when or "—"
        label = click_button_label(row)
        callback = str(row.get("callback_data") or "")
        kind = kind_label(str(row.get("button_kind") or ""))
        lines.append(
            f"{when}  {who}  id={telegram_id}  {label}  {callback}  {kind}"
        )
    return "\n".join(lines) + "\n"


def day_axis_label(day: date) -> str:
    return format_date(day)

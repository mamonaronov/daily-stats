"""Built-in 1–5 daily ratings (wellbeing, energy, productivity, mood, day, stress)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from database.models import User
from database.queries import Repo
from services.ui_prefs import prefs_of
from utils.formatting import SCORE_EMOJI, SCORE_LABELS, score_text
from utils.time import format_date, parse_hhmm, user_now, user_today

MIN_SCORE = 1
MAX_SCORE = 5


@dataclass(frozen=True, slots=True)
class DailyScoreSpec:
    key: str
    code: str
    emoji: str
    label: str
    hint: str

    @property
    def button_label(self) -> str:
        return f"{self.emoji} {self.label}"


DAILY_SCORE_SPECS: tuple[DailyScoreSpec, ...] = (
    DailyScoreSpec(
        "wellbeing",
        "wb",
        "💚",
        "Самочувствие",
        "общее состояние здоровья, ничего не болит",
    ),
    DailyScoreSpec(
        "energy",
        "en",
        "⚡",
        "Энергия",
        "сколько сегодня было сил и энергии",
    ),
    DailyScoreSpec(
        "productivity",
        "pr",
        "📈",
        "Продуктивность",
        "насколько день был продуктивным в целом",
    ),
    DailyScoreSpec(
        "mood",
        "md",
        "😊",
        "Настроение",
        "насколько хорошим было настроение",
    ),
    DailyScoreSpec(
        "day_rating",
        "dr",
        "🌟",
        "Оценка дня",
        "насколько хорошим или плохим день был в целом",
    ),
    DailyScoreSpec(
        "stress",
        "st",
        "😰",
        "Стресс",
        "насколько спокойным был день, без давления и тревоги",
    ),
)

DAILY_SCORE_KEYS: tuple[str, ...] = tuple(spec.key for spec in DAILY_SCORE_SPECS)
SCORE_BY_KEY: dict[str, DailyScoreSpec] = {spec.key: spec for spec in DAILY_SCORE_SPECS}
SCORE_BY_CODE: dict[str, DailyScoreSpec] = {spec.code: spec for spec in DAILY_SCORE_SPECS}

HUB_EMOJI = "🙂"
HUB_LABEL = f"{HUB_EMOJI} Оценки дня"
OPEN_SCORE_PAGE = 8
OPEN_SCORES_CB = "ds:gaps"


def spec_of(key: str) -> DailyScoreSpec:
    if key not in SCORE_BY_KEY:
        raise KeyError(key)
    return SCORE_BY_KEY[key]


def parse_daily_score(raw: str) -> int:
    text = (raw or "").strip()
    if not text.isdigit():
        raise ValueError("score")
    value = int(text)
    if value < MIN_SCORE or value > MAX_SCORE:
        raise ValueError("score")
    return value


def format_score_line(spec: DailyScoreSpec, score: int | None) -> str:
    if score is None:
        return f"{spec.emoji} {spec.label} — нет"
    return f"{spec.emoji} {spec.label} — {score_text(score)}"


def format_score_compact(spec: DailyScoreSpec, score: int) -> str:
    return f"{spec.emoji} {SCORE_EMOJI.get(score, '')} {SCORE_LABELS.get(score, str(score))}".strip()


def tracked_score_keys(tracked: set[str]) -> list[str]:
    return [key for key in DAILY_SCORE_KEYS if key in tracked]


def missing_score_count(kinds: set[str], keys: list[str]) -> int:
    if not keys:
        return 0
    return sum(1 for key in keys if key not in kinds)


def missing_keys_by_day(
    pairs: list[tuple[str, str]],
    keys: list[str],
    days: list[date],
) -> dict[date, list[str]]:
    """Days that still miss a tracked score, mapped to those keys in tracker order."""
    needed = list(dict.fromkeys(keys))
    if not needed:
        return {}
    needed_set = set(needed)
    have: dict[str, set[str]] = defaultdict(set)
    for day, kind in pairs:
        if kind in needed_set:
            have[day].add(kind)
    missing: dict[date, list[str]] = {}
    for day in days:
        left = [key for key in needed if key not in have.get(day.isoformat(), set())]
        if left:
            missing[day] = left
    return missing


def missing_by_day(
    pairs: list[tuple[str, str]],
    keys: list[str],
    days: list[date],
) -> dict[date, int]:
    """Days that still miss at least one tracked score, mapped to the missing count."""
    return {day: len(left) for day, left in missing_keys_by_day(pairs, keys, days).items()}


def score_day_is_due(day: date, today: date, reminder_hhmm: str | None, local_now: datetime) -> bool:
    """Today joins the unrated list only after the score reminder clock."""
    if day > today:
        return False
    if day < today or not reminder_hhmm:
        return True
    try:
        hour, minute = parse_hhmm(reminder_hhmm)
    except ValueError:
        return True
    return local_now.hour * 60 + local_now.minute >= hour * 60 + minute


def missing_keys_since_first(
    pairs: list[tuple[str, str]],
    keys: list[str],
    today: date,
    skipped: set[tuple[str, str]] | None = None,
) -> dict[date, list[str]]:
    """Missing keys from each key's first recorded day through today.

    A key with no recorded day is not expected yet. ``pairs`` must include every
    recorded day of those keys up to ``today``. ``skipped`` is ``(iso day, key)``.
    """
    needed = list(dict.fromkeys(keys))
    if not needed:
        return {}
    needed_set = set(needed)
    have: dict[str, set[str]] = defaultdict(set)
    first: dict[str, date] = {}
    for day_s, kind in pairs:
        if kind not in needed_set:
            continue
        have[day_s].add(kind)
        day = date.fromisoformat(day_s)
        prev = first.get(kind)
        if prev is None or day < prev:
            first[kind] = day
    if not first:
        return {}
    hidden = skipped or set()
    start = min(first.values())
    if start > today:
        return {}
    missing: dict[date, list[str]] = {}
    current = start
    while current <= today:
        iso = current.isoformat()
        present = have.get(iso, set())
        left = [
            key
            for key in needed
            if key in first
            and current >= first[key]
            and key not in present
            and (iso, key) not in hidden
        ]
        if left:
            missing[current] = left
        current += timedelta(days=1)
    return missing


def page_open_scores(
    gaps: list[tuple[date, list[str]]],
    page: int,
    size: int = OPEN_SCORE_PAGE,
) -> tuple[list[tuple[date, list[str]]], int, int]:
    total = len(gaps)
    pages = max(1, (total + size - 1) // size) if total else 1
    current = min(max(int(page), 0), pages - 1)
    start = current * size
    return gaps[start : start + size], current, pages


def open_score_day_label(day: date, today: date) -> str:
    if day == today:
        return "сегодня"
    if day == today - timedelta(days=1):
        return "вчера"
    return format_date(day)


def open_scores_button_label(missing: int) -> str:
    if missing <= 0:
        return "Всё оценено"
    return f"Неоценено · {missing}"


def open_score_total(gaps: list[tuple[date, list[str]]]) -> int:
    return sum(len(keys) for _, keys in gaps)


def format_open_scores(gaps: list[tuple[date, list[str]]], today: date) -> str:
    if not gaps:
        return "Всё оценено.\n\nПустых оценок нет."
    lines = ["Не оценено", ""]
    for day, keys in gaps:
        names = ", ".join(f"{spec_of(key).emoji} {spec_of(key).label}" for key in keys)
        lines.append(f"{open_score_day_label(day, today)} — {names}")
    lines.append("")
    lines.append(
        "Нажмите день, чтобы поставить оценки. "
        "✖️ убирает запись, если не помните или не хотите оценивать."
    )
    return "\n".join(lines)


async def list_open_score_gaps(repo: Repo, user: User) -> list[tuple[date, list[str]]]:
    """Newest first: missing scores since each kind was first recorded.

    Today is included only after the local score-reminder time.
    """
    keys = tracked_score_keys(prefs_of(user).tracked)
    if not keys:
        return []
    local_now = user_now(user.timezone)
    today = local_now.date()
    earliest = await repo.earliest_daily_score_days(user.telegram_id)
    started = [date.fromisoformat(earliest[key]) for key in keys if key in earliest]
    if not started:
        return []
    start = min(started)
    if start > today:
        return []
    start_iso = start.isoformat()
    end_iso = today.isoformat()
    pairs = await repo.list_daily_score_kinds_between(user.telegram_id, start_iso, end_iso)
    skipped = set(
        await repo.list_daily_score_skips_between(user.telegram_id, start_iso, end_iso)
    )
    through = today
    if not score_day_is_due(today, today, user.daily_score_reminder_time, local_now):
        through = today - timedelta(days=1)
    missing = missing_keys_since_first(pairs, keys, through, skipped)
    return [(day, missing[day]) for day in sorted(missing, reverse=True)]


async def dismiss_open_score(repo: Repo, user: User, day: date, kind: str) -> str | None:
    """Hide one unrated score so it no longer appears in the gaps list."""
    if kind not in SCORE_BY_KEY:
        return "Неизвестная оценка."
    if day > user_today(user.timezone):
        return "Этот день ещё не наступил"
    await repo.add_daily_score_skip(user.telegram_id, day.isoformat(), kind)
    return None

"""Built-in 1–5 daily ratings (wellbeing, energy, productivity, mood, day, stress)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from database.models import User
from database.queries import Repo
from services.ui_prefs import prefs_of
from utils.formatting import SCORE_EMOJI, SCORE_LABELS, score_text
from utils.time import format_date, parse_hhmm, parse_iso, to_user, user_now

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
OPEN_SCORE_DAYS = 14
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


def score_gap_days(today: date, registered_on: date | None = None) -> list[date]:
    """Local days to check for empty scores, not earlier than registration."""
    start = today - timedelta(days=OPEN_SCORE_DAYS - 1)
    if registered_on is not None and registered_on > start:
        start = registered_on
    if start > today:
        return []
    days: list[date] = []
    current = start
    while current <= today:
        days.append(current)
        current += timedelta(days=1)
    return days


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
    lines.append("Нажмите день, чтобы поставить оценки.")
    return "\n".join(lines)


def _registered_local_day(user: User) -> date:
    return to_user(parse_iso(user.registered_at), user.timezone).date()


async def list_open_score_gaps(repo: Repo, user: User) -> list[tuple[date, list[str]]]:
    """Newest first: days in the lookback window that still miss a tracked score."""
    keys = tracked_score_keys(prefs_of(user).tracked)
    local_now = user_now(user.timezone)
    today = local_now.date()
    days = score_gap_days(today, _registered_local_day(user))
    if not keys or not days:
        return []
    due = [day for day in days if score_day_is_due(day, today, user.daily_score_reminder_time, local_now)]
    if not due:
        return []
    pairs = await repo.list_daily_score_kinds_between(
        user.telegram_id,
        days[0].isoformat(),
        days[-1].isoformat(),
    )
    missing = missing_keys_by_day(pairs, keys, due)
    return [(day, missing[day]) for day in reversed(days) if day in missing]

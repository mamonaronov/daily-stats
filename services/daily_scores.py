"""Built-in 1–5 daily ratings (wellbeing, energy, productivity, mood, day, stress)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from utils.formatting import SCORE_EMOJI, SCORE_LABELS, score_text

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


def missing_by_day(
    pairs: list[tuple[str, str]],
    keys: list[str],
    days: list[date],
) -> dict[date, int]:
    """Days that still miss at least one tracked score, mapped to the missing count."""
    needed = set(keys)
    if not needed:
        return {}
    have: dict[str, set[str]] = defaultdict(set)
    for day, kind in pairs:
        if kind in needed:
            have[day].add(kind)
    missing: dict[date, int] = {}
    for day in days:
        left = len(needed - have.get(day.isoformat(), set()))
        if left:
            missing[day] = left
    return missing

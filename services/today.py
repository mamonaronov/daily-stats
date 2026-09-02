"""Today's snapshot for the main screen."""

from __future__ import annotations

from dataclasses import dataclass

from database.models import SleepRecord, SnusPack, User
from database.queries import Repo
from services.daily_scores import DAILY_SCORE_SPECS, format_score_compact
from utils.formatting import format_int_spaces, format_kg
from utils.time import day_bounds_utc, format_time, parse_iso, to_iso, user_today

SLEEP_IN_BED = {"with_phone", "no_phone"}
SLEEP_OPEN = {"awake", "need_onset"}


@dataclass(frozen=True, slots=True)
class DaySnapshot:
    cigarettes: int
    snus_line: str
    sleep_line: str
    steps: int | None = None
    weight_kg: float | None = None
    scores: dict[str, int] | None = None

    def as_text(self, tracked: set[str] | None = None) -> str:
        def show(key: str) -> bool:
            return tracked is None or key in tracked

        lines: list[str] = []
        if show("cigarettes"):
            lines.append(f"🚬 {self.cigarettes}")
        if show("snus"):
            lines.append(f"🟢 {self.snus_line}")
        if show("sleep"):
            lines.append(f"😴 {self.sleep_line}")
        if show("steps") and self.steps is not None:
            lines.append(f"🚶 {format_int_spaces(self.steps)}")
        if show("weight") and self.weight_kg is not None:
            lines.append(f"⚖️ {format_kg(self.weight_kg)}")
        recorded = self.scores or {}
        for spec in DAILY_SCORE_SPECS:
            if show(spec.key) and spec.key in recorded:
                lines.append(format_score_compact(spec, recorded[spec.key]))
        if not lines:
            return ""
        return "\n".join(["<b>Сегодня</b>", *lines])


def sleep_status_line(sleep: SleepRecord | None) -> str:
    if sleep is None:
        return "нет записи"
    phase = sleep.phase()
    if phase in SLEEP_IN_BED:
        return "лёг"
    if phase in SLEEP_OPEN:
        return "не закрыт"
    return "нет записи"


def snus_status_line(pack: SnusPack | None, tz: str) -> str:
    if pack is None or not pack.bought_at:
        return "нет"
    return f"открыта с {format_time(parse_iso(pack.bought_at), tz)}"


async def day_snapshot(repo: Repo, user: User) -> DaySnapshot:
    today = user_today(user.timezone)
    start, end = day_bounds_utc(user.timezone, today)
    cigarettes = await repo.list_cigarettes(user.telegram_id, to_iso(start), to_iso(end))
    pack = await repo.oldest_open_snus(user.telegram_id)
    sleep = await repo.latest_sleep(user.telegram_id)
    steps_rec = await repo.get_steps_by_day(user.telegram_id, today.isoformat())
    weights = await repo.list_weight(user.telegram_id, to_iso(start), to_iso(end))
    latest_kg = weights[-1].kilograms if weights else None
    score_rows = await repo.list_daily_scores_for_day(user.telegram_id, today.isoformat())
    return DaySnapshot(
        cigarettes=len(cigarettes),
        snus_line=snus_status_line(pack, user.timezone),
        sleep_line=sleep_status_line(sleep),
        steps=steps_rec.steps if steps_rec else None,
        weight_kg=latest_kg,
        scores={row.kind: row.score for row in score_rows},
    )


EMPTY_TRACKED_HINT = "Пока нет выбранных метрик — отметьте их в Настройках."


async def today_block(repo: Repo, user: User) -> str:
    from services.ui_prefs import prefs_of

    prefs = prefs_of(user)
    text = (await day_snapshot(repo, user)).as_text(prefs.tracked)
    if text:
        return text
    if not prefs.tracked:
        return EMPTY_TRACKED_HINT
    return ""

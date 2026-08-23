"""Today's snapshot for the main screen."""

from __future__ import annotations

from dataclasses import dataclass

from database.models import SleepRecord, SnusPack, User
from database.queries import Repo
from utils.time import day_bounds_utc, format_time, parse_iso, to_iso, user_today

SLEEP_IN_BED = {"with_phone", "no_phone"}
SLEEP_OPEN = {"awake", "need_onset"}


@dataclass(frozen=True, slots=True)
class DaySnapshot:
    cigarettes: int
    snus_line: str
    sleep_line: str

    def as_text(self) -> str:
        return (
            f"<b>Сегодня</b>\n"
            f"🚬 {self.cigarettes}\n"
            f"🟢 {self.snus_line}\n"
            f"😴 {self.sleep_line}"
        )


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
    return DaySnapshot(
        cigarettes=len(cigarettes),
        snus_line=snus_status_line(pack, user.timezone),
        sleep_line=sleep_status_line(sleep),
    )


async def today_block(repo: Repo, user: User) -> str:
    return (await day_snapshot(repo, user)).as_text()

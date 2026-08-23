"""CSV export of a user's own diary rows."""

from __future__ import annotations

import csv
import io
from datetime import date

from database.models import User
from database.queries import Repo
from services.history import build_timeline
from utils.time import format_dt_compact, to_user

_HEADERS = ("время", "тип", "детали", "id")


def timeline_csv(user: User, items) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_HEADERS)
    for item in items:
        local = to_user(item.occurred_at, user.timezone)
        writer.writerow(
            [
                format_dt_compact(item.occurred_at, user.timezone)
                if hasattr(item.occurred_at, "tzinfo")
                else local.strftime("%d.%m.%Y %H:%M:%S"),
                item.title,
                item.detail,
                item.id,
            ]
        )
    return buf.getvalue()


async def export_user_csv(repo: Repo, user: User, start: date, end: date) -> tuple[str, str]:
    items = await build_timeline(repo, user, start, end)
    filename = f"daily-stats-{start.isoformat()}-{end.isoformat()}.csv"
    return filename, timeline_csv(user, items)

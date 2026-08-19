from __future__ import annotations

from datetime import date, datetime, timezone

from services.reminders import average_bedtime_minutes, reminder_clock_minutes
from services.statistics import cigarette_stats, pearson
from utils.time import circular_mean_minutes, combine_local, day_bounds_utc, minutes_to_hhmm, to_iso


def test_circular_mean_around_midnight():
    # 23:30 and 00:30 -> ~00:00
    mean = circular_mean_minutes([23 * 60 + 30, 30])
    assert mean is not None
    assert abs(mean - 0) <= 2 or abs(mean - 24 * 60) <= 2


def test_day_bounds_moscow():
    start, end = day_bounds_utc("Europe/Moscow", date(2026, 8, 17))
    # Moscow is UTC+3
    assert start.hour == 21
    assert start.day == 16
    assert (end - start).total_seconds() == 24 * 3600


def test_combine_local_stored_utc():
    dt = combine_local("Europe/Moscow", date(2026, 8, 17), 14, 35)
    assert dt.tzinfo is not None
    assert dt.hour == 11  # 14:35 MSK = 11:35 UTC


def test_reminder_three_hours_before_average():
    bedtimes = [
        to_iso(datetime(2026, 8, 14, 20, 30, tzinfo=timezone.utc)),  # 23:30 MSK
        to_iso(datetime(2026, 8, 15, 21, 0, tzinfo=timezone.utc)),  # 00:00 MSK
        to_iso(datetime(2026, 8, 16, 20, 45, tzinfo=timezone.utc)),  # 23:45 MSK
    ]
    avg = average_bedtime_minutes(bedtimes, "Europe/Moscow")
    clock = reminder_clock_minutes(avg, 3, "20:45")
    # average ~23:45, minus 3h = 20:45
    assert minutes_to_hhmm(clock) == "20:45"


def test_reminder_fallback_without_sleep():
    assert reminder_clock_minutes(None, 3, "20:45") == 20 * 60 + 45


def test_pearson_perfect():
    assert round(pearson([1, 2, 3, 4], [2, 4, 6, 8]) or 0, 5) == 1.0


def test_overnight_sleep_duration():
    from services.entries import _duration

    bed = "2026-08-16T20:00:00+00:00"  # 23:00 MSK
    wake = "2026-08-17T04:00:00+00:00"  # 07:00 MSK
    assert _duration(bed, wake) == 8 * 60


def test_snus_elapsed_spans_days():
    from services.entries import _elapsed_minutes

    bought = "2026-08-10T12:00:00+00:00"
    finished = "2026-08-13T18:30:00+00:00"
    assert _elapsed_minutes(bought, finished) == 3 * 24 * 60 + 6 * 60 + 30
    assert _elapsed_minutes(finished, bought) is None


def test_duration_human_days():
    from utils.formatting import duration_human

    assert duration_human(8 * 60) == "8 ч"
    assert duration_human(3 * 24 * 60 + 5 * 60) == "3 д 5 ч"
    assert duration_human(45) == "45 мин"


def test_seconds_human():
    from utils.formatting import seconds_human

    assert seconds_human(0) == "0 с"
    assert seconds_human(40) == "40 с"
    assert seconds_human(90) == "1 мин 30 с"
    assert seconds_human(3 * 3600 + 12 * 60) == "3 ч 12 мин"
    assert seconds_human(26 * 3600) == "1 д 2 ч"


def test_hours_kb_includes_past_hours_and_date_shortcuts():
    from keyboards.main import hours_kb

    kb = hours_kb(date_shortcuts=True)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "hr:0" in data
    assert "hr:7" in data
    assert "hr:23" in data
    assert "hdt:today" in data
    assert "hdt:yesterday" in data
    assert "hdt:calendar" in data


def test_cigarette_stats_text():
    from database.models import Cigarette, User

    user = User(
        telegram_id=5,
        username=None,
        first_name="A",
        last_name=None,
        registered_at="2026-08-01T00:00:00+00:00",
        timezone="UTC",
        status="active",
        last_activity_at=None,
        balance=10,
        daily_price=1,
        paid_until_date=None,
        last_charge_date=None,
        deleted_at=None,
        bot_blocked_at=None,
        created_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T00:00:00+00:00",
    )
    items = [
        Cigarette(1, 5, "2026-08-17T08:00:00+00:00", "2026-08-17T08:00:00+00:00"),
        Cigarette(2, 5, "2026-08-17T10:00:00+00:00", "2026-08-17T10:00:00+00:00"),
    ]
    text = cigarette_stats(user, items, date(2026, 8, 17), date(2026, 8, 17))
    assert "Всего: 2" in text
    assert "Средний интервал" in text


def test_snus_stats_text():
    from database.models import SnusPack, User
    from services.statistics import snus_stats

    user = User(
        telegram_id=5,
        username=None,
        first_name="A",
        last_name=None,
        registered_at="2026-08-01T00:00:00+00:00",
        timezone="UTC",
        status="active",
        last_activity_at=None,
        balance=10,
        daily_price=1,
        paid_until_date=None,
        last_charge_date=None,
        deleted_at=None,
        bot_blocked_at=None,
        created_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T00:00:00+00:00",
    )
    items = [
        SnusPack(
            1,
            5,
            "2026-08-10T12:00:00+00:00",
            "2026-08-13T12:00:00+00:00",
            3 * 24 * 60,
            "2026-08-10T12:00:00+00:00",
            "2026-08-13T12:00:00+00:00",
        ),
        SnusPack(
            2,
            5,
            "2026-08-14T12:00:00+00:00",
            "2026-08-16T12:00:00+00:00",
            2 * 24 * 60,
            "2026-08-14T12:00:00+00:00",
            "2026-08-16T12:00:00+00:00",
        ),
    ]
    text = snus_stats(user, items, date(2026, 8, 10), date(2026, 8, 17))
    assert "Законченных шайб: 2" in text
    assert "В среднем хватает: 2 д 12 ч" in text
    assert "Минимум: 2 д" in text
    assert "Максимум: 3 д" in text

from __future__ import annotations

from datetime import date, datetime, timezone

from services.statistics import cigarette_stats, pearson
from utils.time import circular_mean_minutes, combine_local, day_bounds_utc


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


def test_host_uptime_seconds(tmp_path):
    from utils.uptime import host_uptime_seconds

    path = tmp_path / "uptime"
    path.write_text("12345.67 88888.00\n", encoding="utf-8")
    assert host_uptime_seconds(path) == 12345.67
    assert host_uptime_seconds(tmp_path / "missing") is None
    bad = tmp_path / "bad"
    bad.write_text("not-a-number\n", encoding="utf-8")
    assert host_uptime_seconds(bad) is None


def test_process_uptime_from_stat():
    from utils.uptime import process_uptime_from_stat

    fields = ["0"] * 20
    fields[19] = "200"
    stat = "1 (python) " + " ".join(fields)
    assert process_uptime_from_stat(stat, 30.0, 100) == 28.0


def test_uptime_report_lines(monkeypatch):
    import utils.uptime as uptime

    monkeypatch.setattr(uptime, "bot_uptime_seconds", lambda: 90)
    monkeypatch.setattr(uptime, "host_uptime_seconds", lambda: 26 * 3600)
    monkeypatch.setattr(uptime, "app_build_identity", lambda: ("deadbeef", "fix: uptime <commit>"))
    lines = uptime.uptime_report_lines()
    assert lines[0] == "Аптайм бота: 1 мин 30 с"
    assert lines[1] == "Аптайм сервера: 1 д 2 ч"
    assert lines[2] == "Коммит: fix: uptime &lt;commit&gt; (<code>deadbeef</code>)"
    assert len(lines) == 3


def test_mark_bot_started(monkeypatch):
    import utils.uptime as uptime

    clock = {"now": 10.0}
    monkeypatch.setattr(uptime.time, "monotonic", lambda: clock["now"])
    uptime._started_monotonic = None
    uptime.mark_bot_started()
    clock["now"] = 100.0
    assert uptime.bot_uptime_seconds() == 90.0
    uptime._started_monotonic = None


def test_hours_kb_includes_past_hours_and_date_shortcuts():
    from keyboards.main import hours_kb

    kb = hours_kb(date_shortcuts=True)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "hr:0" in data
    assert "hr:7" in data
    assert "hr:23" in data
    assert "hdt:today" in data
    assert "hdt:yesterday" in data
    assert "hdt:daybefore" in data
    assert "hdt:calendar" in data
    assert "hr:manual" in data


def test_parse_hhmm_accepts_compact_and_spaced_clock():
    from utils.time import parse_hhmm

    assert parse_hhmm("10:00") == (10, 0)
    assert parse_hhmm("1000") == (10, 0)
    assert parse_hhmm("10 00") == (10, 0)
    assert parse_hhmm("10.00") == (10, 0)
    assert parse_hhmm("930") == (9, 30)
    assert parse_hhmm("14:35") == (14, 35)


def test_parse_minutes_ago_units():
    from utils.time import parse_minutes_ago

    assert parse_minutes_ago("7") == 7
    assert parse_minutes_ago("90 мин") == 90
    assert parse_minutes_ago("1 час") == 60
    assert parse_minutes_ago("1ч 20м") == 80
    assert parse_minutes_ago("1.5 часа") == 90


def test_parse_when_text_clock_or_minutes_ago():
    from datetime import datetime, timezone

    from utils.time import parse_when_text, to_user

    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)  # 15:00 MSK
    local = to_user(parse_when_text("1000", "Europe/Moscow", now=now), "Europe/Moscow")
    assert (local.hour, local.minute, local.day) == (10, 0, 23)
    local = to_user(parse_when_text("10 00", "Europe/Moscow", now=now), "Europe/Moscow")
    assert (local.hour, local.minute) == (10, 0)
    local = to_user(parse_when_text("20:00", "Europe/Moscow", now=now), "Europe/Moscow")
    assert (local.hour, local.minute, local.day) == (20, 0, 22)
    ago = parse_when_text("15", "Europe/Moscow", now=now)
    assert int((now - ago).total_seconds()) == 15 * 60


def test_parse_calendar_token_relative_days():
    from datetime import date

    from utils.time import parse_calendar_token

    today = date(2026, 8, 23)
    assert parse_calendar_token("today", today) == today
    assert parse_calendar_token("yesterday", today) == date(2026, 8, 22)
    assert parse_calendar_token("daybefore", today) == date(2026, 8, 21)
    assert parse_calendar_token("2026-08-10", today) == date(2026, 8, 10)


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


def test_fooling_stats_text():
    from database.models import Fooling, User
    from services.statistics import fooling_stats

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
        Fooling(1, 5, "2026-08-17T08:00:00+00:00", "2026-08-17T08:00:00+00:00"),
        Fooling(2, 5, "2026-08-17T10:00:00+00:00", "2026-08-17T10:00:00+00:00"),
    ]
    text = fooling_stats(user, items, date(2026, 8, 17), date(2026, 8, 17))
    assert "Всего: 2" in text
    assert "Средний интервал" in text
    assert "Валять дурака" in text


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


def test_alcohol_stats_include_volume():
    from database.models import AlcoholRecord, User
    from services.statistics import drink_stats
    from utils.formatting import ALCOHOL_TYPES

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
        AlcoholRecord(1, 5, "beer", 500, "мл", None, "2026-08-17T18:00:00+00:00", "2026-08-17T18:00:00+00:00"),
        AlcoholRecord(2, 5, "wine", 150, "мл", None, "2026-08-17T20:00:00+00:00", "2026-08-17T20:00:00+00:00"),
    ]
    text = drink_stats("🍺 Алкоголь", user, items, "drink_type", ALCOHOL_TYPES, date(2026, 8, 17), date(2026, 8, 17))
    assert "Всего: 2" in text
    assert "Объём: 0,65 л" in text
    assert "пиво — 0,5 л" in text
    assert "вино — 150 мл" in text

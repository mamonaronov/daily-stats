from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from utils.formatting import (
    balance_coverage_block,
    balance_runway,
    colon_block,
    coverage,
    extra_paid_days,
    paid_days,
    pre_html,
)


def _user(**kwargs):
    fields = dict(
        balance=100.0,
        daily_price=10.0,
        timezone="Europe/Moscow",
        paid_until_date=None,
    )
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def test_extra_paid_days_unlimited_and_empty():
    assert extra_paid_days(100, 0) is None
    assert extra_paid_days(0, 10) == 0
    assert extra_paid_days(99, 10) == 9
    assert paid_days(50, 0) == "безлимит"


def test_coverage_when_today_already_paid():
    today = date(2026, 8, 23)
    days, until = coverage(100, 10, today, "2026-08-23")
    assert days == 11
    assert until == date(2026, 9, 2)


def test_coverage_when_today_not_paid_yet():
    today = date(2026, 8, 23)
    days, until = coverage(100, 10, today, None)
    assert days == 10
    assert until == date(2026, 9, 1)


def test_coverage_stable_across_today_charge():
    today = date(2026, 8, 23)
    before = coverage(110, 10, today, None)
    after = coverage(100, 10, today, "2026-08-23")
    assert before == after
    assert before == (11, date(2026, 9, 2))


def test_coverage_today_paid_but_no_extra():
    today = date(2026, 8, 23)
    days, until = coverage(0, 10, today, "2026-08-23")
    assert days == 1
    assert until == today


def test_coverage_unpaid():
    days, until = coverage(3, 10, date(2026, 8, 23), None)
    assert days == 0
    assert until is None


def test_coverage_free():
    days, until = coverage(0, 0, date(2026, 8, 23), None)
    assert days is None
    assert until is None


def test_balance_runway_shows_days_and_date():
    user = _user(balance=100, daily_price=10, paid_until_date="2026-08-23")
    text = balance_runway(user, today=date(2026, 8, 23))
    assert text == "осталось 11 дней, хватит до 2 сентября 2026"


def test_balance_coverage_block_splits_lines():
    user = _user(balance=100, daily_price=10, paid_until_date="2026-08-23")
    text = balance_coverage_block(user, today=date(2026, 8, 23))
    assert text == "Осталось: 11 дней\nХватит до: 2 сентября 2026"


def test_balance_runway_unpaid_and_free():
    today = date(2026, 8, 23)
    assert balance_runway(_user(balance=0, paid_until_date=None), today=today) == "уже не хватает"
    assert balance_runway(_user(daily_price=0), today=today) == "безлимит"
    assert balance_coverage_block(_user(daily_price=0), today=today) == "Безлимит"


def test_colon_block_aligns_colons():
    text = colon_block(
        [
            ("Аптайм бота", "1 мин 30 с"),
            ("Аптайм сервера", "1 д 2 ч"),
            ("Коммит", "deadbeef"),
        ]
    )
    rows = text.splitlines()
    assert rows[0] == "   Аптайм бота: 1 мин 30 с"
    assert rows[1] == "Аптайм сервера: 1 д 2 ч"
    assert rows[2] == "        Коммит: deadbeef"
    assert len({row.index(":") for row in rows}) == 1
    assert colon_block([]) == ""


def test_pre_html_escapes_markup():
    assert pre_html("a <b> & c") == "<pre>a &lt;b&gt; &amp; c</pre>"

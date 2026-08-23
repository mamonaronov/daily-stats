from __future__ import annotations

from datetime import date

import pytest

from services.balance import debit, format_finance_stats, gift
from services.billing import charge_user_for_day


@pytest.mark.asyncio
async def test_finance_splits_income_gifts_and_usage(repo):
    await repo.create_user(10, "ann", "Анна", None, "UTC", 10.0, "23:00")
    await repo.create_user(20, "bob", "Боб", None, "UTC", 10.0, "23:00")
    await repo.create_user(30, "free", "Фри", None, "UTC", 0.0, "23:00")
    await repo.apply_balance_change(10, "credit", delta=100, comment="pay", performed_by=1)
    await gift(repo, 10, 40, comment="подарок", performed_by=1)
    await repo.apply_balance_change(20, "credit", delta=30, comment="pay", performed_by=1)
    day = date(2026, 8, 17)
    assert await charge_user_for_day(repo, await repo.get_user(10), day) == "charged"
    assert await charge_user_for_day(repo, await repo.get_user(20), day) == "charged"
    assert await charge_user_for_day(repo, await repo.get_user(30), day) == "free"
    await debit(repo, 10, 5, comment="ручное", performed_by=1)

    totals = await repo.finance_totals()
    assert totals["income"] == pytest.approx(130)
    assert totals["gifts"] == pytest.approx(40)
    assert totals["deposits"] == pytest.approx(170)
    assert totals["usage_charged"] == pytest.approx(20)
    assert totals["usage_count"] == 2
    assert totals["admin_debits"] == pytest.approx(5)

    by_user = await repo.finance_usage_by_user()
    by_id = {row["telegram_id"]: row for row in by_user}
    assert 30 not in by_id
    assert by_id[10]["charged"] == pytest.approx(10)
    assert by_id[10]["charge_count"] == 1
    assert by_id[10]["display_name"] == "Анна"
    assert by_id[20]["charged"] == pytest.approx(10)


def test_format_finance_stats_text():
    text = format_finance_stats(
        {
            "usage_charged": 20,
            "usage_count": 2,
            "deposits": 170,
            "income": 130,
            "gifts": 40,
            "admin_debits": 5,
        },
        [{"display_name": "Анна", "charged": 10, "charge_count": 1}],
    )
    assert "Заработано за пользование: 20 ₽ · 2 списания" in text
    assert "Анна: 10 ₽ (1 списание)" in text
    assert "Положили на счета: 170 ₽" in text
    assert "доход: 130 ₽" in text
    assert "подарки: 40 ₽" in text
    assert "Ручные списания в админке: 5 ₽" in text


def test_format_finance_stats_empty_and_escapes_names():
    empty = format_finance_stats({}, [])
    assert "пока никого" in empty
    assert "0 списаний" in empty
    escaped = format_finance_stats(
        {"usage_charged": 1, "usage_count": 1},
        [{"display_name": "<b>x</b>", "charged": 1, "charge_count": 1}],
    )
    assert "&lt;b&gt;x&lt;/b&gt;" in escaped
    assert "<b>x</b>" not in escaped

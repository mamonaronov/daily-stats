"""Balance mutations always go through a ledger row."""

from __future__ import annotations

import html

from database.queries import Repo
from utils.formatting import money

SYSTEM_ACTOR = 0

OPERATION_LABELS = {
    "credit": "доход",
    "gift": "подарок",
    "debit": "списание",
    "refund": "возврат",
    "set": "установка",
    "adjustment": "корректировка",
}


class BalanceError(RuntimeError):
    pass


def operation_label(operation_type: str) -> str:
    return OPERATION_LABELS.get(operation_type, operation_type)


def _ru_charges(n: int) -> str:
    value = abs(int(n))
    mod100 = value % 100
    mod10 = value % 10
    if 11 <= mod100 <= 14:
        word = "списаний"
    elif mod10 == 1:
        word = "списание"
    elif 2 <= mod10 <= 4:
        word = "списания"
    else:
        word = "списаний"
    return f"{int(n)} {word}"


def format_finance_stats(totals: dict, by_user: list[dict], *, limit: int = 15) -> str:
    usage = float(totals.get("usage_charged") or 0)
    count = int(totals.get("usage_count") or 0)
    lines = [f"Заработано за пользование: {money(usage)} · {_ru_charges(count)}"]
    shown = by_user[:limit]
    if shown:
        for row in shown:
            name = html.escape(str(row["display_name"]))
            lines.append(
                f"  {name}: {money(row['charged'])} ({_ru_charges(row['charge_count'])})"
            )
        extra = len(by_user) - len(shown)
        if extra:
            lines.append(f"  … и ещё {extra}")
    else:
        lines.append("  пока никого")
    lines.append("")
    lines.append(f"Положили на счета: {money(float(totals.get('deposits') or 0))}")
    lines.append(f"  доход: {money(float(totals.get('income') or 0))}")
    lines.append(f"  подарки: {money(float(totals.get('gifts') or 0))}")
    admin_debits = float(totals.get("admin_debits") or 0)
    if admin_debits:
        lines.append(f"Ручные списания в админке: {money(admin_debits)} (не в доходе)")
    return "\n".join(lines)


async def credit(
    repo: Repo,
    telegram_id: int,
    amount: float,
    *,
    comment: str | None,
    performed_by: int,
    idempotency_key: str | None = None,
) -> tuple[bool, float, float]:
    if amount <= 0:
        raise BalanceError("Сумма пополнения должна быть больше нуля")
    return await repo.apply_balance_change(
        telegram_id,
        "credit",
        delta=amount,
        comment=comment,
        performed_by=performed_by,
        idempotency_key=idempotency_key,
    )


async def gift(
    repo: Repo,
    telegram_id: int,
    amount: float,
    *,
    comment: str | None,
    performed_by: int,
    idempotency_key: str | None = None,
) -> tuple[bool, float, float]:
    if amount <= 0:
        raise BalanceError("Сумма подарка должна быть больше нуля")
    return await repo.apply_balance_change(
        telegram_id,
        "gift",
        delta=amount,
        comment=comment,
        performed_by=performed_by,
        idempotency_key=idempotency_key,
    )


async def debit(
    repo: Repo,
    telegram_id: int,
    amount: float,
    *,
    comment: str | None,
    performed_by: int | None,
    idempotency_key: str | None = None,
    paid_until_date: str | None = None,
    last_charge_date: str | None = None,
) -> tuple[bool, float, float]:
    if amount < 0:
        raise BalanceError("Сумма списания не может быть отрицательной")
    return await repo.apply_balance_change(
        telegram_id,
        "debit",
        delta=-amount,
        comment=comment,
        performed_by=performed_by,
        idempotency_key=idempotency_key,
        paid_until_date=paid_until_date,
        last_charge_date=last_charge_date,
    )


async def refund(
    repo: Repo,
    telegram_id: int,
    amount: float,
    *,
    comment: str | None,
    performed_by: int,
) -> tuple[bool, float, float]:
    if amount <= 0:
        raise BalanceError("Сумма возврата должна быть больше нуля")
    return await repo.apply_balance_change(
        telegram_id,
        "refund",
        delta=amount,
        comment=comment,
        performed_by=performed_by,
    )


async def set_balance(
    repo: Repo,
    telegram_id: int,
    value: float,
    *,
    comment: str | None,
    performed_by: int,
) -> tuple[bool, float, float]:
    return await repo.apply_balance_change(
        telegram_id,
        "set",
        set_to=value,
        comment=comment,
        performed_by=performed_by,
    )


async def adjust(
    repo: Repo,
    telegram_id: int,
    delta: float,
    *,
    comment: str | None,
    performed_by: int,
) -> tuple[bool, float, float]:
    return await repo.apply_balance_change(
        telegram_id,
        "adjustment",
        delta=delta,
        comment=comment,
        performed_by=performed_by,
    )

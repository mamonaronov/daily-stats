"""Balance mutations always go through a ledger row."""

from __future__ import annotations

from database.queries import Repo


class BalanceError(RuntimeError):
    pass


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

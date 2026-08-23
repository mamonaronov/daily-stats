"""User-reported payment. Owner tops up manually; balance does not change here."""

from __future__ import annotations

from aiogram import Bot

from config import Config
from database.models import User
from keyboards.main import admin_user_kb
from services.alerts import notify_owner
from utils.formatting import money


async def report_payment(bot: Bot, config: Config, user: User, amount: str | None) -> None:
    extra = f"Сумма: {amount}" if amount else "Сумма не указана"
    await notify_owner(
        bot,
        config,
        (
            f"«Я оплатил» от {user.display_name} (id {user.telegram_id})\n"
            f"Баланс: {money(user.balance)}\n"
            f"{extra}"
        ),
        reply_markup=admin_user_kb(user.telegram_id),
    )

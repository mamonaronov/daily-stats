"""Registration and /start."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import show_main
from keyboards.main import back_kb, timezone_kb
from services.billing import process_user
from services.reminders import refresh_user_reminder
from states.diary import RegisterSG
from utils.telegram import safe_edit
from utils.time import is_valid_timezone

router = Router(name="start")


async def _activate(
    repo: Repo,
    config: Config,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    timezone: str,
) -> User:
    existing = await repo.get_user(telegram_id)
    if existing is None:
        user = await repo.create_user(
            telegram_id,
            username,
            first_name,
            last_name,
            timezone,
            config.default_daily_price,
            config.default_sleep_time,
        )
    else:
        user = await repo.restore_user(telegram_id, username, first_name, last_name)
        await repo.set_timezone(telegram_id, timezone)
        user = await repo.get_user(telegram_id)
        assert user is not None
    await process_user(repo, user)
    user = await repo.get_user(telegram_id)
    assert user is not None
    await refresh_user_reminder(repo, user, config)
    return user


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, repo: Repo, config: Config, db_user: User | None, is_owner: bool) -> None:
    await state.clear()
    if db_user and db_user.is_banned:
        await message.answer("Доступ ограничен. Напишите владельцу сервиса.")
        return
    if db_user and db_user.is_active:
        await show_main(message, db_user, config, is_owner, state)
        return
    prompt = "Выберите часовой пояс. Он нужен для статистики, границ дня и напоминаний."
    if db_user and db_user.is_deleted:
        prompt = "Аккаунт был удалён. Данные сохранены.\n\nВыберите часовой пояс, чтобы восстановить доступ."
    await state.set_state(RegisterSG.timezone)
    await message.answer(prompt, reply_markup=timezone_kb())


@router.callback_query(F.data.startswith("tz:"), RegisterSG.timezone)
async def pick_tz(cb: CallbackQuery, state: FSMContext, repo: Repo, config: Config, is_owner: bool) -> None:
    token = cb.data.split(":", 1)[1]
    if token == "custom":
        await state.set_state(RegisterSG.timezone_custom)
        await cb.answer()
        await safe_edit(cb.message, "Введите IANA-имя пояса, например Europe/Moscow", back_kb("tz:list", menu=False))
        return
    if not is_valid_timezone(token):
        await cb.answer("Неизвестный пояс", show_alert=True)
        return
    user = cb.from_user
    db_user = await _activate(repo, config, user.id, user.username, user.first_name, user.last_name, token)
    await cb.answer()
    await show_main(cb, db_user, config, is_owner, state)


@router.callback_query(F.data == "tz:list", RegisterSG.timezone_custom)
async def tz_list_register(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegisterSG.timezone)
    await cb.answer()
    await safe_edit(cb.message, "Выберите часовой пояс. Он нужен для статистики, границ дня и напоминаний.", timezone_kb())


@router.message(RegisterSG.timezone_custom)
async def custom_tz(message: Message, state: FSMContext, repo: Repo, config: Config, is_owner: bool) -> None:
    token = (message.text or "").strip()
    if not is_valid_timezone(token):
        await message.answer(
            "Не получилось распознать пояс. Пример: Asia/Yekaterinburg",
            reply_markup=back_kb("tz:list", menu=False),
        )
        return
    user = message.from_user
    db_user = await _activate(repo, config, user.id, user.username, user.first_name, user.last_name, token)
    await show_main(message, db_user, config, is_owner, state)

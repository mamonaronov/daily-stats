"""User settings, timezone, account deletion."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, show_main
from keyboards.main import back_kb, cancel_kb, confirm_delete_kb, settings_kb, timezone_kb
from states.diary import SettingsSG
from utils.callbacks import NAV_SETTINGS
from utils.telegram import safe_edit
from utils.time import is_valid_timezone, parse_hhmm

router = Router(name="settings")


@router.callback_query(F.data == NAV_SETTINGS)
async def settings_root(cb: CallbackQuery, state: FSMContext, db_user: User | None, repo: Repo) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.clear()
    user = await repo.get_user(user.telegram_id) or user
    await cb.answer()
    await safe_edit(cb.message, "⚙️ Настройки", settings_kb(user))


@router.callback_query(F.data == "set:tz")
async def set_tz(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await state.set_state(SettingsSG.timezone_custom)
    await cb.answer()
    await safe_edit(cb.message, "Выберите часовой пояс:", timezone_kb(NAV_SETTINGS))


@router.callback_query(F.data.startswith("tz:"), F.data != "tz:list", SettingsSG.timezone_custom)
async def set_tz_pick(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":", 1)[1]
    if token == "custom":
        await cb.answer()
        await safe_edit(cb.message, "Введите IANA-имя, например Europe/Moscow", back_kb("tz:list"))
        return
    if not is_valid_timezone(token):
        await cb.answer("Неизвестный пояс", show_alert=True)
        return
    await repo.set_timezone(user.telegram_id, token)
    user = await repo.get_user(user.telegram_id)
    assert user
    await show_main(cb, user, config, is_owner, state)


@router.message(SettingsSG.timezone_custom)
async def set_tz_custom(
    message: Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    user = await require_active(message, db_user)
    if user is None:
        return
    token = (message.text or "").strip()
    if not is_valid_timezone(token):
        await message.answer("Не получилось распознать пояс.", reply_markup=back_kb("tz:list"))
        return
    await repo.set_timezone(user.telegram_id, token)
    user = await repo.get_user(user.telegram_id)
    assert user
    await show_main(message, user, config, is_owner, state)


@router.callback_query(F.data == "tz:list", SettingsSG.timezone_custom)
async def set_tz_list(cb: CallbackQuery, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "Выберите часовой пояс:", timezone_kb(NAV_SETTINGS))


@router.callback_query(F.data == "set:sleep")
async def set_sleep(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await state.set_state(SettingsSG.sleep_time)
    await cb.answer()
    await safe_edit(cb.message, "Введите обычное время сна ЧЧ:ММ", cancel_kb(NAV_SETTINGS))


@router.message(SettingsSG.sleep_time)
async def save_sleep(
    message: Message,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_active(message, db_user)
    if user is None:
        return
    try:
        hour, minute = parse_hhmm(message.text or "")
    except ValueError:
        await message.answer("Пример: 23:30", reply_markup=cancel_kb(NAV_SETTINGS))
        return
    value = f"{hour:02d}:{minute:02d}"
    await repo.update_settings(user.telegram_id, default_sleep_time=value)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await message.answer("Сохранено", reply_markup=settings_kb(user))


@router.callback_query(F.data == "set:contact")
async def contact(cb: CallbackQuery, config: Config, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(
        cb.message,
        f"Владелец сервиса: {config.owner_contact}\nПо вопросам оплаты и доступа пишите сюда.",
        settings_kb(db_user),
    )


@router.callback_query(F.data == "set:del")
async def delete_ask(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await state.set_state(SettingsSG.confirm_delete)
    await cb.answer()
    await safe_edit(
        cb.message,
        "Удалить аккаунт?\nЗаписи физически не стираются и останутся для аудита. "
        "Вы потеряете доступ, пока не нажмёте /start снова.",
        confirm_delete_kb(),
    )


@router.callback_query(F.data == "set:del:yes", SettingsSG.confirm_delete)
async def delete_yes(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await repo.mark_deleted(user)
    await state.clear()
    await cb.answer()
    await safe_edit(
        cb.message,
        "Аккаунт помечен как удалённый. Данные сохранены.\n/start — восстановить доступ.",
    )

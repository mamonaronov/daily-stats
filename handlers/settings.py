"""User settings, timezone, account deletion."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, show_main
from keyboards.main import (
    back_kb,
    cancel_kb,
    confirm_delete_kb,
    export_period_kb,
    hours_kb,
    minutes_kb,
    owner_write_kb,
    score_reminder_kb,
    settings_kb,
    timezone_kb,
    track_metrics_kb,
    wake_reminder_kb,
)
from services.legal import legal_contact
from states.diary import SettingsSG
from utils.callbacks import NAV_SETTINGS
from utils.telegram import safe_edit, text_file
from utils.time import add_days, is_valid_timezone, parse_hhmm, user_today

router = Router(name="settings")

TRACK_PROMPT = "Какие метрики вести:"


async def show_track_metrics(event: CallbackQuery | Message, user: User) -> None:
    from services.ui_prefs import prefs_of

    text, markup = TRACK_PROMPT, track_metrics_kb(prefs_of(user).tracked)
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
        return
    await safe_edit(event.message, text, markup)


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
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, "Сохранено", settings_kb(user))


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
    await state.clear()
    await message.answer("Сохранено", reply_markup=settings_kb(user))


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


@router.callback_query(F.data == "set:sedge")
async def toggle_sleep_empty_edges(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    from services.ui_prefs import prefs_of, save_prefs

    prefs = prefs_of(user)
    prefs.hide_sleep_empty_edges = not prefs.hide_sleep_empty_edges
    user = await save_prefs(repo, user, prefs)
    await cb.answer("Сохранено")
    await safe_edit(cb.message, "⚙️ Настройки", settings_kb(user))


WAKE_REMINDER_PROMPT = (
    "Во сколько напомнить отметить подъём?\n"
    "Если к этому времени ещё нет записи «встал», бот напишет."
)


def _wake_prompt(user: User) -> str:
    current = user.wake_up_reminder_time or "выкл"
    return f"{WAKE_REMINDER_PROMPT}\n\nСейчас: {current}"


@router.callback_query(F.data == "set:wake")
async def wake_reminder_root(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, _wake_prompt(user), wake_reminder_kb(user.wake_up_reminder_time))


@router.callback_query(F.data == "set:wake:off")
async def wake_reminder_off(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await repo.set_wake_up_reminder(user.telegram_id, None)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await cb.answer("Выключено")
    await safe_edit(cb.message, "Сохранено", settings_kb(user))


@router.callback_query(F.data == "set:wake:custom")
async def wake_reminder_custom(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.set_state(SettingsSG.wake_hour)
    await cb.answer()
    await safe_edit(
        cb.message,
        "Выберите час. Напоминание придёт в это время по вашему поясу.",
        hours_kb(prefix="wrh", back="set:wake"),
    )


@router.callback_query(F.data.regexp(r"^set:wake:\d{1,2}:\d{2}$"))
async def wake_reminder_preset(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    token = cb.data.removeprefix("set:wake:")
    try:
        hour, minute = parse_hhmm(token)
    except ValueError:
        await cb.answer("Некорректное время", show_alert=True)
        return
    value = f"{hour:02d}:{minute:02d}"
    await repo.set_wake_up_reminder(user.telegram_id, value)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await cb.answer("Сохранено")
    await safe_edit(cb.message, "Сохранено", settings_kb(user))


@router.callback_query(F.data.startswith("wrh:"), SettingsSG.wake_hour)
async def wake_reminder_hour(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    token = cb.data.split(":", 1)[1]
    if token == "manual":
        await state.set_state(SettingsSG.wake_manual)
        await cb.answer()
        await safe_edit(cb.message, "Введите время ЧЧ:ММ", cancel_kb("set:wake"))
        return
    await state.update_data(picked_hour=int(token))
    await state.set_state(SettingsSG.wake_minute)
    await cb.answer()
    await safe_edit(
        cb.message,
        f"Час: {int(token):02d}\nВыберите минуты:",
        minutes_kb(prefix="wrm", back="set:wake:custom"),
    )


@router.callback_query(F.data.startswith("wrm:"), SettingsSG.wake_minute)
async def wake_reminder_minute(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    minute = int(cb.data.split(":", 1)[1])
    data = await state.get_data()
    hour = int(data["picked_hour"])
    value = f"{hour:02d}:{minute:02d}"
    await repo.set_wake_up_reminder(user.telegram_id, value)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await cb.answer("Сохранено")
    await safe_edit(cb.message, "Сохранено", settings_kb(user))


@router.message(SettingsSG.wake_manual)
async def wake_reminder_manual(
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
        await message.answer("Пример: 09:30", reply_markup=cancel_kb("set:wake"))
        return
    value = f"{hour:02d}:{minute:02d}"
    await repo.set_wake_up_reminder(user.telegram_id, value)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await message.answer("Сохранено", reply_markup=settings_kb(user))


SCORE_REMINDER_PROMPT = (
    "Во сколько напомнить оценить день?\n"
    "Если к этому времени ещё нет всех оценок за сегодня, бот напишет. "
    "Оценки включаются в Метриках."
)


def _score_prompt(user: User) -> str:
    current = user.daily_score_reminder_time or "выкл"
    return f"{SCORE_REMINDER_PROMPT}\n\nСейчас: {current}"


@router.callback_query(F.data == "set:dsr")
async def score_reminder_root(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.clear()
    await cb.answer()
    await safe_edit(cb.message, _score_prompt(user), score_reminder_kb(user.daily_score_reminder_time))


@router.callback_query(F.data == "set:dsr:off")
async def score_reminder_off(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await repo.set_daily_score_reminder(user.telegram_id, None)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await cb.answer("Выключено")
    await safe_edit(cb.message, "Сохранено", settings_kb(user))


@router.callback_query(F.data == "set:dsr:custom")
async def score_reminder_custom(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.set_state(SettingsSG.score_hour)
    await cb.answer()
    await safe_edit(
        cb.message,
        "Выберите час. Напоминание придёт в это время по вашему поясу.",
        hours_kb(prefix="srh", back="set:dsr"),
    )


@router.callback_query(F.data.regexp(r"^set:dsr:\d{1,2}:\d{2}$"))
async def score_reminder_preset(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    token = cb.data.removeprefix("set:dsr:")
    try:
        hour, minute = parse_hhmm(token)
    except ValueError:
        await cb.answer("Некорректное время", show_alert=True)
        return
    value = f"{hour:02d}:{minute:02d}"
    await repo.set_daily_score_reminder(user.telegram_id, value)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await cb.answer("Сохранено")
    await safe_edit(cb.message, "Сохранено", settings_kb(user))


@router.callback_query(F.data.startswith("srh:"), SettingsSG.score_hour)
async def score_reminder_hour(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    token = cb.data.split(":", 1)[1]
    if token == "manual":
        await state.set_state(SettingsSG.score_manual)
        await cb.answer()
        await safe_edit(cb.message, "Введите время ЧЧ:ММ", cancel_kb("set:dsr"))
        return
    await state.update_data(picked_hour=int(token))
    await state.set_state(SettingsSG.score_minute)
    await cb.answer()
    await safe_edit(
        cb.message,
        f"Час: {int(token):02d}\nВыберите минуты:",
        minutes_kb(prefix="srm", back="set:dsr:custom"),
    )


@router.callback_query(F.data.startswith("srm:"), SettingsSG.score_minute)
async def score_reminder_minute(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    minute = int(cb.data.split(":", 1)[1])
    data = await state.get_data()
    hour = int(data["picked_hour"])
    value = f"{hour:02d}:{minute:02d}"
    await repo.set_daily_score_reminder(user.telegram_id, value)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await cb.answer("Сохранено")
    await safe_edit(cb.message, "Сохранено", settings_kb(user))


@router.message(SettingsSG.score_manual)
async def score_reminder_manual(
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
        await message.answer("Пример: 21:30", reply_markup=cancel_kb("set:dsr"))
        return
    value = f"{hour:02d}:{minute:02d}"
    await repo.set_daily_score_reminder(user.telegram_id, value)
    user = await repo.get_user(user.telegram_id)
    assert user
    await state.clear()
    await message.answer("Сохранено", reply_markup=settings_kb(user))


@router.callback_query(F.data == "set:contact")
async def contact(cb: CallbackQuery, config: Config, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await cb.answer()
    contact = html.escape(legal_contact(config.owner_contact))
    await safe_edit(
        cb.message,
        f"Владелец сервиса: {contact}\nПо вопросам оплаты и доступа пишите сюда.",
        owner_write_kb(config.owner_contact, NAV_SETTINGS),
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


@router.callback_query(F.data == "set:trk")
async def track_root(cb: CallbackQuery, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await cb.answer()
    await show_track_metrics(cb, user)


@router.callback_query(F.data.startswith("set:trk:"))
async def track_toggle(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    from services.ui_prefs import prefs_of, save_prefs, toggle_tracked

    key = cb.data.split(":")[2]
    prefs = toggle_tracked(prefs_of(user), key)
    user = await save_prefs(repo, user, prefs)
    await cb.answer("Сохранено")
    await show_track_metrics(cb, user)


@router.callback_query(F.data == "set:exp")
async def export_root(cb: CallbackQuery, db_user: User | None) -> None:
    if await require_active(cb, db_user) is None:
        return
    await cb.answer()
    await safe_edit(cb.message, "За какой период выгрузить CSV?", export_period_kb())


@router.callback_query(F.data.startswith("exp:"))
async def export_send(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    from services.export import export_user_csv

    token = cb.data.split(":")[1]
    today = user_today(user.timezone)
    if token == "today":
        start = end = today
    elif token == "7":
        start, end = add_days(today, -6), today
    else:
        start, end = add_days(today, -29), today
    filename, body = await export_user_csv(repo, user, start, end)
    await cb.answer()
    await cb.message.answer_document(text_file(body, filename), caption="Ваши записи")

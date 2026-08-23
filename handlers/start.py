"""Registration and /start."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import TZ_PROMPT, show_main, start_payload
from keyboards.main import back_kb, timezone_kb
from services.billing import process_user
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
) -> tuple[User, bool]:
    existing = await repo.get_user(telegram_id)
    is_new = existing is None
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
    return user, is_new


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, repo: Repo, config: Config, db_user: User | None, is_owner: bool) -> None:
    await state.clear()
    if db_user and db_user.is_active:
        await show_main(message, db_user, config, is_owner, state, repo, attach_reply=True)
        return
    text, markup = start_payload(db_user, config, is_owner)
    if db_user and db_user.is_banned:
        pass
    elif db_user and db_user.is_deleted:
        await state.set_state(RegisterSG.timezone)
    else:
        await state.set_state(RegisterSG.consent)
    await message.answer(text, reply_markup=markup)


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
    db_user, is_new = await _activate(repo, config, user.id, user.username, user.first_name, user.last_name, token)
    await cb.answer()
    if is_new:
        from handlers.common import HOW_TO
        from keyboards.main import how_to_kb, reply_main_kb

        await state.clear()
        await cb.message.answer("Быстрый ввод: Сигарета · Снюс · Сон · Ещё", reply_markup=reply_main_kb())
        await safe_edit(cb.message, HOW_TO, how_to_kb())
        return
    await show_main(cb, db_user, config, is_owner, state, repo, attach_reply=True)


@router.callback_query(F.data == "tz:list", RegisterSG.timezone_custom)
async def tz_list_register(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegisterSG.timezone)
    await cb.answer()
    await safe_edit(cb.message, TZ_PROMPT, timezone_kb())


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
    db_user, is_new = await _activate(repo, config, user.id, user.username, user.first_name, user.last_name, token)
    if is_new:
        from handlers.common import HOW_TO
        from keyboards.main import how_to_kb, reply_main_kb

        await state.clear()
        await message.answer("Быстрый ввод: Сигарета · Снюс · Сон · Ещё", reply_markup=reply_main_kb())
        await message.answer(HOW_TO, reply_markup=how_to_kb())
        return
    await show_main(message, db_user, config, is_owner, state, repo, attach_reply=True)


@router.message(Command("menu"))
@router.message(Command("today"))
async def cmd_menu(
    message: Message,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    from handlers.common import require_active

    user = await require_active(message, db_user)
    if user is None:
        return
    await show_main(message, user, config, is_owner, state, repo, attach_reply=True)


@router.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext, db_user: User | None) -> None:
    from handlers.common import require_active
    from handlers.statistics import DEFAULT_METRICS
    from keyboards.main import stats_period_kb

    user = await require_active(message, db_user)
    if user is None:
        return
    await state.clear()
    await state.update_data(stats_metrics=list(DEFAULT_METRICS))
    await message.answer("📊 Статистика\nСначала выберите период:", reply_markup=stats_period_kb())


@router.callback_query(F.data == "onb:ok")
async def onboarding_ok(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    from handlers.common import require_active

    user = await require_active(cb, db_user)
    if user is None:
        return
    await show_main(cb, user, config, is_owner, state, repo, attach_reply=True)

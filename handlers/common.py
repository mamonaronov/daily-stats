"""Shared handler helpers."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import Config
from database.models import SleepRecord, User
from database.queries import Repo
from keyboards.main import back_kb, calendar_kb, hours_kb, legal_consent_kb, main_menu, timezone_kb, when_kb
from services.users import access_message, can_write, write_block_message
from utils.formatting import balance_runway, money
from utils.telegram import hide_reply_keyboard, safe_edit

TZ_PROMPT = "Выберите часовой пояс. Он нужен для статистики и границ дня."
TZ_RESTORE_PROMPT = (
    "Аккаунт был удалён. Данные сохранены.\n\n"
    "Продолжая, вы подтверждаете Пользовательское соглашение и Политику конфиденциальности "
    "(их можно снова открыть в Настройках).\n\n"
    "Выберите часовой пояс, чтобы восстановить доступ."
)
BANNED_TEXT = "Доступ ограничен. Напишите владельцу сервиса."
BOT_PURPOSE = (
    "Бот для того чтобы отмечать что случилось за день, и потом видеть картину целиком: "
    "сигареты, снюс, сон, кофеин, алкоголь, активность, шаги, вес, оценки дня, свои метрики и метки. "
    "История, статистика и графики собираются сами."
)

LEGAL_PROMPT = (
    "📓 <b>Daily Stats</b> — персональный дневник привычек в Telegram.\n\n"
    f"{BOT_PURPOSE}\n\n"
    "Перед регистрацией прочитайте документы. Нажимая «Принимаю», вы соглашаетесь "
    "с Пользовательским соглашением и даёте согласие на обработку персональных данных "
    "по Политике конфиденциальности.\n\n"
    "Дальше нужно выбрать часовой пояс."
)


HOW_TO = (
    "📓 <b>Для чего этот бот</b>\n\n"
    f"{BOT_PURPOSE}\n\n"
    "Как писать день:\n"
    "• в Настройках галочками отметьте, какие метрики вести\n"
    "• отметьте привычку, когда случилась\n"
    "• вечером закройте сон, если ведёте его\n"
    "• статистика копится сама\n\n"
    "Подробности — кнопка «Гайд» в меню."
)


def menu_text(user: User, config: Config, today_block: str | None = None) -> str:
    write_ok = "доступны" if can_write(user) else "временно недоступны"
    text = (
        f"📓 <b>Дневник</b>\n\n"
        f"Привет, {user.display_name}!\n"
        f"💰 Баланс: {money(user.balance)} · {money(user.daily_price)}/день · {balance_runway(user)}\n"
        f"Новые записи: {write_ok}"
    )
    if today_block:
        text += f"\n\n{today_block}"
    return text + "\n\nВыберите действие:"


def start_payload(
    user: User | None,
    config: Config,
    is_owner: bool,
    sleep: SleepRecord | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Text and keyboard of /start — same payload used for lifecycle pings."""
    if user and user.is_banned:
        return BANNED_TEXT, None
    if user and user.is_active:
        from services.ui_prefs import prefs_of

        return menu_text(user, config), main_menu(
            user, is_owner, sleep, tracked=prefs_of(user).tracked
        )
    if user and user.is_deleted:
        return TZ_RESTORE_PROMPT, timezone_kb()
    return LEGAL_PROMPT, legal_consent_kb()


async def show_main(
    target: CallbackQuery | Message,
    user: User,
    config: Config,
    is_owner: bool,
    state: FSMContext | None = None,
    repo: Repo | None = None,
    *,
    hide_reply: bool = False,
) -> None:
    if state:
        await state.clear()
    sleep = await repo.latest_sleep(user.telegram_id) if repo is not None else None
    today_block = None
    tracked: set[str] = set()
    pinned: list = []
    open_metric_ids: set[int] = set()
    if repo is not None:
        from services.today import today_block as today_text
        from services.ui_prefs import MAX_PINS, prefs_of

        prefs = prefs_of(user)
        tracked = prefs.tracked
        today_block = await today_text(repo, user)
        if "custom" in tracked:
            metrics = await repo.list_metrics(user.telegram_id, enabled_only=True)
            pinned = [item for item in metrics if item.pinned][:MAX_PINS]
            open_metric_ids = {item.metric_id for item in await repo.list_open_metric_values(user.telegram_id)}
    text = menu_text(user, config, today_block)
    markup = main_menu(
        user, is_owner, sleep, tracked=tracked, pinned=pinned, open_metric_ids=open_metric_ids
    )
    if hide_reply:
        source = target.message if isinstance(target, CallbackQuery) else target
        await hide_reply_keyboard(source)
    if isinstance(target, CallbackQuery):
        await target.answer()
        await safe_edit(target.message, text, markup)
        return
    await target.answer(text, reply_markup=markup)


async def require_active(event: CallbackQuery | Message, user: User | None) -> User | None:
    if user is None:
        text = "Сначала нажмите /start для регистрации."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None
    blocked = access_message(user)
    if blocked:
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit(event.message, blocked, back_kb())
        else:
            await event.answer(blocked)
        return None
    return user


async def require_writable(event: CallbackQuery | Message, user: User | None) -> User | None:
    user = await require_active(event, user)
    if user is None:
        return None
    blocked = write_block_message(user)
    if blocked:
        if isinstance(event, CallbackQuery):
            await event.answer()
            from services.ui_prefs import prefs_of

            await safe_edit(event.message, blocked, main_menu(user, False, tracked=prefs_of(user).tracked))
        else:
            await event.answer(blocked)
        return None
    return user


async def ask_when_after_amount(event: CallbackQuery | Message, state: FSMContext) -> None:
    data = await state.get_data()
    prefix = "caft" if data.get("amount_kind") == "caf" else "alct"
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, "Когда это было?", when_kb(prefix))
        return
    await event.answer("Когда это было?", reply_markup=when_kb(prefix))


async def start_time_pick(
    cb: CallbackQuery,
    state: FSMContext,
    purpose: str,
    extra: dict | None = None,
    *,
    skip_date: bool = False,
) -> None:
    from datetime import date

    from states.diary import TimePickSG
    from utils.callbacks import NAV_BACK
    from utils.time import user_today

    extra = dict(extra or {})
    if "time_exit" not in extra:
        if purpose.startswith("edit:"):
            _, kind, raw_id = purpose.split(":", 2)
            extra["time_exit"] = f"hist:{kind}:{raw_id}"
        else:
            extra["time_exit"] = {
                "cig": "when:cig",
                "fool": "when:fool",
                "slp_onset": "slp_onset",
                "slp_bed": "sleep",
                "slp_wake": "when:slw",
                "slp_up": "when:slu",
                "snus_buy": "snus",
                "snus_end": "snus",
                "caf": "when:caft",
                "alc": "when:alct",
                "act": "when:actt",
                "wgt": "when:wgt",
                "cm": "when:cmt",
                "cm_start": "when:cms",
                "cm_end": "when:cme",
                "mk": "when:mkt",
            }.get(purpose, "when:cig")
    user_tz = extra.get("tz")
    today = user_today(user_tz) if user_tz else date.today()
    payload = {"time_purpose": purpose, "picked_date": today.isoformat(), **extra}
    if skip_date:
        payload["time_date_shortcuts"] = True
        await state.set_state(TimePickSG.hour)
        await state.update_data(**payload)
        await cb.answer()
        await safe_edit(
            cb.message,
            f"Дата: {today.isoformat()} (сегодня)\nВыберите час — можно уже прошедший:",
            hours_kb(date_shortcuts=True, back=NAV_BACK),
        )
        return
    await state.set_state(TimePickSG.date)
    await state.update_data(**payload)
    await cb.answer()
    await safe_edit(cb.message, "Выберите дату:", calendar_kb(today.year, today.month, back=NAV_BACK))

"""Owner-only admin panel inside the same bot."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import show_main
from keyboards.main import (
    _btn,
    admin_broadcast_kb,
    admin_period_kb,
    admin_root_kb,
    admin_user_kb,
    admin_vpn_kb,
    cancel_kb,
    skip_comment_kb,
    users_page_kb,
    with_nav,
)
from services import balance as balance_svc
from services.broadcast import (
    AUDIENCE_LABELS,
    album_buffer,
    audience_counts,
    format_broadcast_result,
    normalize_audience,
    send_broadcast,
)
from services.statistics import render_stats
from services.telegram_backup import last_telegram_backup_at, next_backup_caption
from services.vpn_charts import build_vpn_charts, downtime_ticks
from services.vpn_monitor import (
    collect_vpn_log_entries,
    format_vpn_log,
    subscription_label,
    vpn_samples_as_dicts,
)
from states.diary import AdminSG
from utils.callbacks import NAV_ADMIN
from utils.formatting import balance_runway, money, seconds_human
from utils.telegram import png_file, safe_edit, safe_send, text_file
from utils.time import add_days, format_dt, now_utc, parse_iso, range_bounds_utc, to_iso, user_today
from utils.uptime import host_uptime_seconds, uptime_report_lines

router = Router(name="admin")
logger = logging.getLogger(__name__)


async def _owner(event: CallbackQuery | Message, config: Config) -> bool:
    user = event.from_user
    if user is None or user.id != config.owner_id:
        if isinstance(event, CallbackQuery):
            await event.answer("Недостаточно прав", show_alert=True)
        else:
            await event.answer("Недостаточно прав.")
        return False
    return True


def _card(user: User, entries_count: int, last_entry: str | None) -> str:
    last_act = format_dt(parse_iso(user.last_activity_at), user.timezone) if user.last_activity_at else "—"
    last_data = format_dt(parse_iso(last_entry), user.timezone) if last_entry else "—"
    status = user.status
    if user.deleted_at:
        status = f"удалён ({user.deleted_at[:10]})"
    return (
        f"👤 <b>{user.display_name}</b>\n"
        f"ID: <code>{user.telegram_id}</code>\n"
        f"Username: @{user.username or '—'}\n"
        f"Регистрация: {user.registered_at[:10]}\n"
        f"Активность: {last_act}\n"
        f"Пояс: {user.timezone}\n"
        f"Баланс: {money(user.balance)}\n"
        f"Стоимость: {money(user.daily_price)} / день\n"
        f"{balance_runway(user.balance, user.daily_price).capitalize()}\n"
        f"Оплачено до: {user.paid_until_date or '—'}\n"
        f"Статус аккаунта: {status}\n"
        f"Записей: {entries_count}\n"
        f"Последняя запись: {last_data}"
    )


def _service_age_seconds(started_iso: str | None, now: datetime | None = None) -> float | None:
    if not started_iso:
        return None
    try:
        started = parse_iso(started_iso)
    except ValueError:
        return None
    return max(0.0, ((now or now_utc()) - started).total_seconds())


async def _admin_status_lines(repo: Repo, config: Config, now: datetime | None = None) -> list[str]:
    now = now or now_utc()
    started = await repo.service_started_at()
    last_backup = await last_telegram_backup_at(repo.db)
    return [
        f"Возраст сервиса: {seconds_human(_service_age_seconds(started, now))}",
        next_backup_caption(last_backup, config.telegram_backup_interval_hours, now),
    ]


@router.callback_query(F.data == NAV_ADMIN)
async def admin_root(cb: CallbackQuery, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    await state.clear()
    counts = await repo.user_stats_counts()
    extra = await _admin_status_lines(repo, config)
    text = (
        "🛠 <b>Админ-панель</b>\n\n"
        f"Всего пользователей: {counts.get('total', 0)}\n"
        f"Активных: {counts.get('active', 0)}\n"
        f"С закончившимся балансом: {counts.get('unpaid', 0)}\n"
        f"Удалённых: {counts.get('deleted', 0)}\n"
        f"Заблокировали бота: {counts.get('bot_blocked', 0)}\n"
        f"{extra[0]}\n"
        f"{extra[1]}"
    )
    await cb.answer()
    await safe_edit(cb.message, text, admin_root_kb())


@router.callback_query(F.data == "ad:users")
@router.callback_query(F.data.startswith("ad:up:"))
async def admin_users(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    offset = int(cb.data.split(":")[2]) if cb.data.startswith("ad:up:") else 0
    users = await repo.list_users_page(offset, 10)
    total = await repo.users_count()
    b = InlineKeyboardBuilder()
    for user in users:
        flag = " 🗑" if user.deleted_at else ""
        b.row(_btn(f"{user.display_name} · {money(user.balance)}{flag}", f"ad:u:{user.telegram_id}"))
    has_next = offset + 10 < total
    nav = users_page_kb(offset, has_next)
    for row in nav.inline_keyboard:
        b.row(*row)
    await cb.answer()
    await safe_edit(cb.message, f"👥 Пользователи ({total})", b.as_markup())


@router.callback_query(F.data == "ad:search")
async def admin_search(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not await _owner(cb, config):
        return
    await state.set_state(AdminSG.search)
    await cb.answer()
    await safe_edit(cb.message, "Введите Telegram ID, username или имя:", cancel_kb(NAV_ADMIN))


@router.message(AdminSG.search)
async def admin_search_msg(message: Message, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(message, config):
        return
    users = await repo.search_users(message.text or "")
    if not users:
        await message.answer("Никого не нашёл.", reply_markup=admin_root_kb())
        return
    if len(users) == 1:
        await state.clear()
        await _send_card(message, repo, users[0])
        return
    b = InlineKeyboardBuilder()
    for user in users:
        b.row(_btn(f"{user.display_name} ({user.telegram_id})", f"ad:u:{user.telegram_id}"))
    await message.answer("Результаты:", reply_markup=with_nav(b, NAV_ADMIN))


@router.callback_query(F.data == "ad:bc")
async def admin_broadcast_start(cb: CallbackQuery, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    await state.clear()
    counts = audience_counts(await repo.list_broadcast_users())
    await cb.answer()
    await safe_edit(
        cb.message,
        "📢 <b>Рассылка</b>\n\nКому отправить сообщение?",
        admin_broadcast_kb(counts),
    )


@router.callback_query(F.data.startswith("ad:bc:"))
async def admin_broadcast_audience(cb: CallbackQuery, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    raw = cb.data.split(":")[2] if cb.data else ""
    if raw not in AUDIENCE_LABELS:
        await cb.answer("Неизвестная группа", show_alert=True)
        return
    audience = raw
    await state.set_state(AdminSG.broadcast)
    await state.update_data(broadcast_audience=audience)
    counts = audience_counts(await repo.list_broadcast_users())
    n = counts.get(audience, 0)
    await cb.answer()
    await safe_edit(
        cb.message,
        "📢 <b>Рассылка</b>\n\n"
        f"Сообщение уйдёт: {AUDIENCE_LABELS[audience]} ({n}).\n"
        "Вам оно тоже придёт.\n\n"
        "Пришлите сообщение — копия уйдёт как есть: текст, фото, видео, файлы и подписи.",
        cancel_kb("ad:bc"),
    )


@router.message(AdminSG.broadcast)
async def admin_broadcast_msg(
    message: Message,
    state: FSMContext,
    config: Config,
    repo: Repo,
    bot: Bot,
) -> None:
    if not await _owner(message, config):
        return
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Это команда, не рассылка. Пришлите обычное сообщение или отмените.",
            reply_markup=cancel_kb("ad:bc"),
        )
        return
    if message.media_group_id:
        message_ids = await album_buffer.add(message.media_group_id, message.message_id)
        if message_ids is None:
            return
    elif message.message_id is None:
        await message.answer("Не удалось прочитать сообщение.", reply_markup=cancel_kb("ad:bc"))
        return
    else:
        message_ids = [message.message_id]
    data = await state.get_data()
    audience = normalize_audience(data.get("broadcast_audience"))
    await state.clear()
    status = await message.answer("Отправляю…")
    result = await send_broadcast(
        bot,
        repo,
        from_chat_id=message.chat.id,
        message_ids=message_ids,
        audience=audience,
        include_telegram_id=config.owner_id,
    )
    text = format_broadcast_result(result)
    if status is not None:
        await safe_edit(status, text, admin_root_kb())
    else:
        await message.answer(text, reply_markup=admin_root_kb())


async def _send_card(target: CallbackQuery | Message, repo: Repo, user: User) -> None:
    count = await repo.count_user_entries(user.telegram_id)
    last = await repo.last_entry_at(user.telegram_id)
    text = _card(user, count, last)
    markup = admin_user_kb(user.telegram_id)
    if isinstance(target, CallbackQuery):
        await target.answer()
        await safe_edit(target.message, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ad:u:"))
async def admin_user(cb: CallbackQuery, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    await state.clear()
    telegram_id = int(cb.data.split(":")[2])
    user = await repo.get_user(telegram_id)
    if user is None:
        await cb.answer("Пользователь не найден", show_alert=True)
        return
    await _send_card(cb, repo, user)


async def _ask_amount(cb: CallbackQuery, state: FSMContext, config: Config, action: str, telegram_id: int) -> None:
    if not await _owner(cb, config):
        return
    await state.set_state(AdminSG.amount)
    await state.update_data(admin_action=action, target_id=telegram_id)
    await cb.answer()
    await safe_edit(cb.message, "Введите сумму (число):", cancel_kb(f"ad:u:{telegram_id}"))


@router.callback_query(F.data.startswith("ad:cr:"))
async def admin_credit(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    await _ask_amount(cb, state, config, "credit", int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("ad:db:"))
async def admin_debit(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    await _ask_amount(cb, state, config, "debit", int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("ad:st:"))
async def admin_set(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    await _ask_amount(cb, state, config, "set", int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("ad:pr:"))
async def admin_price(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not await _owner(cb, config):
        return
    await state.set_state(AdminSG.price)
    await state.update_data(target_id=int(cb.data.split(":")[2]))
    await cb.answer()
    await safe_edit(cb.message, "Новая стоимость в сутки:", cancel_kb(f"ad:u:{int(cb.data.split(':')[2])}"))


@router.message(AdminSG.amount)
async def admin_amount(message: Message, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(message, config):
        return
    data = await state.get_data()
    try:
        amount = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        await message.answer("Введите число.", reply_markup=cancel_kb(f"ad:u:{data.get('target_id')}"))
        return
    await state.update_data(amount=amount)
    await state.set_state(AdminSG.comment)
    await message.answer(
        "Комментарий к операции? Можно пропустить.",
        reply_markup=skip_comment_kb(f"ad:u:{data['target_id']}"),
    )


async def _apply_admin_op(
    event: CallbackQuery | Message,
    state: FSMContext,
    config: Config,
    repo: Repo,
    comment: str | None,
) -> None:
    data = await state.get_data()
    target = int(data["target_id"])
    amount = float(data["amount"])
    action = data["admin_action"]
    try:
        if action == "credit":
            await balance_svc.credit(repo, target, amount, comment=comment, performed_by=config.owner_id)
        elif action == "debit":
            await balance_svc.debit(repo, target, amount, comment=comment, performed_by=config.owner_id)
        elif action == "set":
            await balance_svc.set_balance(repo, target, amount, comment=comment, performed_by=config.owner_id)
    except Exception as exc:
        text = f"Ошибка: {exc}"
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return
    await state.clear()
    user = await repo.get_user(target)
    if isinstance(event, Message):
        await event.answer("Операция записана в журнал.")
    if user:
        await _send_card(event, repo, user)
    elif isinstance(event, CallbackQuery):
        await event.answer("Операция записана")
        await safe_edit(event.message, "Операция записана в журнал.", admin_root_kb())


@router.callback_query(F.data == "wb:skip", AdminSG.comment)
async def admin_skip_comment(cb: CallbackQuery, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    await _apply_admin_op(cb, state, config, repo, None)


@router.message(AdminSG.comment)
async def admin_comment(message: Message, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(message, config):
        return
    await _apply_admin_op(message, state, config, repo, (message.text or "").strip() or None)


@router.message(AdminSG.price)
async def admin_price_save(message: Message, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(message, config):
        return
    data = await state.get_data()
    try:
        price = float((message.text or "").replace(",", ".").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите неотрицательное число.", reply_markup=cancel_kb(f"ad:u:{data.get('target_id')}"))
        return
    target = int(data["target_id"])
    old = await repo.get_user(target)
    await repo.set_daily_price(target, price)
    await repo.apply_balance_change(
        target,
        "adjustment",
        delta=0,
        comment=f"Изменение стоимости {old.daily_price if old else '?'} → {price}",
        performed_by=config.owner_id,
    )
    await state.clear()
    user = await repo.get_user(target)
    await message.answer("Стоимость обновлена. История прошлых списаний не изменена.")
    if user:
        await _send_card(message, repo, user)


@router.callback_query(F.data.startswith("ad:op:"))
@router.callback_query(F.data == "ad:ops")
async def admin_ops(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    telegram_id = None
    if cb.data.startswith("ad:op:"):
        telegram_id = int(cb.data.split(":")[2])
    ops = await repo.list_operations(telegram_id, limit=15)
    if not ops:
        text = "Операций нет."
    else:
        lines = ["📋 <b>История операций</b>"]
        for op in ops:
            who = "система" if not op.performed_by else str(op.performed_by)
            lines.append(
                f"{op.created_at[5:16]} · {op.operation_type} · {money(op.amount)}\n"
                f"{money(op.balance_before)} → {money(op.balance_after)} · {op.comment or '—'} · {who}"
            )
        text = "\n\n".join(lines)
    await cb.answer()
    await safe_edit(cb.message, text[:4000], admin_root_kb() if telegram_id is None else admin_user_kb(telegram_id))


@router.callback_query(F.data.startswith("ad:us:"))
async def admin_user_stats(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    telegram_id = int(cb.data.split(":")[2])
    user = await repo.get_user(telegram_id)
    if user is None:
        await cb.answer("Нет пользователя", show_alert=True)
        return
    end = user_today(user.timezone)
    start = add_days(end, -6)
    text = await render_stats(
        repo,
        user,
        start,
        end,
        ["cigarettes", "fooling", "snus", "sleep", "mood", "wellbeing", "caffeine", "alcohol", "activity"],
    )
    await cb.answer()
    await safe_edit(cb.message, text[:4000], admin_user_kb(telegram_id))


@router.callback_query(F.data.startswith("ad:bn:"))
async def admin_ban(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    telegram_id = int(cb.data.split(":")[2])
    if telegram_id == config.owner_id:
        await cb.answer("Нельзя заблокировать владельца", show_alert=True)
        return
    await repo.set_status(telegram_id, "banned")
    user = await repo.get_user(telegram_id)
    await cb.answer("Заблокирован")
    if user:
        await _send_card(cb, repo, user)


@router.callback_query(F.data.startswith("ad:un:"))
async def admin_unban(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    telegram_id = int(cb.data.split(":")[2])
    await repo.set_status(telegram_id, "active")
    user = await repo.get_user(telegram_id)
    await cb.answer("Разблокирован")
    if user:
        await _send_card(cb, repo, user)


@router.callback_query(F.data == "ad:stats")
async def admin_stats_root(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    await cb.answer()
    await safe_edit(cb.message, "Период статистики сервиса:", admin_period_kb())


@router.callback_query(F.data.startswith("ads:"))
async def admin_stats(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    token = cb.data.split(":")[1]
    from datetime import date, datetime, timezone

    now = now_utc()
    today = now.date()
    if token == "today":
        start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    else:
        days = int(token)
        start = now - timedelta(days=days)
    end = now
    counts = await repo.user_stats_counts()
    entries_today = await repo.count_entries_between(
        to_iso(datetime(today.year, today.month, today.day, tzinfo=timezone.utc)),
        to_iso(now),
    )
    entries_7 = await repo.count_entries_between(to_iso(now - timedelta(days=7)), to_iso(now))
    entries_30 = await repo.count_entries_between(to_iso(now - timedelta(days=30)), to_iso(now))
    money_tot = await repo.finance_totals(to_iso(start), to_iso(end))
    text = (
        "📊 <b>Статистика сервиса</b>\n\n"
        f"Пользователей: {counts.get('total', 0)}\n"
        f"Активных: {counts.get('active', 0)}\n"
        f"Нулевой/недостаточный баланс: {counts.get('unpaid', 0)}\n"
        f"Удалённых: {counts.get('deleted', 0)}\n"
        f"Записей сегодня: {entries_today}\n"
        f"За 7 дней: {entries_7}\n"
        f"За 30 дней: {entries_30}\n"
        f"Пополнения за период: {money(money_tot['credits'])}\n"
        f"Списания за период: {money(money_tot['debits'])}\n"
        f"Доход (пополнения): {money(money_tot['income'])}"
    )
    await cb.answer()
    await safe_edit(cb.message, text, admin_period_kb())


@router.callback_query(F.data == "ad:cfg")
async def admin_cfg(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    if config.telegram_backup_interval_hours > 0:
        tg_backup = f"каждые {config.telegram_backup_interval_hours} ч, без звука"
    else:
        tg_backup = "выкл"
    extra = await _admin_status_lines(repo, config)
    text = (
        "⚙️ <b>Настройки сервиса</b>\n\n"
        f"Пояс по умолчанию: {config.default_timezone}\n"
        f"Цена по умолчанию: {money(config.default_daily_price)}/день\n"
        f"Backup на диск каждые {config.backup_interval_hours} ч, хранить {config.backup_keep}\n"
        f"Бэкап в Telegram: {tg_backup}\n"
        f"{extra[1]}\n"
        f"{extra[0]}\n"
        f"Напоминание: за {config.reminder_hours_before_sleep} ч до сна\n"
        f"Fallback: {config.reminder_fallback_time}\n"
        f"Контакт: {config.owner_contact}\n"
        f"Версия БД: {config.required_db_version}\n"
        f"VPN-монитор: {'вкл' if config.vpn_monitor_enabled else 'выкл'}"
        f" / {config.vpn_monitor_interval_seconds} с\n"
        f"Mihomo API: {config.mihomo_api_url} · группа {config.mihomo_proxy_group}"
    )
    await cb.answer()
    await safe_edit(cb.message, text, admin_root_kb())


@router.callback_query(F.data == "ad:bal")
async def admin_balances(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    users = await repo.list_users_page(0, 15)
    lines = ["💰 <b>Балансы</b>"]
    for user in users:
        lines.append(f"{user.display_name}: {money(user.balance)} · {money(user.daily_price)}/д")
    b = InlineKeyboardBuilder()
    for user in users:
        b.row(_btn(user.display_name, f"ad:u:{user.telegram_id}"))
    await cb.answer()
    await safe_edit(cb.message, "\n".join(lines), with_nav(b, NAV_ADMIN))


VPN_PERIODS: dict[str, tuple[timedelta | None, str]] = {
    "5m": (timedelta(minutes=5), "последние 5 минут"),
    "1h": (timedelta(hours=1), "последний час"),
    "24h": (timedelta(hours=24), "последние сутки"),
    "7d": (timedelta(days=7), "последнюю неделю"),
    "30d": (timedelta(days=30), "последний месяц"),
    "all": (None, "всё время"),
}

_VPN_LOG_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _ms(value) -> str:
    if value is None:
        return "—"
    return str(int(round(value)))


def _pct_line(label: str, value, count: int) -> str:
    return f"{label}: {_ms(value)} мс ({count} зам.)"


def _bucket_line(label: str, count: int, total: int, interval: int) -> str:
    pct = f"{(count / total * 100):.1f}%".replace(".", ",") if total else "—"
    return f"{label}: {seconds_human(count * interval)} ({pct})"


_VPN_BUCKET_KEYS = (
    ("0–100 мс", "bucket_0_100"),
    ("100–500 мс", "bucket_100_500"),
    ("500–1000 мс", "bucket_500_1000"),
    ("&gt; 1000 мс", "bucket_1000"),
    ("Нет пинга/соединения", "no_ping"),
    ("сервис не запущен", "service_down"),
    ("сервер выключен", "server_off"),
)


def _vpn_bucket_lines(summary: dict, interval: int) -> list[str]:
    rows = [(label, int(summary.get(key, 0))) for label, key in _VPN_BUCKET_KEYS]
    total = sum(count for _, count in rows)
    lines = [f"Время в диапазонах (тик {interval} с):"]
    lines.extend(_bucket_line(label, count, total, interval) for label, count in rows)
    return lines


def _vpn_top_item_lines(row: dict, title_html: str) -> list[str]:
    return [
        f"• {title_html}",
        f"\t{int(row['samples'])} раз, avg {_ms(row['avg_ms'])} мс,",
        f"\tp95 {_ms(row['p95_ms'])} мс ({int(row['p95_count'])} зам.)",
    ]


def _append_vpn_top(lines: list[str], heading: str, rows: list[dict], title_html) -> None:
    if not rows:
        return
    lines.append("")
    lines.append(heading)
    for i, row in enumerate(rows):
        if i:
            lines.append("")
        lines.extend(_vpn_top_item_lines(row, title_html(row)))


async def _vpn_window(
    repo: Repo, period_key: str, now: datetime | None = None
) -> tuple[str, datetime, datetime, str]:
    key, delta, title = _vpn_period(period_key)
    end = now or now_utc()
    if delta is None:
        earliest = await repo.earliest_vpn_measured_at()
        start = parse_iso(earliest) if earliest else end
    else:
        start = end - delta
    return key, start, end, title


async def _vpn_report(repo: Repo, config: Config, period_key: str, *, now=None, top: str = "n") -> str:
    _key, start, end, title = await _vpn_window(repo, period_key, now)
    start_iso, end_iso = to_iso(start), to_iso(end)
    summary = await repo.vpn_latency_summary(start_iso, end_iso)
    interval = max(1, config.vpn_monitor_interval_seconds)
    heartbeats = await repo.list_vpn_heartbeats(start_iso, end_iso)
    service_down, server_off = downtime_ticks(
        heartbeats,
        window_start=start,
        window_end=end,
        interval_seconds=interval,
        now_host_uptime_s=host_uptime_seconds(),
    )
    summary["service_down"] = service_down
    summary["server_off"] = server_off
    extra = await _admin_status_lines(repo, config, end)

    lines = ["🛡 <b>VPN / задержка бота</b>", ""]
    lines.extend(uptime_report_lines())
    lines.extend(extra)

    total = summary["total"]
    fail = summary["fail_count"]
    fail_pct = f"{(fail / total * 100):.1f}%".replace(".", ",") if total else "—"
    lines.extend(
        [
            "",
            f"<b>За {title}</b>",
            f"Замеров: {total} · ошибок: {fail} ({fail_pct})",
            f"Средняя: {_ms(summary['avg_ms'])} мс",
            f"Минимум: {_ms(summary['min_ms'])} мс",
            f"Максимум: {_ms(summary['max_ms'])} мс",
            _pct_line("p95", summary["p95_ms"], summary["p95_count"]),
            _pct_line("p99", summary["p99_ms"], summary["p99_count"]),
            _pct_line("p99.9", summary["p99_9_ms"], summary["p99_9_count"]),
            "",
            *_vpn_bucket_lines(summary, interval),
        ]
    )
    if top == "s":
        top_subs = await repo.vpn_top_subscriptions(start_iso, end_iso, limit=5)
        _append_vpn_top(
            lines,
            "Топ подписок:",
            top_subs,
            lambda row: (
                f"{html.escape(subscription_label(row['subscription']))} "
                f"(<code>{html.escape(row['subscription'] or '—')}</code>)"
            ),
        )
    else:
        top_nodes = await repo.vpn_top_nodes(start_iso, end_iso, limit=5)
        _append_vpn_top(
            lines,
            "Топ нод:",
            top_nodes,
            lambda row: f"<code>{html.escape(row['node_name'] or '—')}</code>",
        )
    if not config.vpn_monitor_enabled:
        lines.append("")
        lines.append("Монитор выключен (VPN_MONITOR_ENABLED=0).")
    return "\n".join(lines)


def _vpn_period(period_key: str | None) -> tuple[str, timedelta | None, str]:
    key = period_key if period_key in VPN_PERIODS else "24h"
    delta, title = VPN_PERIODS[key]
    return key, delta, title


def _parse_vpn_view(data: str | None) -> tuple[str, str]:
    period, top = "24h", "n"
    if data and data.startswith("adv:"):
        parts = data.split(":")
        if len(parts) >= 2:
            period = parts[1] if parts[1] in VPN_PERIODS else "24h"
        if len(parts) >= 3 and parts[2] in {"n", "s"}:
            top = parts[2]
    return period, top


@router.callback_query(F.data == "ad:vpn")
@router.callback_query(F.data.startswith("adv:"))
async def admin_vpn(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    period, top = _parse_vpn_view(cb.data)
    text = await _vpn_report(repo, config, period, top=top)
    await cb.answer()
    await safe_edit(cb.message, text, admin_vpn_kb(period, top))


@router.callback_query(F.data.startswith("advc:"))
async def admin_vpn_charts(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    _period, start, end, title = await _vpn_window(
        repo, cb.data.split(":", 1)[1] if cb.data else "24h"
    )
    await cb.answer("Строю графики")
    if cb.message is None:
        return
    try:
        charts = await build_vpn_charts(repo, to_iso(start), to_iso(end), title)
    except Exception:
        logger.exception("VPN charts failed")
        await safe_send(cb.message.answer, "Не удалось построить графики.")
        return
    if not charts:
        await safe_send(cb.message.answer, "Нет данных для графиков.")
        return
    for caption, png in charts:
        filename = "vpn-timeline.png" if caption.startswith("Пинг по времени") else "vpn-distribution.png"
        await safe_send(
            cb.message.answer_document,
            png_file(png, filename),
            caption=caption[:1024],
        )


@router.callback_query(F.data.startswith("advl:"))
@router.callback_query(F.data == "ad:vpnlog")
async def admin_vpn_log(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    raw = cb.data.split(":", 1)[1] if cb.data and cb.data.startswith("advl:") else "24h"
    period, start, end, title = await _vpn_window(repo, raw)
    if period == "all" and start == end:
        start = _VPN_LOG_EPOCH
    start_iso, end_iso = to_iso(start), to_iso(end)
    db_samples = await repo.list_vpn_samples(start_iso, end_iso)
    payload = vpn_samples_as_dicts(db_samples)
    if not payload:
        payload = collect_vpn_log_entries(config.vpn_log_dir, start_iso, end_iso)
    if not payload:
        await cb.answer(f"За {title} логов нет", show_alert=True)
        return
    owner = await repo.get_user(config.owner_id)
    tz_name = owner.timezone if owner and owner.timezone else config.default_timezone
    text = format_vpn_log(payload, start_iso, end_iso, tz_name=tz_name)
    filename = f"vpn-{period}-{end.strftime('%Y%m%d-%H%M')}.txt"
    await cb.answer("Отправляю файл")
    if cb.message is None:
        return
    sent = await safe_send(
        cb.message.answer_document,
        text_file(text, filename),
        caption=f"VPN-логи за {title} · {len(payload)} записей",
    )
    if sent is None:
        await safe_edit(cb.message, "Не удалось отправить файл логов.", admin_vpn_kb(period))

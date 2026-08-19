"""Owner-only admin panel inside the same bot."""

from __future__ import annotations

import html
from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import show_main
from keyboards.main import (
    _btn,
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
from services.statistics import render_stats
from states.diary import AdminSG
from utils.callbacks import NAV_ADMIN
from utils.formatting import balance_runway, money, seconds_human
from utils.telegram import safe_edit
from utils.time import add_days, format_dt, now_utc, parse_iso, range_bounds_utc, to_iso, user_today

router = Router(name="admin")


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


@router.callback_query(F.data == NAV_ADMIN)
async def admin_root(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    counts = await repo.user_stats_counts()
    text = (
        "👑 <b>Админ-панель</b>\n\n"
        f"Всего пользователей: {counts.get('total', 0)}\n"
        f"Активных: {counts.get('active', 0)}\n"
        f"С закончившимся балансом: {counts.get('unpaid', 0)}\n"
        f"Удалённых: {counts.get('deleted', 0)}\n"
        f"Заблокировали бота: {counts.get('bot_blocked', 0)}"
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
    await safe_edit(cb.message, "Введите Telegram ID, username или имя:", cancel_kb())


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
async def admin_user(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
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
    await safe_edit(cb.message, "Введите сумму (число):", cancel_kb())


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
    await safe_edit(cb.message, "Новая стоимость в сутки:", cancel_kb())


@router.message(AdminSG.amount)
async def admin_amount(message: Message, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(message, config):
        return
    try:
        amount = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        await message.answer("Введите число.", reply_markup=cancel_kb())
        return
    await state.update_data(amount=amount)
    await state.set_state(AdminSG.comment)
    await message.answer("Комментарий к операции? Можно пропустить.", reply_markup=skip_comment_kb())


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
    try:
        price = float((message.text or "").replace(",", ".").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите неотрицательное число.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
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
        ["cigarettes", "snus", "sleep", "mood", "wellbeing", "caffeine", "alcohol", "activity"],
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
async def admin_cfg(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    text = (
        "⚙️ <b>Настройки сервиса</b>\n\n"
        f"Пояс по умолчанию: {config.default_timezone}\n"
        f"Цена по умолчанию: {money(config.default_daily_price)}/день\n"
        f"Backup каждые {config.backup_interval_hours} ч, хранить {config.backup_keep}\n"
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


VPN_PERIODS = {
    "1h": (timedelta(hours=1), "последний час"),
    "24h": (timedelta(hours=24), "последние сутки"),
    "7d": (timedelta(days=7), "последнюю неделю"),
    "30d": (timedelta(days=30), "последний месяц"),
}


def _ms(value) -> str:
    if value is None:
        return "—"
    return str(int(round(value)))


def _pct_line(label: str, value, count: int) -> str:
    return f"{label}: {_ms(value)} мс ({count} зам.)"


def _bucket_line(label: str, count: int, measured: int, interval: int) -> str:
    pct = f"{(count / measured * 100):.1f}%".replace(".", ",") if measured else "—"
    return f"{label}: {seconds_human(count * interval)} ({pct})"


async def _vpn_report(repo: Repo, config: Config, period_key: str) -> str:
    from services.vpn_monitor import fetch_auto_now, parse_node, subscription_label

    delta, title = VPN_PERIODS.get(period_key, VPN_PERIODS["24h"])
    end = now_utc()
    start = end - delta
    latest = await repo.latest_vpn_sample()
    summary = await repo.vpn_latency_summary(to_iso(start), to_iso(end))
    top = await repo.vpn_top_nodes(to_iso(start), to_iso(end), limit=5)
    live_now, live_err = await fetch_auto_now(config)
    live_node, live_sub = parse_node(live_now)
    interval = max(1, config.vpn_monitor_interval_seconds)
    measured = summary["measured"]

    lines = ["🛡 <b>VPN / задержка бота</b>", ""]
    if live_node:
        lines.append(f"Сейчас AUTO: <code>{html.escape(live_node)}</code>")
        lines.append(f"Подписка: {html.escape(subscription_label(live_sub))} ({html.escape(live_sub or '—')})")
    elif live_err:
        lines.append(f"Mihomo сейчас: {html.escape(live_err)}")
    else:
        lines.append("Mihomo сейчас: нет данных")

    if latest:
        latest_ms = _ms(latest.latency_ms)
        status = "ok" if latest.ok else "ошибка"
        lines.append(
            f"Последний замер: {latest_ms} мс · {status} · {html.escape(latest.measured_at[11:19])} UTC"
        )
        if latest.error:
            lines.append(f"Ошибка замера: {html.escape(latest.error)}")
    else:
        lines.append("Замеров ещё нет — первый тик в течение ~10 с.")

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
            f"p95: {_ms(summary['p95_ms'])} мс",
            _pct_line("p99", summary["p99_ms"], summary["p99_count"]),
            _pct_line("p99.9", summary["p99_9_ms"], summary["p99_9_count"]),
            "",
            f"Время в диапазонах (тик {interval} с):",
            _bucket_line("&lt; 100 мс", summary["lt_100"], measured, interval),
            _bucket_line("&gt; 100 мс", summary["ge_100"], measured, interval),
            _bucket_line("&gt; 500 мс", summary["ge_500"], measured, interval),
            _bucket_line("&gt; 1 с", summary["ge_1000"], measured, interval),
        ]
    )
    if top:
        lines.append("")
        lines.append("Топ нод:")
        for i, row in enumerate(top):
            if i:
                lines.append("")
            name = html.escape(row["node_name"] or "—")
            sub = html.escape(subscription_label(row["subscription"]))
            avg = _ms(row["avg_ms"])
            min_ms = _ms(row["min_ms"])
            max_ms = _ms(row["max_ms"])
            fails = int(row["fail_count"] or 0)
            fail_bit = f", ошибок {fails}" if fails else ""
            lines.append(
                f"• <code>{name}</code> — {int(row['samples'])} раз, "
                f"avg {avg} мс, min {min_ms} / max {max_ms} мс ({sub}{fail_bit})"
            )
            lines.append(
                f"  p99 {_ms(row['p99_ms'])} мс ({int(row['p99_count'])} зам.), "
                f"p99.9 {_ms(row['p99_9_ms'])} мс ({int(row['p99_9_count'])} зам.)"
            )
    if not config.vpn_monitor_enabled:
        lines.append("")
        lines.append("Монитор выключен (VPN_MONITOR_ENABLED=0).")
    return "\n".join(lines)


@router.callback_query(F.data == "ad:vpn")
@router.callback_query(F.data.startswith("adv:"))
async def admin_vpn(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    period = cb.data.split(":", 1)[1] if cb.data.startswith("adv:") else "24h"
    if period not in VPN_PERIODS:
        period = "24h"
    text = await _vpn_report(repo, config, period)
    await cb.answer()
    await safe_edit(cb.message, text, admin_vpn_kb(period))

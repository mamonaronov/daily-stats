"""Owner confirmation for a host-side git update."""

from __future__ import annotations

import html
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import Config
from handlers.admin import _owner
from keyboards.main import admin_updates_kb
from utils.app_version import app_build_identity
from utils.callbacks import ADMIN_DEPLOY_NO, ADMIN_DEPLOY_OK, ADMIN_UPDATES
from utils.deploy_offer import (
    APPROVE,
    SKIP,
    DeployDecision,
    DeployOffer,
    read_decision,
    read_offer,
    write_decision,
)
from utils.telegram import safe_edit

router = Router(name="admin_deploy")
logger = logging.getLogger(__name__)


def _data_dir(config: Config) -> Path:
    return Path(config.db_path).parent


def _commit_line(label: str, short: str, title: str) -> str:
    return f"{label}: <code>{html.escape(short)}</code> — {html.escape(title)}"


def format_deploy_accepted(offer: DeployOffer) -> str:
    return (
        "✅ <b>Обновление принято</b>\n"
        "Выкатываю новую версию. Сначала дождусь простоя бота.\n\n"
        f"{_commit_line('Сейчас', offer.was_short, offer.was_title)}\n"
        f"{_commit_line('Новая', offer.new_short, offer.new_title)}"
    )


def format_deploy_skipped(offer: DeployOffer) -> str:
    return (
        "⏭ <b>Обновление отложено</b>\n"
        "Напомню, когда в main появится другой коммит.\n\n"
        f"{_commit_line('Сейчас', offer.was_short, offer.was_title)}\n"
        f"{_commit_line('Пропущена', offer.new_short, offer.new_title)}"
    )


def updates_actions(
    offer: DeployOffer | None,
    decision: DeployDecision | None,
) -> tuple[bool, bool]:
    if offer is None:
        return False, False
    if decision is not None and decision.sha == offer.new:
        if decision.action == APPROVE:
            return False, False
        if decision.action == SKIP:
            return True, False
    return True, True


def format_updates_panel(
    *,
    running_short: str,
    running_title: str,
    offer: DeployOffer | None = None,
    decision: DeployDecision | None = None,
) -> str:
    lines = [
        "🔄 <b>Обновления</b>",
        "",
        f"Работает: <code>{html.escape(running_short)}</code> — {html.escape(running_title)}",
    ]
    if offer is None:
        lines.append("")
        lines.append("Актуально. Хост сам предложит, когда в main появится другой коммит.")
        return "\n".join(lines)
    lines.append("")
    lines.append(_commit_line("Сейчас", offer.was_short, offer.was_title))
    lines.append(_commit_line("Новая", offer.new_short, offer.new_title))
    lines.append("")
    if decision is not None and decision.sha == offer.new:
        if decision.action == APPROVE:
            lines.append("Принято. Выкатываю новую версию. Сначала дождусь простоя бота.")
        else:
            lines.append("Отложено. Напомню, когда в main появится другой коммит.")
            lines.append("Можно выкатить эту версию сейчас.")
    else:
        lines.append("Доступно обновление. Выкатить эту версию?")
    return "\n".join(lines)


def _updates_view(config: Config) -> tuple[str, object]:
    data_dir = _data_dir(config)
    offer = read_offer(data_dir)
    decision = read_decision(data_dir)
    running_short, running_title = app_build_identity()
    can_approve, can_skip = updates_actions(offer, decision)
    return (
        format_updates_panel(
            running_short=running_short,
            running_title=running_title,
            offer=offer,
            decision=decision,
        ),
        admin_updates_kb(can_approve=can_approve, can_skip=can_skip),
    )


async def _show_updates(cb: CallbackQuery, config: Config) -> None:
    text, markup = _updates_view(config)
    await safe_edit(cb.message, text, markup)


@router.callback_query(F.data == ADMIN_UPDATES)
async def updates_root(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    await cb.answer()
    await _show_updates(cb, config)


@router.callback_query(F.data == ADMIN_DEPLOY_OK)
async def deploy_approve(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    data_dir = _data_dir(config)
    offer = read_offer(data_dir)
    if offer is None:
        await cb.answer("Предложение уже неактуально.", show_alert=True)
        await _show_updates(cb, config)
        return
    existing = read_decision(data_dir)
    if existing is not None and existing.action == APPROVE and existing.sha == offer.new:
        await cb.answer("Уже принято")
        await _show_updates(cb, config)
        return
    write_decision(data_dir, APPROVE, offer.new)
    logger.info("Owner approved deploy %s", offer.new)
    await cb.answer("Выкатываю")
    await _show_updates(cb, config)


@router.callback_query(F.data == ADMIN_DEPLOY_NO)
async def deploy_skip(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    data_dir = _data_dir(config)
    offer = read_offer(data_dir)
    if offer is None:
        await cb.answer("Предложение уже неактуально.", show_alert=True)
        await _show_updates(cb, config)
        return
    existing = read_decision(data_dir)
    if existing is not None and existing.action == APPROVE and existing.sha == offer.new:
        await cb.answer("Обновление уже принято.", show_alert=True)
        await _show_updates(cb, config)
        return
    write_decision(data_dir, SKIP, offer.new)
    logger.info("Owner skipped deploy %s", offer.new)
    await cb.answer("Отложено")
    await _show_updates(cb, config)

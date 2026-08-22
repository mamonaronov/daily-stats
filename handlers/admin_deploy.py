"""Owner confirmation for a host-side git update."""

from __future__ import annotations

import html
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import Config
from handlers.admin import _owner
from utils.callbacks import ADMIN_DEPLOY_NO, ADMIN_DEPLOY_OK
from utils.deploy_offer import (
    APPROVE,
    SKIP,
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


@router.callback_query(F.data == ADMIN_DEPLOY_OK)
async def deploy_approve(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    data_dir = _data_dir(config)
    offer = read_offer(data_dir)
    if offer is None:
        await cb.answer("Предложение уже неактуально.", show_alert=True)
        return
    existing = read_decision(data_dir)
    if existing is not None and existing.action == APPROVE and existing.sha == offer.new:
        await cb.answer("Уже принято")
        await safe_edit(cb.message, format_deploy_accepted(offer))
        return
    write_decision(data_dir, APPROVE, offer.new)
    logger.info("Owner approved deploy %s", offer.new)
    await cb.answer("Выкатываю")
    await safe_edit(cb.message, format_deploy_accepted(offer))


@router.callback_query(F.data == ADMIN_DEPLOY_NO)
async def deploy_skip(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    data_dir = _data_dir(config)
    offer = read_offer(data_dir)
    if offer is None:
        await cb.answer("Предложение уже неактуально.", show_alert=True)
        return
    existing = read_decision(data_dir)
    if existing is not None and existing.action == APPROVE and existing.sha == offer.new:
        await cb.answer("Обновление уже принято.", show_alert=True)
        return
    write_decision(data_dir, SKIP, offer.new)
    logger.info("Owner skipped deploy %s", offer.new)
    await cb.answer("Отложено")
    await safe_edit(cb.message, format_deploy_skipped(offer))

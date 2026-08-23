"""Privacy policy and terms of service screens."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import Config
from database.models import User
from handlers.common import BANNED_TEXT, LEGAL_PROMPT, TZ_PROMPT, show_main
from keyboards.main import legal_consent_kb, legal_page_kb, timezone_kb
from services.legal import DOC_PRIVACY, DOC_TERMS, DOC_TITLES, document_page, legal_contact
from states.diary import RegisterSG
from utils.telegram import safe_edit

logger = logging.getLogger(__name__)

router = Router(name="legal")

_DOC_BY_TOKEN = {"p": DOC_PRIVACY, "t": DOC_TERMS}
_TOKEN_BY_DOC = {DOC_PRIVACY: "p", DOC_TERMS: "t"}


async def _show_consent(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegisterSG.consent)
    await cb.answer()
    await safe_edit(cb.message, LEGAL_PROMPT, legal_consent_kb())


async def _show_doc(
    cb: CallbackQuery,
    doc: str,
    page: int,
    origin: str,
    config: Config,
    state: FSMContext,
) -> None:
    if origin == "c":
        await state.set_state(RegisterSG.consent)
    contact = legal_contact(config.owner_contact)
    body, index, total = document_page(doc, page, contact)
    title = html.escape(DOC_TITLES[doc])
    header = f"<b>{title}</b>"
    if total > 1:
        header += f" ({index + 1}/{total})"
    await cb.answer()
    await safe_edit(
        cb.message,
        f"{header}\n\n{body}",
        legal_page_kb(_TOKEN_BY_DOC[doc], index, total, origin),
    )


@router.callback_query(F.data == "lg:home")
async def legal_home(
    cb: CallbackQuery,
    state: FSMContext,
    db_user: User | None,
) -> None:
    if db_user and db_user.is_banned:
        await cb.answer()
        await safe_edit(cb.message, BANNED_TEXT)
        return
    await _show_consent(cb, state)


@router.callback_query(F.data == "lg:ok")
async def legal_accept(
    cb: CallbackQuery,
    state: FSMContext,
    db_user: User | None,
    config: Config,
    is_owner: bool,
    repo,
) -> None:
    if db_user and db_user.is_banned:
        await cb.answer()
        await safe_edit(cb.message, BANNED_TEXT)
        return
    if db_user and db_user.is_active:
        await show_main(cb, db_user, config, is_owner, state, repo)
        return
    logger.info("legal_accepted telegram_id=%s", cb.from_user.id if cb.from_user else None)
    await state.set_state(RegisterSG.timezone)
    await cb.answer()
    await safe_edit(cb.message, TZ_PROMPT, timezone_kb())


@router.callback_query(F.data.regexp(r"^lg:[pt]:\d+:[cs]$"))
async def legal_doc(
    cb: CallbackQuery,
    state: FSMContext,
    config: Config,
) -> None:
    assert cb.data is not None
    _, token, page_s, origin = cb.data.split(":")
    doc = _DOC_BY_TOKEN[token]
    await _show_doc(cb, doc, int(page_s), origin, config, state)

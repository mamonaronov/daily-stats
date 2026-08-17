"""Aiogram middlewares: context injection, user sync, error isolation."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from config import Config
from database.queries import Repo
from services.alerts import format_alert, notify_owner

logger = logging.getLogger(__name__)


class ContextMiddleware(BaseMiddleware):
    def __init__(self, repo: Repo, config: Config, scheduler, bot) -> None:
        self.repo = repo
        self.config = config
        self.scheduler = scheduler
        self.bot = bot

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["repo"] = self.repo
        data["config"] = self.config
        data["scheduler"] = self.scheduler
        data["app_bot"] = self.bot
        return await handler(event, data)


def _extract_user(event: TelegramObject):
    from aiogram.types import Update

    if isinstance(event, Update):
        for attr in ("message", "callback_query", "edited_message"):
            obj = getattr(event, attr, None)
            if obj is not None and getattr(obj, "from_user", None) is not None:
                return obj.from_user
        return None
    return getattr(event, "from_user", None)


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user") or _extract_user(event)
        repo: Repo | None = data.get("repo")
        if tg_user is not None and repo is not None:
            db_user = await repo.get_user(tg_user.id)
            if db_user and not db_user.is_deleted:
                try:
                    await repo.touch_user(tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name)
                    if db_user.status == "bot_blocked":
                        db_user = await repo.get_user(tg_user.id)
                except Exception:
                    logger.exception("Failed to touch user %s", tg_user.id)
            data["db_user"] = db_user
            data["is_owner"] = tg_user.id == data["config"].owner_id
        else:
            data["db_user"] = None
            data["is_owner"] = False
        return await handler(event, data)


class ErrorIsolationMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            logger.exception("Handler error")
            bot = data.get("app_bot") or data.get("bot")
            config: Config | None = data.get("config")
            if bot is not None and config is not None:
                await notify_owner(
                    bot,
                    config,
                    format_alert("handler", "Необработанная ошибка хендлера", exc=exc),
                )
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("Произошла ошибка. Попробуйте ещё раз.", show_alert=True)
                except Exception:
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer("Произошла ошибка. Попробуйте ещё раз.")
                except Exception:
                    pass
            return None


class CallbackIdempotencyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.id:
            repo: Repo = data["repo"]
            user = event.from_user
            if user:
                claimed = await repo.claim_callback(event.id, user.id)
                if not claimed:
                    await event.answer()
                    return None
        return await handler(event, data)

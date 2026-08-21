from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from keyboards.main import admin_broadcast_kb, admin_root_kb
from services.broadcast import (
    AlbumBuffer,
    BroadcastResult,
    audience_counts,
    filter_broadcast_audience,
    format_broadcast_result,
    send_broadcast,
)
from utils.callbacks import NAV_ADMIN, NAV_MAIN


class _FakeBot:
    def __init__(self) -> None:
        self.copied: list[tuple] = []
        self.fail: dict[int, BaseException] = {}
        self.copy_messages_error: BaseException | None = None

    async def copy_message(self, chat_id, from_chat_id, message_id, **kwargs):
        exc = self.fail.get(chat_id)
        if exc is not None:
            raise exc
        self.copied.append(("message", chat_id, from_chat_id, message_id))

    async def copy_messages(self, chat_id, from_chat_id, message_ids, **kwargs):
        if self.copy_messages_error is not None:
            raise self.copy_messages_error
        exc = self.fail.get(chat_id)
        if exc is not None:
            raise exc
        self.copied.append(("messages", chat_id, from_chat_id, tuple(message_ids)))


async def _users(repo, *ids: int, price: float = 0):
    for telegram_id in ids:
        await repo.create_user(telegram_id, f"u{telegram_id}", "Name", None, "UTC", price, "23:00")


def test_admin_root_has_broadcast_button():
    pairs = [(btn.text, btn.callback_data) for row in admin_root_kb().inline_keyboard for btn in row]
    assert ("📢 Рассылка", "ad:bc") in pairs
    assert ("📦 Бэкапы", "ad:bk") in pairs
    assert ("🏠 Меню", NAV_MAIN) in pairs


def test_admin_broadcast_kb_groups():
    pairs = [(btn.text, btn.callback_data) for row in admin_broadcast_kb({"all": 3, "paid": 2, "unpaid": 1}).inline_keyboard for btn in row]
    assert ("👥 Все активные (3)", "ad:bc:all") in pairs
    assert ("✅ С доступом (2)", "ad:bc:paid") in pairs
    assert ("💸 Без оплаты (1)", "ad:bc:unpaid") in pairs
    assert ("⬅️ Назад", NAV_ADMIN) in pairs
    assert all(data and len(data.encode()) <= 64 for _, data in pairs)


@pytest.mark.asyncio
async def test_list_broadcast_users_skips_inactive(repo):
    await _users(repo, 1, 2, 3, 4, 5)
    await repo.mark_deleted(await repo.get_user(3), reason="test")
    await repo.set_status(4, "banned")
    await repo.mark_bot_blocked(5)
    users = await repo.list_broadcast_users()
    assert [user.telegram_id for user in users] == [1, 2]


@pytest.mark.asyncio
async def test_filter_broadcast_audience_paid_and_unpaid(repo):
    await _users(repo, 1, price=0)
    await _users(repo, 2, price=10)
    await _users(repo, 3, price=10)
    await repo.apply_balance_change(3, "credit", delta=50, comment="test", performed_by=1)
    users = await repo.list_broadcast_users()
    assert [u.telegram_id for u in filter_broadcast_audience(users, "paid")] == [1, 3]
    assert [u.telegram_id for u in filter_broadcast_audience(users, "unpaid")] == [2]
    assert audience_counts(users) == {"all": 3, "paid": 2, "unpaid": 1}


@pytest.mark.asyncio
async def test_send_broadcast_copies_photo_message_including_owner(repo):
    await _users(repo, 1, 10, 11)
    bot = _FakeBot()
    result = await send_broadcast(
        bot,
        repo,
        from_chat_id=1,
        message_ids=[42],
        include_telegram_id=1,
        delay=0,
    )
    assert result.total == 3
    assert result.sent == 3
    assert result.blocked == 0
    assert result.failed == 0
    assert bot.copied == [
        ("message", 1, 1, 42),
        ("message", 10, 1, 42),
        ("message", 11, 1, 42),
    ]


@pytest.mark.asyncio
async def test_send_broadcast_includes_owner_outside_unpaid_group(repo):
    await _users(repo, 1, price=0)
    await _users(repo, 20, price=10)
    await _users(repo, 21, price=10)
    bot = _FakeBot()
    result = await send_broadcast(
        bot,
        repo,
        from_chat_id=1,
        message_ids=[9],
        audience="unpaid",
        include_telegram_id=1,
        delay=0,
    )
    assert result.audience == "unpaid"
    assert result.sent == 3
    assert [item[1] for item in bot.copied] == [1, 20, 21]


@pytest.mark.asyncio
async def test_send_broadcast_album_uses_copy_messages(repo):
    await _users(repo, 1, 20)
    bot = _FakeBot()
    result = await send_broadcast(
        bot,
        repo,
        from_chat_id=1,
        message_ids=[5, 7, 6],
        include_telegram_id=1,
        delay=0,
    )
    assert result.sent == 2
    assert bot.copied == [
        ("messages", 1, 1, (5, 6, 7)),
        ("messages", 20, 1, (5, 6, 7)),
    ]


@pytest.mark.asyncio
async def test_send_broadcast_album_falls_back_to_single_copies(repo):
    await _users(repo, 1, 21)
    bot = _FakeBot()
    bot.copy_messages_error = TelegramBadRequest(method=SimpleNamespace(), message="can't copy")
    result = await send_broadcast(
        bot,
        repo,
        from_chat_id=1,
        message_ids=[8, 9],
        include_telegram_id=1,
        delay=0,
    )
    assert result.sent == 2
    assert bot.copied == [
        ("message", 1, 1, 8),
        ("message", 1, 1, 9),
        ("message", 21, 1, 8),
        ("message", 21, 1, 9),
    ]


@pytest.mark.asyncio
async def test_send_broadcast_marks_blocked_users(repo):
    await _users(repo, 1, 30, 31)
    bot = _FakeBot()
    bot.fail[30] = TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden: bot was blocked by the user")
    result = await send_broadcast(
        bot,
        repo,
        from_chat_id=1,
        message_ids=[1],
        include_telegram_id=1,
        delay=0,
    )
    assert result.sent == 2
    assert result.blocked == 1
    user = await repo.get_user(30)
    assert user is not None
    assert user.status == "bot_blocked"
    assert bot.copied == [
        ("message", 1, 1, 1),
        ("message", 31, 1, 1),
    ]


@pytest.mark.asyncio
async def test_send_broadcast_retries_flood_wait(repo):
    await _users(repo, 1, 40)

    class FloodBot(_FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def copy_message(self, chat_id, from_chat_id, message_id, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise TelegramRetryAfter(method=SimpleNamespace(), message="retry", retry_after=0)
            return await super().copy_message(chat_id, from_chat_id, message_id, **kwargs)

    bot = FloodBot()
    result = await send_broadcast(
        bot,
        repo,
        from_chat_id=1,
        message_ids=[3],
        include_telegram_id=1,
        delay=0,
    )
    assert result.sent == 2
    assert bot.attempts == 3


@pytest.mark.asyncio
async def test_album_buffer_collects_sorted_unique_ids():
    buffer = AlbumBuffer(wait=0.05)

    async def rest() -> None:
        await asyncio.sleep(0.01)
        assert await buffer.add("g1", 12) is None
        assert await buffer.add("g1", 10) is None

    rest_task = asyncio.create_task(rest())
    ids = await buffer.add("g1", 11)
    await rest_task
    assert ids == [10, 11, 12]


def test_format_broadcast_result():
    text = format_broadcast_result(BroadcastResult(total=3, sent=2, blocked=1, failed=0, audience="paid"))
    assert "Кому: с доступом" in text
    assert "Получателей: 3" in text
    assert "Доставлено: 2" in text
    assert "Заблокировали бота: 1" in text
    assert "Не удалось: 0" in text

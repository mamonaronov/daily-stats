from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from utils.telegram_session import AbandonableAiohttpSession, make_telegram_session


async def test_make_request_abandons_hang_and_raises_network_error(monkeypatch):
    monkeypatch.setattr("utils.telegram_session._REQUEST_SLACK", 0.05)
    monkeypatch.setattr("utils.telegram_session._CLOSE_TIMEOUT", 0.05)
    monkeypatch.setattr("utils.telegram_session._SSL_CLOSE_PAUSE", 0)

    async def hanging(self, bot, method, timeout=None):
        await asyncio.sleep(10)
        return object()

    monkeypatch.setattr(
        "aiogram.client.session.aiohttp.AiohttpSession.make_request",
        hanging,
    )
    session = AbandonableAiohttpSession(timeout=0.05)
    started = time.monotonic()
    with pytest.raises(TelegramNetworkError, match="timeout"):
        await session.make_request(SimpleNamespace(), GetMe())
    assert time.monotonic() - started < 0.4
    assert session._session is None
    await asyncio.sleep(0.2)


async def test_close_abandons_hung_client_session(monkeypatch):
    monkeypatch.setattr("utils.telegram_session._CLOSE_TIMEOUT", 0.05)
    monkeypatch.setattr("utils.telegram_session._SSL_CLOSE_PAUSE", 0)

    class FakeClient:
        closed = False

        async def close(self) -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.4)
                raise

    session = AbandonableAiohttpSession(timeout=1)
    session._session = FakeClient()
    started = time.monotonic()
    await session.close()
    assert time.monotonic() - started < 0.3
    assert session._session is None
    await asyncio.sleep(0.5)


def test_make_telegram_session_with_and_without_proxy():
    plain = make_telegram_session(None, 20)
    assert isinstance(plain, AbandonableAiohttpSession)
    assert plain.proxy is None
    assert plain.timeout == 20

    proxied = make_telegram_session("socks5://127.0.0.1:11808", 8)
    assert isinstance(proxied, AbandonableAiohttpSession)
    assert proxied.proxy == "socks5://127.0.0.1:11808"
    assert proxied.timeout == 8

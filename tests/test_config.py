from __future__ import annotations

from config import load_config


def test_telegram_proxy_optional(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    assert load_config().telegram_proxy_url is None


def test_telegram_proxy_blank_is_none(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "  ")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    assert load_config().telegram_proxy_url is None


def test_telegram_proxy_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "socks5://127.0.0.1:11808")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    assert load_config().telegram_proxy_url == "socks5://127.0.0.1:11808"


def test_bot_session_without_proxy():
    from bot import _bot_session
    from utils.telegram_session import AbandonableAiohttpSession

    session = _bot_session(None)
    assert isinstance(session, AbandonableAiohttpSession)
    assert session.proxy is None
    assert session.timeout == 60.0


def test_bot_session_socks5():
    from bot import _bot_session
    from utils.telegram_session import AbandonableAiohttpSession

    session = _bot_session("socks5://127.0.0.1:11808")
    assert isinstance(session, AbandonableAiohttpSession)
    assert session.proxy is not None
    assert session.proxy == "socks5://127.0.0.1:11808"
    assert session.timeout == 60.0

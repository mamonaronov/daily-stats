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


def test_spam_alert_defaults_and_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    for name in (
        "SPAM_BUTTON_COUNT",
        "SPAM_BUTTON_WINDOW_SECONDS",
        "SPAM_WRITE_COUNT",
        "SPAM_WRITE_WINDOW_SECONDS",
        "SPAM_DAILY_ENTRIES",
        "SPAM_USER_ROWS",
        "SPAM_ALERT_COOLDOWN_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = load_config()
    assert cfg.spam_button_count == 20
    assert cfg.spam_button_window_seconds == 8
    assert cfg.spam_write_count == 15
    assert cfg.spam_daily_entries == 80
    assert cfg.spam_user_rows == 5000
    assert cfg.spam_alert_cooldown_minutes == 30
    monkeypatch.setenv("SPAM_DAILY_ENTRIES", "0")
    monkeypatch.setenv("SPAM_BUTTON_COUNT", "12")
    cfg = load_config()
    assert cfg.spam_daily_entries == 0
    assert cfg.spam_button_count == 12


def test_clicks_db_path_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "database.sqlite3"))
    monkeypatch.setenv("CLICKS_DB_PATH", str(tmp_path / "analytics.sqlite3"))
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    cfg = load_config()
    assert cfg.clicks_db_path == tmp_path / "analytics.sqlite3"


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

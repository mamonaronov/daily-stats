from __future__ import annotations

from types import SimpleNamespace

from database.database import Database
from database.models import User
from database.queries import Repo
from handlers.common import BANNED_TEXT, LEGAL_PROMPT, TZ_RESTORE_PROMPT, menu_text, start_payload
from services.alerts import (
    BOT_STARTED_TEXT,
    BOT_STOPPED_TEXT,
    format_alert,
    format_backup_problems,
    notify_alert,
    notify_owner_lifecycle,
)
from services.telegram_backup import set_telegram_backup_chat
from tests.conftest import make_config
from utils.callbacks import NAV_ADMIN


def _user(**kwargs) -> User:
    fields = dict(
        telegram_id=1,
        username="owner",
        first_name="Owner",
        last_name=None,
        registered_at="t",
        timezone="UTC",
        status="active",
        last_activity_at=None,
        balance=10.0,
        daily_price=1.0,
        paid_until_date=None,
        last_charge_date=None,
        deleted_at=None,
        bot_blocked_at=None,
        created_at="t",
        updated_at="t",
    )
    fields.update(kwargs)
    return User(**fields)


def _button_texts(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


def test_start_payload_active_owner(tmp_path):
    config = make_config(tmp_path)
    user = _user()
    text, markup = start_payload(user, config, True)
    assert text == menu_text(user, config)
    assert any("Админ-панель" in text for text in _button_texts(markup))
    assert any(btn.callback_data == NAV_ADMIN for row in markup.inline_keyboard for btn in row)


def test_start_payload_unregistered(tmp_path):
    text, markup = start_payload(None, make_config(tmp_path), True)
    assert text == LEGAL_PROMPT
    assert any(btn.callback_data == "lg:ok" for row in markup.inline_keyboard for btn in row)
    assert any(btn.callback_data == "lg:p:0:c" for row in markup.inline_keyboard for btn in row)
    assert any(btn.callback_data == "lg:t:0:c" for row in markup.inline_keyboard for btn in row)


def test_start_payload_deleted_and_banned(tmp_path):
    config = make_config(tmp_path)
    deleted_text, deleted_markup = start_payload(_user(status="deleted", deleted_at="t"), config, True)
    assert deleted_text == TZ_RESTORE_PROMPT
    assert deleted_markup is not None
    banned_text, banned_markup = start_payload(_user(status="banned"), config, True)
    assert banned_text == BANNED_TEXT
    assert banned_markup is None


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


async def test_lifecycle_ready_sends_status_then_start_menu(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    repo = Repo(db)
    user = await repo.create_user(1, "owner", "Owner", None, "UTC", 10, "23:00")
    bot = _FakeBot()
    try:
        await notify_owner_lifecycle(bot, repo, config, started=True)
        assert [item["text"] for item in bot.sent] == [BOT_STARTED_TEXT, menu_text(user, config)]
        assert bot.sent[0].get("reply_markup") is None
        assert bot.sent[1]["reply_markup"] is not None
        assert any("Админ-панель" in text for text in _button_texts(bot.sent[1]["reply_markup"]))
        assert all(item["chat_id"] == config.owner_id for item in bot.sent)
    finally:
        await db.close()


async def test_lifecycle_stop_sends_status_without_menu(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    repo = Repo(db)
    await repo.create_user(1, "owner", "Owner", None, "UTC", 10, "23:00")
    bot = _FakeBot()
    try:
        await notify_owner_lifecycle(bot, repo, config, started=False)
        assert [item["text"] for item in bot.sent] == [BOT_STOPPED_TEXT]
        assert bot.sent[0].get("reply_markup") is None
    finally:
        await db.close()


def test_format_alert_includes_readable_reason():
    text = format_alert("backup", "Не удалось отправить бэкап", exc=RuntimeError("file is <too> big"))
    assert "Причина: RuntimeError: file is &lt;too&gt; big" in text
    assert "<too>" not in text


def test_format_backup_problems_lists_each_reason():
    text = format_backup_problems(
        "при выключении",
        [
            ("сделать копию на диск", RuntimeError("no space")),
            ("отправить бэкап в Telegram", TimeoutError("shutdown.telegram_backup timed out after 15.0s")),
        ],
    )
    assert "Не удалось сделать или отправить бэкап при выключении." in text
    assert "Причина (сделать копию на диск): RuntimeError: no space" in text
    assert "Причина (отправить бэкап в Telegram): TimeoutError: shutdown.telegram_backup timed out after 15.0s" in text


async def test_lifecycle_stop_reports_backup_failure_without_menu(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    repo = Repo(db)
    await repo.create_user(1, "owner", "Owner", None, "UTC", 10, "23:00")
    await set_telegram_backup_chat(db, -100123, "Backups")
    bot = _FakeBot()
    try:
        await notify_owner_lifecycle(
            bot,
            repo,
            config,
            started=False,
            backup_problems=[("отправить бэкап в Telegram", RuntimeError("file too big"))],
        )
        assert bot.sent[0]["text"] == BOT_STOPPED_TEXT
        assert bot.sent[0]["chat_id"] == config.owner_id
        assert bot.sent[0].get("reply_markup") is None
        assert bot.sent[1]["chat_id"] == -100123
        assert "Не удалось сделать или отправить бэкап при выключении." in bot.sent[1]["text"]
        assert "Причина (отправить бэкап в Telegram): RuntimeError: file too big" in bot.sent[1]["text"]
        assert len(bot.sent) == 2
    finally:
        await db.close()


async def test_graceful_shutdown_notifies_when_backup_fails(tmp_path, monkeypatch):
    from bot import graceful_shutdown

    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    repo = Repo(db)
    await repo.create_user(1, "owner", "Owner", None, "UTC", 10, "23:00")
    await set_telegram_backup_chat(db, -100123, "Backups")

    async def fail_backup(self, prefix: str = "backup"):
        raise RuntimeError("no space")

    async def fake_send(*_args, **_kwargs):
        return None

    monkeypatch.setattr(Database, "backup", fail_backup)
    monkeypatch.setattr("services.telegram_backup.send_telegram_backup", fake_send)

    class Session:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    bot = _FakeBot()
    bot.session = Session()
    scheduler = SimpleNamespace(running=False)
    try:
        await graceful_shutdown(bot, db, scheduler, config, repo, notify=True)
        assert bot.sent[0]["text"] == BOT_STOPPED_TEXT
        assert bot.sent[0]["chat_id"] == config.owner_id
        assert "Причина (сделать копию на диск): RuntimeError: no space" in bot.sent[1]["text"]
        assert bot.sent[1]["chat_id"] == -100123
        assert bot.session.closed == 1
    finally:
        await db.close()


async def test_graceful_shutdown_reports_telegram_send_reason(tmp_path, monkeypatch):
    from bot import graceful_shutdown

    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    repo = Repo(db)
    await repo.create_user(1, "owner", "Owner", None, "UTC", 10, "23:00")

    async def fail_send(*_args, **_kwargs):
        raise RuntimeError("file is too big")

    monkeypatch.setattr("services.telegram_backup.send_telegram_backup", fail_send)
    await db._set_system("telegram_backup_chat_id", "-100123")
    await db._set_system("telegram_backup_chat_title", "Backups")

    class Session:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    bot = _FakeBot()
    bot.session = Session()
    scheduler = SimpleNamespace(running=False)
    try:
        await graceful_shutdown(bot, db, scheduler, config, repo, notify=True)
        assert bot.sent[0]["text"] == BOT_STOPPED_TEXT
        assert bot.sent[0]["chat_id"] == config.owner_id
        assert bot.sent[1]["chat_id"] == -100123
        assert "Причина (отправить бэкап в Telegram): RuntimeError: file is too big" in bot.sent[1]["text"]
        assert bot.session.closed == 1
    finally:
        await db.close()


async def test_notify_alert_goes_to_backup_group_not_owner(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    await set_telegram_backup_chat(db, -100123, "Backups")
    bot = _FakeBot()
    try:
        await notify_alert(bot, config, format_alert("handler", "Необработанная ошибка хендлера"), db=db)
        assert len(bot.sent) == 1
        assert bot.sent[0]["chat_id"] == -100123
        assert bot.sent[0]["chat_id"] != config.owner_id
        assert "Необработанная ошибка хендлера" in bot.sent[0]["text"]
    finally:
        await db.close()


async def test_notify_alert_skipped_when_group_unbound(tmp_path):
    config = make_config(tmp_path)
    db = Database(config)
    await db.initialize()
    bot = _FakeBot()
    try:
        await notify_alert(bot, config, format_alert("backup", "Не удалось сделать бэкап"), db=db)
        assert bot.sent == []
    finally:
        await db.close()

"""Owner backup management and restore from a Telegram archive."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from database.database import list_sqlite_backups
from database.queries import Repo
from handlers.admin import _owner
from keyboards.main import (
    admin_backups_kb,
    admin_disk_backups_kb,
    admin_restore_confirm_kb,
    cancel_kb,
)
from services.jobs import reschedule_telegram_backup
from services.telegram_backup import (
    TELEGRAM_DOCUMENT_LIMIT,
    TelegramBackupError,
    format_backups_panel,
    last_telegram_backup_at,
    send_telegram_backup,
)
from services.telegram_restore import (
    TELEGRAM_DOWNLOAD_LIMIT,
    RestoreError,
    format_restore_preview,
    inspect_archive,
    inspect_sqlite,
    looks_like_backup_archive,
    stage_pending_restore,
    stage_pending_sqlite,
)
from states.diary import AdminSG
from utils.formatting import bytes_human
from utils.runtime import RuntimeControl
from utils.telegram import safe_edit, safe_send
from utils.time import parse_iso

router = Router(name="admin_restore")
logger = logging.getLogger(__name__)

DISK_PAGE = 5


def _cleanup_incoming(path_str: str | None) -> None:
    if not path_str:
        return
    Path(path_str).unlink(missing_ok=True)


async def _backups_text(repo: Repo, config) -> str:
    last_tg = await last_telegram_backup_at(repo.db)
    files = list_sqlite_backups(config.backup_path)
    last_disk_at = None
    raw = await repo.db.get_system("last_backup_at")
    if raw:
        try:
            last_disk_at = parse_iso(raw)
        except ValueError:
            last_disk_at = None
    return format_backups_panel(
        last_sent=last_tg,
        interval_hours=config.telegram_backup_interval_hours,
        disk_count=len(files),
        latest_disk=files[0].name if files else None,
        last_disk_at=last_disk_at,
    )


async def _show_backups(cb: CallbackQuery, repo: Repo, config: Config) -> None:
    await safe_edit(cb.message, await _backups_text(repo, config), admin_backups_kb())


def _disk_index(data: str | None, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data.split(":")[-1]
    try:
        index = int(raw)
    except ValueError:
        return None
    return index if index >= 0 else None


@router.callback_query(F.data == "ad:bk")
async def backups_root(cb: CallbackQuery, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    data = await state.get_data()
    _cleanup_incoming(data.get("restore_path"))
    await state.clear()
    await cb.answer()
    await _show_backups(cb, repo, config)


@router.callback_query(F.data == "ad:bknow")
async def backups_send_now(
    cb: CallbackQuery,
    config: Config,
    repo: Repo,
    bot: Bot,
    scheduler: AsyncIOScheduler | None,
) -> None:
    if not await _owner(cb, config):
        return
    await cb.answer("Собираю архив")
    try:
        await send_telegram_backup(repo.db, bot, config, silent=False)
    except TelegramBackupError as exc:
        await safe_edit(
            cb.message,
            await _backups_text(repo, config) + f"\n\nНе удалось отправить: {html.escape(str(exc))}",
            admin_backups_kb(),
        )
        return
    except Exception:
        logger.exception("Manual telegram backup failed")
        await safe_edit(
            cb.message,
            await _backups_text(repo, config) + "\n\nНе удалось собрать или отправить бэкап.",
            admin_backups_kb(),
        )
        return
    last = await last_telegram_backup_at(repo.db)
    if scheduler is not None:
        try:
            reschedule_telegram_backup(scheduler, bot, repo.db, config, last)
        except Exception:
            logger.exception("Failed to reschedule telegram backup after manual send")
    await safe_edit(
        cb.message,
        await _backups_text(repo, config) + "\n\nАрхив отправлен в этот чат.",
        admin_backups_kb(),
    )


@router.callback_query(F.data == "ad:rst")
async def restore_start(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not await _owner(cb, config):
        return
    data = await state.get_data()
    _cleanup_incoming(data.get("restore_path"))
    await state.set_state(AdminSG.restore_file)
    await cb.answer()
    await safe_edit(
        cb.message,
        "📦 <b>Восстановление из бэкапа</b>\n\n"
        "Пришлите сюда архив, который бот отправлял в этот чат "
        "(<code>daily-stats-backup_….tar.gz</code>). Можно переслать то сообщение.\n\n"
        "Текущая база сохранится, затем бот перезапустится с данными из файла.\n"
        "Telegram отдаёт боту файлы до 20 МБ. Если архив больше — на сервере:\n"
        "<code>./restore.sh /path/to/backup.tar.gz --start</code>",
        cancel_kb("ad:bk"),
    )


@router.message(AdminSG.restore_file, F.document)
@router.message(AdminSG.restore_confirm, F.document)
async def restore_file(
    message: Message, state: FSMContext, config: Config, bot: Bot
) -> None:
    if not await _owner(message, config):
        return
    doc = message.document
    if doc is None:
        return
    name = doc.file_name or "backup.tar.gz"
    if doc.file_size and doc.file_size > TELEGRAM_DOWNLOAD_LIMIT:
        await message.answer(
            "Файл больше 20 МБ — Telegram не отдаст его боту.\n"
            "Положите архив на сервер и выполните:\n"
            f"<code>./restore.sh {html.escape(name)} --start</code>",
            reply_markup=cancel_kb("ad:bk"),
        )
        return
    if not looks_like_backup_archive(name):
        await message.answer(
            "Нужен архив <code>.tar.gz</code> бэкапа бота.",
            reply_markup=cancel_kb("ad:bk"),
        )
        return

    incoming = config.backup_path / f"incoming-restore-{uuid4().hex}.tar.gz"
    config.backup_path.mkdir(parents=True, exist_ok=True)
    old = await state.get_data()
    _cleanup_incoming(old.get("restore_path"))
    try:
        await bot.download(doc, destination=incoming)
        preview = await inspect_archive(incoming, config.required_db_version)
    except RestoreError as exc:
        incoming.unlink(missing_ok=True)
        await message.answer(
            f"Архив не подошёл: {html.escape(str(exc))}",
            reply_markup=cancel_kb("ad:bk"),
        )
        return
    except Exception:
        incoming.unlink(missing_ok=True)
        logger.exception("Failed to download or inspect restore archive")
        await message.answer(
            "Не удалось скачать или прочитать файл.",
            reply_markup=cancel_kb("ad:bk"),
        )
        return

    await state.update_data(restore_path=str(incoming), restore_name=name)
    if not preview.compatible:
        await state.set_state(AdminSG.restore_file)
        await message.answer(format_restore_preview(preview), reply_markup=cancel_kb("ad:bk"))
        incoming.unlink(missing_ok=True)
        await state.update_data(restore_path=None)
        return

    await state.set_state(AdminSG.restore_confirm)
    await message.answer(format_restore_preview(preview), reply_markup=admin_restore_confirm_kb())


@router.message(AdminSG.restore_file)
@router.message(AdminSG.restore_confirm)
async def restore_need_file(message: Message, config: Config) -> None:
    if not await _owner(message, config):
        return
    await message.answer(
        "Пришлите файл архива <code>.tar.gz</code>, не текст.",
        reply_markup=cancel_kb("ad:bk"),
    )


@router.callback_query(F.data == "ad:rstok", AdminSG.restore_confirm)
async def restore_confirm(
    cb: CallbackQuery,
    state: FSMContext,
    config: Config,
    runtime: RuntimeControl | None,
) -> None:
    if not await _owner(cb, config):
        return
    if runtime is None:
        await cb.answer("Перезапуск недоступен", show_alert=True)
        return
    data = await state.get_data()
    path_str = data.get("restore_path")
    incoming = Path(path_str) if path_str else None
    if incoming is None or not incoming.is_file():
        await state.set_state(AdminSG.restore_file)
        await cb.answer("Файл уже недоступен, пришлите архив ещё раз", show_alert=True)
        await safe_edit(
            cb.message,
            "Файл потерян. Пришлите архив бэкапа снова.",
            cancel_kb("ad:bk"),
        )
        return
    try:
        stage_pending_restore(incoming, config.backup_path, data.get("restore_name"))
    except Exception:
        logger.exception("Failed to stage pending restore")
        await cb.answer("Не удалось сохранить архив", show_alert=True)
        return
    incoming.unlink(missing_ok=True)
    await state.clear()
    await cb.answer("Перезапускаюсь")
    await safe_edit(
        cb.message,
        "✅ Бэкап принят. Перезапускаюсь с этими данными.\n"
        "В Docker контейнер поднимется сам. После старта придёт подтверждение.",
    )
    runtime.request_restart()


def _disk_list_text(files: list[Path], offset: int) -> str:
    if not files:
        return "🗄 <b>Копии на диске</b>\n\nПока нет SQLite-копий в каталоге backup."
    end = min(offset + DISK_PAGE, len(files))
    lines = [
        "🗄 <b>Копии на диске</b>",
        "",
        f"Показаны {offset + 1}–{end} из {len(files)}. Это снимки базы, без .env.",
        "",
    ]
    for index in range(offset, end):
        path = files[index]
        size = bytes_human(path.stat().st_size)
        lines.append(f"{index + 1}. <code>{html.escape(path.name)}</code> · {size}")
    return "\n".join(lines)


@router.callback_query(F.data == "ad:bkl")
@router.callback_query(F.data.startswith("ad:bkl:"))
async def backups_disk_list(cb: CallbackQuery, config: Config) -> None:
    if not await _owner(cb, config):
        return
    offset = 0
    if cb.data and cb.data.startswith("ad:bkl:"):
        parsed = _disk_index(cb.data, "ad:bkl:")
        if parsed is None:
            await cb.answer("Некорректная страница", show_alert=True)
            return
        offset = parsed
    files = list_sqlite_backups(config.backup_path)
    if offset and offset >= len(files):
        offset = 0
    await cb.answer()
    await safe_edit(
        cb.message,
        _disk_list_text(files, offset),
        admin_disk_backups_kb(len(files), offset, DISK_PAGE),
    )


@router.callback_query(F.data.startswith("ad:bks:"))
async def backups_send_disk(cb: CallbackQuery, config: Config, bot: Bot) -> None:
    if not await _owner(cb, config):
        return
    index = _disk_index(cb.data, "ad:bks:")
    files = list_sqlite_backups(config.backup_path)
    if index is None or index >= len(files):
        await cb.answer("Копия уже недоступна", show_alert=True)
        return
    path = files[index]
    size = path.stat().st_size
    if size > TELEGRAM_DOCUMENT_LIMIT:
        await cb.answer("Файл слишком большой для Telegram", show_alert=True)
        return
    await cb.answer("Отправляю")
    sent = await safe_send(
        bot.send_document,
        config.owner_id,
        FSInputFile(path, filename=path.name),
        caption=f"🗄 <code>{html.escape(path.name)}</code>\nТолько SQLite, без .env",
        request_timeout=120,
    )
    if sent is None:
        await cb.answer("Не удалось отправить файл", show_alert=True)


@router.callback_query(F.data.startswith("ad:bkr:"))
async def backups_restore_disk(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not await _owner(cb, config):
        return
    index = _disk_index(cb.data, "ad:bkr:")
    files = list_sqlite_backups(config.backup_path)
    if index is None or index >= len(files):
        await cb.answer("Копия уже недоступна", show_alert=True)
        return
    path = files[index]
    try:
        preview = await inspect_sqlite(path, config.required_db_version, path.name)
    except Exception:
        logger.exception("Failed to inspect disk backup %s", path)
        await cb.answer("Не удалось прочитать файл", show_alert=True)
        return
    await state.set_state(AdminSG.restore_disk)
    await state.update_data(restore_disk=str(path), restore_name=path.name)
    await cb.answer()
    text = format_restore_preview(preview)
    if not preview.compatible:
        await safe_edit(cb.message, text, cancel_kb("ad:bk"))
        return
    await safe_edit(cb.message, text, admin_restore_confirm_kb(disk=True))


@router.callback_query(F.data == "ad:bkrok", AdminSG.restore_disk)
async def backups_restore_disk_ok(
    cb: CallbackQuery,
    state: FSMContext,
    config: Config,
    runtime: RuntimeControl | None,
) -> None:
    if not await _owner(cb, config):
        return
    if runtime is None:
        await cb.answer("Перезапуск недоступен", show_alert=True)
        return
    data = await state.get_data()
    path_str = data.get("restore_disk")
    source = Path(path_str) if path_str else None
    if source is None or not source.is_file():
        await cb.answer("Файл уже недоступен", show_alert=True)
        await state.clear()
        return
    try:
        stage_pending_sqlite(source, config.backup_path, data.get("restore_name"))
    except Exception:
        logger.exception("Failed to stage pending sqlite restore")
        await cb.answer("Не удалось подготовить восстановление", show_alert=True)
        return
    await state.clear()
    await cb.answer("Перезапускаюсь")
    await safe_edit(
        cb.message,
        "✅ Копия с диска принята. Перезапускаюсь с этими данными.\n"
        "В Docker контейнер поднимется сам. После старта придёт подтверждение.",
    )
    runtime.request_restart()

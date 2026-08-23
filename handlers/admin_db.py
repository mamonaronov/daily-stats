"""Owner-only SQLite editor inside the admin panel."""

from __future__ import annotations

import csv
import html
import logging
from io import StringIO
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.queries import PURGE_CONFIRM_PHRASE, Repo, SqlError
from handlers.admin import _owner
from keyboards.main import (
    admin_db_kb,
    admin_sql_kb,
    admin_table_kb,
    admin_tables_kb,
    cancel_kb,
)
from states.diary import AdminSG
from utils.telegram import safe_edit, safe_send, text_file

router = Router(name="admin_db")
logger = logging.getLogger(__name__)

TABLE_PAGE = 10
CSV_MAX_ROWS = 5000
SQL_MAX_ROWS = 200
CHAT_MAX_ROWS = 20
MSG_LIMIT = 3500


def _cell(value: Any, max_len: int = 40) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def format_sql_grid(columns: list[str], rows: list[tuple[Any, ...]], *, max_cell: int = 40) -> str:
    if not columns:
        return "(нет столбцов)"
    str_rows = [[_cell(value, max_cell) for value in row] for row in rows]
    widths = [len(name) for name in columns]
    for row in str_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def fmt(values: list[str]) -> str:
        return " | ".join(values[i].ljust(widths[i]) for i in range(len(columns)))

    lines = [fmt(columns), "-+-".join("-" * width for width in widths)]
    lines.extend(fmt(row) for row in str_rows)
    if not rows:
        lines.append("(пусто)")
    return "\n".join(lines)


def rows_to_csv(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return buf.getvalue()


def _pre(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


def _schema_text(name: str, columns: list[dict[str, Any]], indexes: list[str]) -> str:
    lines = [f"📋 <b>{html.escape(name)}</b>", "", "<b>Структура</b>"]
    if not columns:
        lines.append("Нет столбцов.")
    for col in columns:
        bits = [html.escape(str(col.get("name") or ""))]
        col_type = col.get("type")
        if col_type:
            bits.append(html.escape(str(col_type)))
        if col.get("pk"):
            bits.append("PK")
        if col.get("notnull"):
            bits.append("NOT NULL")
        default = col.get("dflt_value")
        if default is not None:
            bits.append(f"DEFAULT {html.escape(str(default))}")
        lines.append("• " + " ".join(bits))
    if indexes:
        lines.append("")
        lines.append("<b>Индексы</b>")
        for sql in indexes:
            lines.append("• " + html.escape(sql))
    return "\n".join(lines)


async def _show_db_root(cb: CallbackQuery, repo: Repo) -> None:
    tables = await repo.list_tables_with_counts()
    total_rows = sum(count for _, count in tables)
    text = (
        "🗄 <b>Редактор базы</b>\n\n"
        f"Таблиц: {len(tables)}\n"
        f"Строк всего: {total_rows}\n\n"
        "Можно смотреть таблицы, выполнять SQL и очистить пользовательские данные."
    )
    await cb.answer()
    await safe_edit(cb.message, text, admin_db_kb())


@router.callback_query(F.data == "ad:dbe")
async def admin_db_root(cb: CallbackQuery, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    await state.clear()
    await _show_db_root(cb, repo)


@router.callback_query(F.data == "ad:tbls")
async def admin_tables(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    tables = await repo.list_tables_with_counts()
    if not tables:
        await cb.answer("Таблиц нет", show_alert=True)
        return
    await cb.answer()
    await safe_edit(cb.message, "📋 <b>Таблицы</b>", admin_tables_kb(tables))


@router.callback_query(F.data.startswith("ad:tp:"))
async def admin_table_page(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    parts = (cb.data or "").split(":")
    if len(parts) != 4:
        await cb.answer("Некорректная ссылка", show_alert=True)
        return
    name = parts[2]
    try:
        offset = int(parts[3])
    except ValueError:
        await cb.answer("Некорректная страница", show_alert=True)
        return
    try:
        columns_info = await repo.table_schema(name)
        indexes = await repo.table_indexes(name)
        page = await repo.table_page(name, offset, TABLE_PAGE)
    except SqlError as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    total = page["total"]
    rows = page["rows"]
    start = offset + 1 if rows else 0
    end = offset + len(rows)
    text = _schema_text(name, columns_info, indexes)
    text += f"\n\n<b>Данные</b> {start}–{end} из {total}"
    if page["columns"]:
        grid = format_sql_grid(page["columns"], rows)
        chunk = _pre(grid)
        if len(text) + len(chunk) > MSG_LIMIT:
            text += "\nДанные не помещаются в сообщение — скачайте CSV."
        else:
            text += "\n" + chunk
    await cb.answer()
    await safe_edit(cb.message, text[:4000], admin_table_kb(name, offset, total, TABLE_PAGE))


@router.callback_query(F.data.startswith("ad:tf:"))
async def admin_table_csv(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    name = (cb.data or "").split(":", 2)[-1]
    try:
        payload = await repo.table_export(name, CSV_MAX_ROWS)
    except SqlError as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    csv_text = rows_to_csv(payload["columns"], payload["rows"])
    exported = len(payload["rows"])
    caption = f"{name}: {exported} из {payload['total']} строк"
    if exported < payload["total"]:
        caption += f" (лимит {CSV_MAX_ROWS})"
    await cb.answer("Отправляю CSV")
    if cb.message is None:
        return
    sent = await safe_send(
        cb.message.answer_document,
        text_file(csv_text, f"{name}.csv"),
        caption=caption,
    )
    if sent is None:
        await safe_edit(cb.message, "Не удалось отправить CSV.", admin_table_kb(name, 0, payload["total"], TABLE_PAGE))


@router.callback_query(F.data == "ad:dsch")
async def admin_schema_file(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    dump = await repo.schema_dump()
    if not dump:
        await cb.answer("Схема пуста", show_alert=True)
        return
    await cb.answer("Отправляю схему")
    if cb.message is None:
        return
    sent = await safe_send(
        cb.message.answer_document,
        text_file(dump, "schema.sql"),
        caption="Схема базы (CREATE TABLE / INDEX)",
    )
    if sent is None:
        await safe_edit(cb.message, "Не удалось отправить схему.", admin_db_kb())


@router.callback_query(F.data == "ad:dint")
async def admin_integrity(cb: CallbackQuery, config: Config, repo: Repo) -> None:
    if not await _owner(cb, config):
        return
    report = await repo.integrity_report()
    text = "🩺 <b>Целостность</b>\n\n" + _pre(report)
    await cb.answer()
    await safe_edit(cb.message, text[:4000], admin_db_kb())


@router.callback_query(F.data == "ad:sql")
async def admin_sql_start(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not await _owner(cb, config):
        return
    await state.set_state(AdminSG.sql)
    await cb.answer()
    await safe_edit(
        cb.message,
        "⌨️ <b>SQL</b>\n\n"
        "Отправьте один SQLite-запрос.\n"
        "SELECT / PRAGMA покажут строки, остальные команды — число затронутых записей.\n"
        "ATTACH, DETACH и VACUUM INTO запрещены.",
        cancel_kb("ad:dbe"),
    )


@router.message(AdminSG.sql)
async def admin_sql_run(message: Message, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(message, config):
        return
    sql = (message.text or "").strip()
    if not sql:
        await message.answer("Пустой запрос.", reply_markup=cancel_kb("ad:dbe"))
        return
    try:
        result = await repo.run_sql(sql, max_rows=SQL_MAX_ROWS)
    except SqlError as exc:
        await message.answer(f"Запрос отклонён: {html.escape(str(exc))}", reply_markup=admin_sql_kb())
        return
    except Exception as exc:
        logger.exception("Admin SQL failed")
        await message.answer(f"Ошибка SQLite: {html.escape(str(exc))}", reply_markup=admin_sql_kb())
        return

    if result["columns"]:
        count = len(result["rows"])
        header = f"⌨️ <b>{html.escape(result['keyword'])}</b> · {count} строк"
        if result["truncated"]:
            header += f" (показаны первые {SQL_MAX_ROWS})"
        grid = format_sql_grid(result["columns"], result["rows"][:CHAT_MAX_ROWS])
        body = header + "\n" + _pre(grid)
        too_wide = len(body) > MSG_LIMIT or count > CHAT_MAX_ROWS or result["truncated"]
        if too_wide:
            csv_text = rows_to_csv(result["columns"], result["rows"])
            await message.answer(header + "\nПолный результат — в файле.", reply_markup=admin_sql_kb())
            await safe_send(
                message.answer_document,
                text_file(csv_text, "query.csv"),
                caption=f"{result['keyword']} · {count} строк",
            )
            return
        await message.answer(body, reply_markup=admin_sql_kb())
        return

    affected = result["rowcount"]
    if affected is None or affected < 0:
        text = f"⌨️ <b>{html.escape(result['keyword'])}</b>\n\nГотово."
    else:
        text = f"⌨️ <b>{html.escape(result['keyword'])}</b>\n\nЗатронуто строк: {affected}"
    await message.answer(text, reply_markup=admin_sql_kb())


@router.callback_query(F.data == "ad:clr")
async def admin_purge_start(cb: CallbackQuery, state: FSMContext, config: Config) -> None:
    if not await _owner(cb, config):
        return
    await state.set_state(AdminSG.purge_confirm)
    await cb.answer()
    await safe_edit(
        cb.message,
        "🧹 <b>Очистка базы</b>\n\n"
        "Удалятся все пользовательские данные: дневник, кастомные метрики, операции, "
        "VPN-замеры, другие аккаунты.\n\n"
        "Останется только то, без чего бот не работает:\n"
        "• схема базы\n"
        "• служебная таблица <code>system_info</code>\n"
        f"• аккаунт владельца <code>{config.owner_id}</code> (настройки и напоминание)\n\n"
        "Перед очисткой будет создан бэкап.\n\n"
        f"Чтобы подтвердить, отправьте точно:\n<code>{html.escape(PURGE_CONFIRM_PHRASE)}</code>",
        cancel_kb("ad:dbe"),
    )


@router.message(AdminSG.purge_confirm)
async def admin_purge_run(message: Message, state: FSMContext, config: Config, repo: Repo) -> None:
    if not await _owner(message, config):
        return
    text = (message.text or "").strip()
    if text != PURGE_CONFIRM_PHRASE:
        await message.answer(
            f"Не совпало. Отправьте <code>{html.escape(PURGE_CONFIRM_PHRASE)}</code> или нажмите Отмена.",
            reply_markup=cancel_kb("ad:dbe"),
        )
        return
    try:
        backup = await repo.db.backup(prefix="pre_purge")
        deleted = await repo.purge_content(config.owner_id)
    except Exception as exc:
        logger.exception("Admin purge failed")
        await message.answer(f"Очистка не удалась: {html.escape(str(exc))}", reply_markup=admin_db_kb())
        return
    await state.clear()
    removed = sum(deleted.values())
    lines = ["🧹 <b>База очищена</b>", "", f"Бэкап: <code>{html.escape(backup.name)}</code>", f"Удалено строк: {removed}"]
    details = [f"{name}: {count}" for name, count in sorted(deleted.items()) if count]
    if details:
        lines.append("")
        lines.append(_pre("\n".join(details)))
    await message.answer("\n".join(lines)[:4000], reply_markup=admin_db_kb())

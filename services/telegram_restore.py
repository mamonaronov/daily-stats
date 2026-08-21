"""Restore database (and host files) from a Telegram backup archive."""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from config import REQUIRED_DB_VERSION, Config
from database.database import copy_sqlite_bundle, install_sqlite_file

logger = logging.getLogger(__name__)

PENDING_RESTORE_NAME = "pending-restore.tar.gz"
PENDING_SQLITE_NAME = "pending-restore.sqlite3"
PENDING_RESTORE_FILENAME = "pending-restore.filename"
SQLITE_HEADER = b"SQLite format 3\x00"
MAX_ARCHIVE_UNCOMPRESSED = 200 * 1024 * 1024
MAX_DATABASE_BYTES = 150 * 1024 * 1024
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024


class RestoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RestorePreview:
    archive_name: str
    db_version: int
    integrity_ok: bool
    users_count: int | None
    has_env: bool
    db_size: int
    required_db_version: int

    @property
    def too_new(self) -> bool:
        return self.db_version > self.required_db_version

    @property
    def compatible(self) -> bool:
        return self.integrity_ok and not self.too_new

    @property
    def needs_migrate(self) -> bool:
        return self.integrity_ok and self.db_version < self.required_db_version


def looks_like_backup_archive(name: str | None) -> bool:
    if not name:
        return False
    lower = name.lower()
    return lower.endswith(".tar.gz") or lower.endswith(".tgz")


def _member_relpath(name: str) -> Path | None:
    raw = name.replace("\\", "/").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.lstrip("/")
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _root_file(member: tarfile.TarInfo, filename: str) -> bool:
    rel = _member_relpath(member.name)
    return bool(rel) and rel.parts == (filename,) and member.isfile()


def extract_database(archive: Path, dest_dir: Path) -> tuple[Path, bool]:
    """Extract database.sqlite3 from a telegram backup tar.gz. Returns (db, has_env)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    db_dest = dest_dir / "database.sqlite3"
    has_env = False
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            has_env = any(_root_file(member, ".env") for member in members)
            db_member = next((member for member in members if _root_file(member, "database.sqlite3")), None)
            if db_member is None:
                raise RestoreError("В архиве нет database.sqlite3 — это не бэкап бота")
            if db_member.size < 0 or db_member.size > MAX_DATABASE_BYTES:
                raise RestoreError("Файл базы в архиве слишком большой или повреждён")
            if db_member.size > MAX_ARCHIVE_UNCOMPRESSED:
                raise RestoreError("Архив слишком большой")
            extracted = tar.extractfile(db_member)
            if extracted is None:
                raise RestoreError("Не удалось прочитать database.sqlite3 из архива")
            with extracted, db_dest.open("wb") as out:
                shutil.copyfileobj(extracted, out)
    except RestoreError:
        raise
    except tarfile.TarError as exc:
        raise RestoreError(f"Не получилось открыть архив: {exc}") from exc
    except OSError as exc:
        raise RestoreError(f"Ошибка чтения архива: {exc}") from exc
    if not db_dest.is_file():
        raise RestoreError("В архиве нет database.sqlite3 — это не бэкап бота")
    header = db_dest.read_bytes()[:16]
    if not header.startswith(SQLITE_HEADER):
        raise RestoreError("database.sqlite3 в архиве не является файлом SQLite")
    return db_dest, has_env


async def inspect_sqlite(path: Path, required_db_version: int, archive_name: str = "") -> RestorePreview:
    size = path.stat().st_size
    conn = await aiosqlite.connect(path)
    try:
        async with conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        version = int(row[0]) if row else 0
        try:
            async with conn.execute("PRAGMA integrity_check") as cur:
                check = await cur.fetchone()
            integrity_ok = bool(check) and str(check[0]).lower() == "ok"
        except Exception:
            logger.exception("integrity_check failed for restore candidate")
            integrity_ok = False
        users_count: int | None
        try:
            async with conn.execute("SELECT COUNT(*) FROM users") as cur:
                row = await cur.fetchone()
            users_count = int(row[0]) if row else 0
        except Exception:
            users_count = None
    finally:
        await conn.close()
    return RestorePreview(
        archive_name=archive_name or path.name,
        db_version=version,
        integrity_ok=integrity_ok,
        users_count=users_count,
        has_env=False,
        db_size=size,
        required_db_version=required_db_version,
    )


async def inspect_archive(archive: Path, required_db_version: int) -> RestorePreview:
    staging = Path(tempfile.mkdtemp(prefix="tg-restore-inspect-"))
    try:
        db_path, has_env = await asyncio.to_thread(extract_database, archive, staging)
        preview = await inspect_sqlite(db_path, required_db_version, archive.name)
        return RestorePreview(
            archive_name=archive.name,
            db_version=preview.db_version,
            integrity_ok=preview.integrity_ok,
            users_count=preview.users_count,
            has_env=has_env,
            db_size=preview.db_size,
            required_db_version=required_db_version,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def format_restore_done(preview: RestorePreview) -> str:
    users = "—" if preview.users_count is None else str(preview.users_count)
    return (
        "✅ <b>Бот запущен с данными из бэкапа</b>\n"
        f"<code>{html.escape(preview.archive_name)}</code>\n"
        f"БД: v{preview.db_version} → приложение v{preview.required_db_version}\n"
        f"Пользователей: {users}"
    )


def format_restore_preview(preview: RestorePreview) -> str:
    users = "—" if preview.users_count is None else str(preview.users_count)
    size_mb = preview.db_size / (1024 * 1024)
    integrity = "ок" if preview.integrity_ok else "ошибка"
    lines = [
        "📦 <b>Бэкап принят</b>",
        "",
        f"Файл: <code>{html.escape(preview.archive_name)}</code>",
        f"БД: v{preview.db_version} (приложение v{preview.required_db_version})",
        f"Целостность: {integrity}",
        f"Пользователей: {users}",
        f"Размер БД: {size_mb:.1f} МБ",
        f".env в архиве: {'да' if preview.has_env else 'нет'}",
    ]
    if not preview.integrity_ok:
        lines.extend(["", "Файл базы повреждён, восстановить нельзя."])
        return "\n".join(lines)
    if preview.too_new:
        lines.extend(
            [
                "",
                "Этот бэкап от <b>более новой</b> версии приложения. "
                "Сначала обновите бота, потом повторите восстановление.",
            ]
        )
        return "\n".join(lines)
    if preview.needs_migrate:
        lines.append("После перезапуска накатятся миграции схемы.")
    lines.extend(
        [
            "",
            "Текущая база будет сохранена рядом, затем бот перезапустится с этими данными.",
        ]
    )
    return "\n".join(lines)


def stage_pending_sqlite(source: Path, backup_dir: Path, original_name: str | None = None) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / PENDING_SQLITE_NAME
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        shutil.copy2(source, tmp)
        tmp.replace(dest)
        label = (original_name or source.name).strip() or dest.name
        (backup_dir / PENDING_RESTORE_FILENAME).write_text(label + "\n", encoding="utf-8")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Staged pending sqlite restore %s", dest)
    return dest


def stage_pending_restore(archive: Path, backup_dir: Path, original_name: str | None = None) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / PENDING_RESTORE_NAME
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        shutil.copy2(archive, tmp)
        tmp.replace(dest)
        label = (original_name or archive.name).strip() or dest.name
        (backup_dir / PENDING_RESTORE_FILENAME).write_text(label + "\n", encoding="utf-8")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Staged pending telegram restore %s", dest)
    return dest


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _relocate_pending(pending: Path, backup_dir: Path, prefix: str) -> Path:
    dest = backup_dir / f"{prefix}_{_stamp()}_{pending.name}"
    try:
        pending.replace(dest)
        return dest
    except OSError:
        shutil.copy2(pending, dest)
        pending.unlink(missing_ok=True)
        return dest


def _quarantine_current_db(db_path: Path) -> Path | None:
    if not db_path.is_file() or db_path.stat().st_size <= 0:
        return None
    dest = db_path.with_name(f"{db_path.name}.pre_restore.{_stamp()}")
    copy_sqlite_bundle(db_path, dest)
    logger.info("Current database quarantined at %s", dest.name)
    return dest


async def _commit_previewed_sqlite(
    sqlite_path: Path,
    db_path: Path,
    preview: RestorePreview,
) -> RestorePreview:
    if not preview.integrity_ok:
        raise RestoreError("База в бэкапе не проходит проверку целостности")
    if preview.too_new:
        raise RestoreError(
            f"Бэкап БД v{preview.db_version} новее приложения v{preview.required_db_version}"
        )
    quarantine = _quarantine_current_db(db_path)
    try:
        install_sqlite_file(sqlite_path, db_path)
    except Exception:
        if quarantine is not None:
            install_sqlite_file(quarantine, db_path)
        raise
    logger.info(
        "Database restored from %s (db v%s, users=%s)",
        preview.archive_name,
        preview.db_version,
        preview.users_count,
    )
    return preview


async def apply_archive(
    archive: Path,
    db_path: Path,
    required_db_version: int,
) -> RestorePreview:
    staging = Path(tempfile.mkdtemp(prefix="tg-restore-apply-"))
    try:
        sqlite_path, has_env = await asyncio.to_thread(extract_database, archive, staging)
        preview = await inspect_sqlite(sqlite_path, required_db_version, archive.name)
        preview = RestorePreview(
            archive_name=archive.name,
            db_version=preview.db_version,
            integrity_ok=preview.integrity_ok,
            users_count=preview.users_count,
            has_env=has_env,
            db_size=preview.db_size,
            required_db_version=required_db_version,
        )
        return await _commit_previewed_sqlite(sqlite_path, db_path, preview)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def apply_sqlite_path(
    source: Path,
    db_path: Path,
    required_db_version: int,
    archive_name: str | None = None,
) -> RestorePreview:
    preview = await inspect_sqlite(source, required_db_version, archive_name or source.name)
    return await _commit_previewed_sqlite(source, db_path, preview)


def _pending_label(backup_dir: Path, fallback: str) -> str:
    sidecar = backup_dir / PENDING_RESTORE_FILENAME
    if sidecar.is_file():
        try:
            label = sidecar.read_text(encoding="utf-8").strip()
            if label:
                return label
        except OSError:
            logger.warning("Cannot read %s", sidecar)
    return fallback


def _clear_pending_label(backup_dir: Path) -> None:
    (backup_dir / PENDING_RESTORE_FILENAME).unlink(missing_ok=True)


def _preview_with_label(preview: RestorePreview, label: str) -> RestorePreview:
    return RestorePreview(
        archive_name=label,
        db_version=preview.db_version,
        integrity_ok=preview.integrity_ok,
        users_count=preview.users_count,
        has_env=preview.has_env,
        db_size=preview.db_size,
        required_db_version=preview.required_db_version,
    )


async def _apply_pending_file(
    pending: Path,
    config: Config,
    apply,
) -> RestorePreview | None:
    backup_dir = config.backup_path
    label = _pending_label(backup_dir, pending.name)
    logger.info("Found pending restore %s (%s)", pending, label)
    try:
        preview = await apply(pending)
    except Exception as exc:
        relocated = _relocate_pending(pending, backup_dir, "rejected-restore")
        _clear_pending_label(backup_dir)
        if isinstance(exc, RestoreError):
            logger.error("Pending restore rejected (%s), kept as %s", exc, relocated.name)
        else:
            logger.exception("Pending restore failed, kept as %s", relocated.name)
        if config.db_path.is_file() and config.db_path.stat().st_size > 0:
            return None
        raise RestoreError(str(exc)) from exc
    _relocate_pending(pending, backup_dir, "applied-restore")
    _clear_pending_label(backup_dir)
    return _preview_with_label(preview, label)


async def apply_pending_telegram_restore(config: Config) -> RestorePreview | None:
    backup_dir = config.backup_path
    tar_pending = backup_dir / PENDING_RESTORE_NAME
    sqlite_pending = backup_dir / PENDING_SQLITE_NAME
    if tar_pending.is_file():
        return await _apply_pending_file(
            tar_pending,
            config,
            lambda path: apply_archive(path, config.db_path, config.required_db_version),
        )
    if sqlite_pending.is_file():
        return await _apply_pending_file(
            sqlite_pending,
            config,
            lambda path: apply_sqlite_path(
                path,
                config.db_path,
                config.required_db_version,
                _pending_label(backup_dir, path.name),
            ),
        )
    return None


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Восстановить базу daily-stats из архива, который бот шлёт в Telegram.",
    )
    parser.add_argument("archive", type=Path, help="Файл daily-stats-backup_*.tar.gz")
    parser.add_argument(
        "--stage",
        action="store_true",
        help="Только положить архив как pending-restore.tar.gz (бот применит при старте)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/database.sqlite3"),
        help="Куда поставить database.sqlite3 (по умолчанию data/database.sqlite3)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("backups"),
        help="Каталог backup (по умолчанию backups)",
    )
    args = parser.parse_args(argv)
    archive = args.archive.expanduser().resolve()
    if not archive.is_file():
        print(f"error: archive not found: {archive}", file=sys.stderr)
        return 2

    async def _run() -> None:
        preview = await inspect_archive(archive, REQUIRED_DB_VERSION)
        print(
            f"archive={preview.archive_name} db=v{preview.db_version} "
            f"integrity={'ok' if preview.integrity_ok else 'fail'} "
            f"users={preview.users_count} env={'yes' if preview.has_env else 'no'}"
        )
        if not preview.compatible:
            raise RestoreError("archive is not compatible")
        if args.stage:
            stage_pending_restore(archive, args.backup_dir, archive.name)
            print(f"staged {args.backup_dir / PENDING_RESTORE_NAME}")
            return
        await apply_archive(archive, args.db_path, REQUIRED_DB_VERSION)
        print(f"restored {args.db_path}")

    try:
        asyncio.run(_run())
    except RestoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

"""SQLite engine: WAL, migrations, integrity, backup and restore."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from config import Config
from database.vpn_database import (
    VpnDatabase,
    diary_vpn_rowcount,
    export_legacy_vpn_samples,
    vacuum_sqlite,
)
from utils.time import now_utc, to_iso

logger = logging.getLogger(__name__)

# A hung sqlite3_backup on the request connection freezes polling. Keep a bound.
_BACKUP_TIMEOUT_SECONDS = 60.0
_BACKUP_CLOSE_TIMEOUT_SECONDS = 2.0

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


class DatabaseError(RuntimeError):
    pass


class DatabaseUnrecoverableError(DatabaseError):
    pass


def _backup_name(prefix: str = "backup") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}.sqlite3"


_SKIP_BACKUP_PREFIXES = (
    "pending-restore",
    "applied-restore",
    "rejected-restore",
    "incoming-restore",
)


def is_managed_sqlite_backup(path: Path) -> bool:
    if not path.is_file() or not path.name.endswith(".sqlite3"):
        return False
    if path.name == "vpn.sqlite3":
        return False
    return not path.name.startswith(_SKIP_BACKUP_PREFIXES)


def list_sqlite_backups(backup_dir: Path) -> list[Path]:
    if not backup_dir.is_dir():
        return []
    files = [p for p in backup_dir.glob("*.sqlite3") if is_managed_sqlite_backup(p)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def sqlite_sidecar(path: Path, suffix: str) -> Path:
    return Path(str(path) + suffix)


def clear_sqlite_sidecars(db_path: Path) -> None:
    sqlite_sidecar(db_path, "-wal").unlink(missing_ok=True)
    sqlite_sidecar(db_path, "-shm").unlink(missing_ok=True)


def copy_sqlite_bundle(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    for suffix in ("-wal", "-shm"):
        extra = sqlite_sidecar(src, suffix)
        if extra.is_file():
            shutil.copy2(extra, sqlite_sidecar(dest, suffix))


def install_sqlite_file(source: Path, dest: Path) -> None:
    """Replace dest with source and drop leftover WAL/SHM so they cannot mix."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".incoming")
    try:
        shutil.copy2(source, tmp)
        clear_sqlite_sidecars(dest)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


class Database:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.db_path
        self.backup_dir = config.backup_path
        self._conn: aiosqlite.Connection | None = None
        self.vpn_db: VpnDatabase | None = None
        self._vacuum_diary_after_vpn_move = False

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise DatabaseError("Database is not connected")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._apply_pragmas(self._conn)
        await self._assert_wal()

    async def close(self) -> None:
        if self.vpn_db is not None:
            try:
                await self.vpn_db.close()
            except Exception:
                logger.exception("VPN database close failed")
            self.vpn_db = None
        if self._conn is not None:
            try:
                await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                logger.exception("WAL checkpoint failed during close")
            await self._conn.close()
            self._conn = None

    async def vacuum(self) -> None:
        await vacuum_sqlite(self.conn)

    async def _apply_pragmas(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA temp_store=MEMORY")
        await conn.execute("PRAGMA wal_autocheckpoint=1000")
        await conn.commit()

    async def _assert_wal(self) -> None:
        async with self.conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        mode = (row[0] if row else "").lower()
        if mode != "wal":
            raise DatabaseError(f"SQLite journal_mode is {mode!r}, expected WAL")
        logger.info("SQLite WAL mode confirmed")

    async def integrity_ok(self, conn: aiosqlite.Connection | None = None) -> bool:
        target = conn or self.conn
        try:
            async with target.execute("PRAGMA integrity_check") as cur:
                row = await cur.fetchone()
            return bool(row) and str(row[0]).lower() == "ok"
        except Exception:
            logger.exception("integrity_check failed")
            return False

    async def user_version(self) -> int:
        async with self.conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def set_user_version(self, version: int) -> None:
        await self.conn.execute(f"PRAGMA user_version={int(version)}")
        await self.conn.commit()

    def _migration_files(self) -> list[tuple[int, Path]]:
        files: list[tuple[int, Path]] = []
        if not MIGRATIONS_DIR.exists():
            return files
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            prefix = path.name.split("_", 1)[0]
            if prefix.isdigit():
                files.append((int(prefix), path))
        return files

    async def migrate(self) -> None:
        current = await self.user_version()
        required = self.config.required_db_version
        logger.info("DB version current=%s required=%s", current, required)
        if current > required:
            raise DatabaseError(
                f"Database version {current} is newer than application {required}"
            )
        if current == required:
            await self._finish_vpn_move()
            return

        files = [(ver, path) for ver, path in self._migration_files() if ver > current]
        if not files:
            raise DatabaseError(f"No migrations to go from {current} to {required}")

        await self.backup(prefix="pre_migrate")
        for version, path in files:
            sql = path.read_text(encoding="utf-8")
            logger.info("Applying migration %s", path.name)
            try:
                await self.conn.executescript(sql)
                await self.set_user_version(version)
            except Exception:
                logger.exception("Migration %s failed", path.name)
                raise DatabaseError(f"Migration {path.name} failed") from None
        final = await self.user_version()
        if final < required:
            raise DatabaseError(f"Migrations finished at {final}, required {required}")
        logger.info("Migrations complete, version=%s", final)
        await self._finish_vpn_move()

    async def _init_vpn_database(self) -> None:
        if self.vpn_db is None:
            self.vpn_db = VpnDatabase(self.config)
            await self.vpn_db.initialize()
        current = await self.user_version()
        if current >= 12:
            return
        legacy_rows = await diary_vpn_rowcount(self.conn)
        await export_legacy_vpn_samples(
            self.conn, self.vpn_db, self.config.vpn_log_keep_days
        )
        self._vacuum_diary_after_vpn_move = legacy_rows > 0

    async def _finish_vpn_move(self) -> None:
        if self._vacuum_diary_after_vpn_move:
            logger.info("Vacuuming diary database after removing VPN samples")
            await self.vacuum()
            self._vacuum_diary_after_vpn_move = False
        if self.vpn_db is not None:
            await self.vpn_db.prune_retained(self.config.vpn_log_keep_days)

    async def _close_backup_conn(self, conn: aiosqlite.Connection | None) -> None:
        if conn is None:
            return
        try:
            await asyncio.wait_for(conn.close(), timeout=_BACKUP_CLOSE_TIMEOUT_SECONDS)
        except Exception:
            logger.warning("Backup connection close timed out or failed")

    async def backup_to(self, dest_path: Path) -> Path:
        """Write a consistent SQLite snapshot to dest_path (includes WAL pages).

        Copies via a side connection so a stuck sqlite3_backup cannot block
        the request connection that polling and handlers use.
        """
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_name(dest_path.name + ".tmp")
        src: aiosqlite.Connection | None = None
        dest: aiosqlite.Connection | None = None
        try:
            src = await aiosqlite.connect(self.path)
            dest = await aiosqlite.connect(tmp_path)
            await asyncio.wait_for(src.backup(dest), timeout=_BACKUP_TIMEOUT_SECONDS)
            await self._close_backup_conn(dest)
            dest = None
            await self._close_backup_conn(src)
            src = None
            tmp_path.replace(dest_path)
            return dest_path
        except TimeoutError as exc:
            logger.error(
                "SQLite backup timed out after %.0fs: %s",
                _BACKUP_TIMEOUT_SECONDS,
                dest_path.name,
            )
            tmp_path.unlink(missing_ok=True)
            raise DatabaseError(
                f"SQLite backup timed out after {_BACKUP_TIMEOUT_SECONDS:.0f}s"
            ) from exc
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        finally:
            await self._close_backup_conn(dest)
            await self._close_backup_conn(src)

    async def backup(self, prefix: str = "backup") -> Path:
        """Online backup via SQLite Backup API — includes WAL pages."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dest_path = self.backup_dir / _backup_name(prefix)
        try:
            await self.backup_to(dest_path)
            await self._set_system("last_backup_at", to_iso(now_utc()))
            await self._set_system("last_backup_path", str(dest_path))
            logger.info("Backup created: %s", dest_path.name)
            self._rotate_backups()
            return dest_path
        except Exception:
            dest_path.unlink(missing_ok=True)
            logger.exception("Backup failed")
            raise

    def _rotate_backups(self) -> None:
        files = list_sqlite_backups(self.backup_dir)
        keep = max(1, self.config.backup_keep)
        for stale in files[keep:]:
            try:
                stale.unlink()
                logger.info("Rotated old backup %s", stale.name)
            except OSError:
                logger.exception("Failed to remove old backup %s", stale)

    def latest_backup(self) -> Path | None:
        files = list_sqlite_backups(self.backup_dir)
        return files[0] if files else None

    async def get_system(self, key: str) -> str | None:
        try:
            async with self.conn.execute(
                "SELECT value FROM system_info WHERE key = ?",
                (key,),
            ) as cur:
                row = await cur.fetchone()
            return str(row[0]) if row and row[0] is not None else None
        except Exception:
            return None

    async def _set_system(self, key: str, value: str) -> None:
        try:
            await self.conn.execute(
                """
                INSERT INTO system_info(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, to_iso(now_utc())),
            )
            await self.conn.commit()
        except Exception:
            # Table may not exist yet during first migrate.
            await self.conn.rollback()

    async def quarantine_damaged(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        damaged = self.path.with_name(f"{self.path.name}.damaged.{stamp}")
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        copy_sqlite_bundle(self.path, damaged)
        logger.critical("Damaged database copied to %s", damaged)
        return damaged

    async def restore_from(self, backup_path: Path) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        install_sqlite_file(backup_path, self.path)
        await self.connect()
        if not await self.integrity_ok():
            raise DatabaseUnrecoverableError("Restored backup failed integrity_check")
        logger.info("Database restored from %s", backup_path.name)

    async def initialize(self) -> None:
        """Connect, repair if needed, migrate, confirm WAL."""
        await self.connect()
        if self.path.exists() and self.path.stat().st_size > 0:
            if not await self.integrity_ok():
                logger.critical("Primary database failed integrity_check")
                await self.quarantine_damaged()
                backup = self.latest_backup()
                if backup is None:
                    raise DatabaseUnrecoverableError(
                        "Database damaged and no backup is available"
                    )
                await self.restore_from(backup)
        await self._init_vpn_database()
        await self.migrate()
        if not await self.integrity_ok():
            raise DatabaseUnrecoverableError("Database failed integrity_check after migrate")
        await self._assert_wal()

from __future__ import annotations

from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import time
from typing import Any, BinaryIO, Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, URL, create_engine, event, text


def _local_path(value: str | Path) -> Path:
    raw = str(value).strip()
    if os.name == "nt" and len(raw) >= 3:
        if raw[0] in {"/", "\\"} and raw[1].isalpha() and raw[2] in {"/", "\\"}:
            raw = f"{raw[1].upper()}:{raw[2:]}"
    return Path(raw).expanduser()


def default_database_path() -> Path:
    configured = os.getenv("AURALY_DATABASE_PATH", "").strip()
    if configured:
        return _local_path(configured)
    return Path.home() / ".auraly" / "auraly.db"


def sqlite_url(database_path: Path) -> str:
    database = _local_path(database_path).resolve().as_posix()
    return URL.create("sqlite", database=database).render_as_string(hide_password=False)


def create_sqlite_engine(database_path: Path) -> Engine:
    database_path = _local_path(database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(sqlite_url(database_path))

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


def _try_lock_file(lock_file: BinaryIO) -> bool:
    lock_file.seek(0)
    try:
        if os.name == "nt":
            msvcrt: Any = importlib.import_module("msvcrt")
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl: Any = importlib.import_module("fcntl")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_file(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        msvcrt: Any = importlib.import_module("msvcrt")
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl: Any = importlib.import_module("fcntl")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _database_migration_lock(database_path: Path, *, timeout_seconds: float = 30) -> Iterator[None]:
    lock_path = database_path.with_name(f"{database_path.name}.migration.lock")
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + timeout_seconds
        while not _try_lock_file(lock_file):
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the database migration lock")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock_file(lock_file)


def migrate_database(database_path: Path) -> None:
    database_path = _local_path(database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _database_migration_lock(database_path):
        package_root = Path(__file__).resolve().parent
        config = Config()
        config.set_main_option("script_location", str(package_root / "migrations"))
        config.set_main_option("sqlalchemy.url", sqlite_url(database_path))
        command.upgrade(config, "head")
        engine = create_sqlite_engine(database_path)
        try:
            with engine.begin() as connection:
                connection.execute(text("PRAGMA journal_mode=WAL"))
        finally:
            engine.dispose()

from __future__ import annotations

import os
from pathlib import Path

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


def migrate_database(database_path: Path) -> None:
    database_path = _local_path(database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
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

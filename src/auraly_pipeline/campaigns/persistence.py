from __future__ import annotations

from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, BinaryIO, Iterator

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, URL, create_engine, event, inspect, text
from sqlalchemy.pool import NullPool


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
    engine = create_engine(URL.create("sqlite", database=database_path.as_posix()))

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


def create_readonly_sqlite_engine(database_path: Path) -> Engine:
    return _existing_sqlite_engine(database_path, mode="ro")


def create_existing_sqlite_engine(database_path: Path) -> Engine:
    engine = _existing_sqlite_engine(database_path, mode="rw")
    try:
        validate_api_database(engine)
    except Exception:
        engine.dispose()
        raise
    return engine


def _existing_sqlite_engine(database_path: Path, *, mode: str) -> Engine:
    database = _local_path(database_path).absolute()
    if not database.is_file() or database.is_symlink():
        raise ValueError("existing regular database required")

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(database.as_uri() + f"?mode={mode}", uri=True,
                                     check_same_thread=False)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    return create_engine("sqlite://", creator=connect, poolclass=NullPool)


def validate_api_database(engine: Engine) -> None:
    from auraly_pipeline.campaigns.db_models import Base
    import auraly_pipeline.heygen.db_models  # noqa: F401
    import auraly_pipeline.images.db_models  # noqa: F401
    import auraly_pipeline.jobs.db_models  # noqa: F401
    import auraly_pipeline.voices.db_models  # noqa: F401

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        inspector = inspect(connection)
        if not inspector.has_table("alembic_version"):
            raise ValueError("incompatible database")
        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
        if list(revisions) != [head]:
            raise ValueError("incompatible database")
        for name, table in Base.metadata.tables.items():
            if not inspector.has_table(name):
                raise ValueError("incompatible database")
            columns = {column["name"] for column in inspector.get_columns(name)}
            if not set(table.columns.keys()).issubset(columns):
                raise ValueError("incompatible database")


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
    deadline = time.monotonic() + timeout_seconds
    try:
        with lock_path.open("x+b") as new_lock_file:
            new_lock_file.write(b"\0")
            new_lock_file.flush()
    except FileExistsError:
        while True:
            try:
                with lock_path.open("r+b") as existing_lock_file:
                    existing_lock_file.seek(0, os.SEEK_END)
                    if existing_lock_file.tell() == 0:
                        existing_lock_file.write(b"\0")
                        existing_lock_file.flush()
                break
            except (FileNotFoundError, PermissionError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out initializing the database migration lock"
                    ) from None
                time.sleep(0.01)
    with lock_path.open("r+b") as lock_file:
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
        config.set_main_option("sqlalchemy.url", sqlite_url(database_path).replace("%", "%%"))
        config.attributes["database_url"] = URL.create(
            "sqlite", database=database_path.as_posix(),
        )
        command.upgrade(config, "head")
        engine = create_sqlite_engine(database_path)
        try:
            with engine.begin() as connection:
                connection.execute(text("PRAGMA journal_mode=WAL"))
        finally:
            engine.dispose()

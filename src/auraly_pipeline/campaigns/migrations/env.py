"""Alembic environment for the local Auraly SQLite database."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from auraly_pipeline.campaigns.db_models import Base
import auraly_pipeline.jobs.db_models  # noqa: F401
from auraly_pipeline.campaigns.persistence import default_database_path, sqlite_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

configured_url = config.get_main_option("sqlalchemy.url", "").strip()
if not configured_url or configured_url == "sqlite:///":
    configured_url = sqlite_url(default_database_path())
database_url = make_url(configured_url)
if database_url.get_backend_name() != "sqlite":
    raise RuntimeError("Campaign persistence requires SQLite.")
if database_url.database and database_url.database != ":memory:":
    Path(database_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", configured_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Alembic environment for the local Auraly SQLite database."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import URL, engine_from_config, pool
from sqlalchemy.engine import make_url

from auraly_pipeline.campaigns.db_models import Base
import auraly_pipeline.images.db_models  # noqa: F401
import auraly_pipeline.heygen.db_models  # noqa: F401
import auraly_pipeline.jobs.db_models  # noqa: F401
import auraly_pipeline.voices.db_models  # noqa: F401
from auraly_pipeline.campaigns.persistence import default_database_path

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

configured_url = config.get_main_option("sqlalchemy.url", "").strip()
if not configured_url or configured_url == "sqlite:///":
    database_url = URL.create(
        "sqlite", database=default_database_path().resolve().as_posix(),
    )
else:
    database_url = config.attributes.get("database_url") or make_url(configured_url)
if database_url.get_backend_name() != "sqlite":
    raise RuntimeError("Campaign persistence requires SQLite.")
if database_url.database and database_url.database != ":memory:":
    Path(database_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration: dict[str, object] = dict(config.get_section(config.config_ini_section, {}))
    configuration["sqlalchemy.url"] = database_url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # SQLite table rebuilds require FK enforcement off on this migration-only
        # connection. Check every FK before committing the atomic migration.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            context.run_migrations()
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise RuntimeError("Migration would violate foreign key integrity")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

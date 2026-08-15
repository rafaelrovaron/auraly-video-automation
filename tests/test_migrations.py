from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from auraly_pipeline.campaigns.persistence import (
    create_sqlite_engine,
    default_database_path,
    migrate_database,
    sqlite_url,
)


def test_fresh_database_migrates_to_campaign_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh" / "auraly.db"

    migrate_database(database_path)

    engine = create_engine(sqlite_url(database_path))
    assert {
        "alembic_version",
        "campaigns",
        "copy_masters",
        "scene_variants",
    }.issubset(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().casefold() == "wal"
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
        assert {
            "prevent_approved_copy_master_delete",
            "prevent_approved_copy_master_update",
        }.issubset(triggers)
    forbidden_columns = {"blob", "secret", "token", "cookie", "signed_url", "media"}
    expected_checks = {
        "campaigns": {"ck_campaigns_campaign_status"},
        "copy_masters": {
            "ck_copy_masters_copy_master_approved",
            "ck_copy_masters_copy_master_version",
        },
        "scene_variants": {"ck_scene_variants_scene_variant_status"},
    }
    for table_name in ("campaigns", "copy_masters", "scene_variants"):
        columns = inspect(engine).get_columns(table_name)
        assert not ({column["name"] for column in columns} & forbidden_columns)
        assert all("BLOB" not in str(column["type"]).upper() for column in columns)
        assert {item["name"] for item in inspect(engine).get_check_constraints(table_name)} == (
            expected_checks[table_name]
        )
    for table_name in ("copy_masters", "scene_variants"):
        foreign_keys = inspect(engine).get_foreign_keys(table_name)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["options"].get("ondelete") == "RESTRICT"
    engine.dispose()


def test_migrations_are_idempotent_and_application_enables_foreign_keys(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"

    migrate_database(database_path)
    migrate_database(database_path)

    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
        assert connection.execute(text("PRAGMA synchronous")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0004_image_domain"
        )
    engine.dispose()


@pytest.mark.skipif(os.name != "nt", reason="Windows MSYS path semantics only")
def test_database_path_accepts_msys_drive_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURALY_DATABASE_PATH", r"\c\Users\Example\.auraly\auraly.db")

    assert default_database_path() == Path(r"C:\Users\Example\.auraly\auraly.db")


@pytest.mark.skipif(os.name == "nt", reason="POSIX path semantics only")
def test_database_path_accepts_native_posix_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURALY_DATABASE_PATH", "/tmp/auraly/auraly.db")

    assert default_database_path() == Path("/tmp/auraly/auraly.db")


def test_direct_alembic_upgrade_creates_parent_and_enables_wal(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "state" / "auraly.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))

    command.upgrade(config, "head")

    engine = create_engine(sqlite_url(database_path))
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().casefold() == "wal"
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0004_image_domain"
        )
    engine.dispose()

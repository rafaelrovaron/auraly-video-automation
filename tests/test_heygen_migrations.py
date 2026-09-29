from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from auraly_pipeline.campaigns.persistence import sqlite_url


NOW = "2026-09-29T12:00:00+00:00"


def _config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sqlite_url(database))
    return config


def _asset_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "00000000-0000-4000-8000-000000000001",
        "provider": "heygen",
        "account": "account-a",
        "kind": "image",
        "sha": "a" * 64,
        "mime": "image/png",
        "size": 10,
        "asset": "asset-1",
        "batch": "batch-1",
        "status": "allocated",
        "now": NOW,
    }
    values.update(overrides)
    return values


def _insert_asset(connection: object, **overrides: object) -> None:
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO remote_assets (id,provider,provider_account_ref,kind,sha256,mime_type,"
            "size_bytes,remote_asset_id,remote_batch_id,status,created_at,updated_at) VALUES "
            "(:id,:provider,:account,:kind,:sha,:mime,:size,:asset,:batch,:status,:now,:now)"
        ),
        _asset_values(**overrides),
    )


def test_migration_creates_exact_remote_asset_contract(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    config = _config(database)
    command.upgrade(config, "0006_manual_image_import")
    command.upgrade(config, "0007_heygen_remote_assets")
    engine = create_engine(sqlite_url(database))

    assert {column["name"] for column in inspect(engine).get_columns("remote_assets")} == {
        "id",
        "provider",
        "provider_account_ref",
        "kind",
        "sha256",
        "mime_type",
        "size_bytes",
        "remote_asset_id",
        "remote_batch_id",
        "status",
        "last_error_code",
        "last_error_message",
        "created_at",
        "updated_at",
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0007_heygen_remote_assets"
        )
    engine.dispose()


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "other"), ("kind", "video"), ("status", "unknown"), ("size", 0)],
)
def test_remote_asset_constraints_reject_invalid_values(
    tmp_path: Path, field: str, value: object
) -> None:
    database = tmp_path / f"{field}.db"
    command.upgrade(_config(database), "head")
    engine = create_engine(sqlite_url(database))

    with engine.begin() as connection, pytest.raises(IntegrityError):
        _insert_asset(connection, **{field: value})
    engine.dispose()


def test_remote_asset_unique_keys_reject_duplicates(tmp_path: Path) -> None:
    database = tmp_path / "unique.db"
    command.upgrade(_config(database), "head")
    engine = create_engine(sqlite_url(database))

    with engine.begin() as connection:
        _insert_asset(connection)
    with engine.begin() as connection, pytest.raises(IntegrityError):
        _insert_asset(connection, id="00000000-0000-4000-8000-000000000002", asset="asset-2")
    with engine.begin() as connection, pytest.raises(IntegrityError):
        _insert_asset(
            connection,
            id="00000000-0000-4000-8000-000000000003",
            sha="b" * 64,
        )
    engine.dispose()

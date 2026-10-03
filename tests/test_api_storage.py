from __future__ import annotations

import importlib
import os
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from auraly_pipeline.campaigns import persistence


def readonly(database: Path) -> Any:
    factory = getattr(persistence, "create_readonly_sqlite_engine", None)
    assert callable(factory), "readonly engine factory not implemented"
    return factory(database)


def settings_type() -> Any:
    assert importlib.util.find_spec("auraly_pipeline.api") is not None, "API package missing"
    return importlib.import_module("auraly_pipeline.api.contracts").ApiSettings


def test_readonly_engine_refuses_writes(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    persistence.migrate_database(db)
    engine = readonly(db)
    try:
        with engine.begin() as connection, pytest.raises(OperationalError):
            connection.execute(text("DELETE FROM campaigns"))
    finally:
        engine.dispose()


def test_missing_database_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "absent" / "test.db"
    with pytest.raises(ValueError, match="existing regular database required"):
        readonly(missing)
    assert not missing.parent.exists()


@pytest.mark.parametrize("damage", ["revision", "table", "column"])
def test_database_head_and_schema_required(tmp_path: Path, damage: str) -> None:
    db = tmp_path / "test.db"
    persistence.migrate_database(db)
    with sqlite3.connect(db) as connection:
        if damage == "revision":
            connection.execute("UPDATE alembic_version SET version_num='old'")
        elif damage == "table":
            connection.execute("DROP TABLE heygen_renders")
        else:
            connection.execute("ALTER TABLE campaigns RENAME COLUMN character TO unknown_character")
    engine = readonly(db)
    try:
        with pytest.raises(ValueError, match="incompatible database"):
            persistence.validate_api_database(engine)
    finally:
        engine.dispose()


def test_database_outside_project_with_special_characters(tmp_path: Path) -> None:
    db = tmp_path / "a # space.db"
    persistence.migrate_database(db)
    settings = settings_type().from_options(project_root=tmp_path / "project", database=db)
    engine = readonly(settings.database)
    try:
        persistence.validate_api_database(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM campaigns")).scalar() == 0
    finally:
        engine.dispose()
    assert sorted(p.name for p in tmp_path.glob("*.db")) == ["a # space.db"]


@pytest.mark.skipif(os.name == "nt", reason="Windows disallows question marks in filenames")
def test_database_question_mark_uri(tmp_path: Path) -> None:
    db = tmp_path / "a?.db"
    persistence.migrate_database(db)
    assert db.is_file()
    engine = readonly(db)
    try:
        persistence.validate_api_database(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize("filename", ["a?.db", "a#.db", "a%.db", "a space.db"])
def test_writer_preserves_exact_database_filename(tmp_path: Path, filename: str) -> None:
    database = tmp_path / filename
    engine = persistence.create_sqlite_engine(database)
    try:
        # Construction does not connect, so '?' is testable even on Windows.
        assert engine.url.database == database.resolve().as_posix()
    finally:
        engine.dispose()


def test_migration_preserves_percent_filename(tmp_path: Path) -> None:
    database = tmp_path / "a%25 # space.db"
    persistence.migrate_database(database)
    assert database.is_file()
    engine = readonly(database)
    try:
        persistence.validate_api_database(engine)
    finally:
        engine.dispose()


def test_roots_reject_links_before_resolve(tmp_path: Path) -> None:
    cls = settings_type()
    target, linked = tmp_path / "target", tmp_path / "linked"
    target.mkdir()
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlinks")
    with pytest.raises(ValueError, match="Invalid local API configuration"):
        cls.from_options(project_root=linked / "project", database=target / "test.db")


def test_live_wal_reads_committed_updates(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    persistence.migrate_database(db)
    writer = sqlite3.connect(db)
    writer.execute("CREATE TABLE observed (value INTEGER)")
    writer.commit()
    engine = readonly(db)
    try:
        persistence.validate_api_database(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM observed")).scalar() == 0
        writer.execute("INSERT INTO observed VALUES (1)")
        writer.commit()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM observed")).scalar() == 1
    finally:
        engine.dispose()
        writer.close()


def test_startup_error_is_sanitized(tmp_path: Path) -> None:
    cls = settings_type()
    with pytest.raises(ValueError) as error:
        cls.from_options(project_root=tmp_path, work_root=tmp_path.parent / "private")
    assert str(tmp_path) not in str(error.value)
    assert "private" not in str(error.value)


def test_settings_defaults_preserve_lexical_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AURALY_PROJECT_ROOT", str(tmp_path / "project"))
    monkeypatch.setenv("AURALY_DATABASE_PATH", str(tmp_path / "outside.db"))
    settings = settings_type().from_options()
    assert settings.project_root == tmp_path / "project"
    assert settings.work_root == tmp_path / "project" / "pipeline" / "work"
    assert settings.database == tmp_path / "outside.db"

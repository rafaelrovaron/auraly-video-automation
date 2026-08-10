from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import sqlite_url
from auraly_pipeline.campaigns.service import CampaignService
from tests.test_campaign_domain import valid_campaign_data


GOAL_1_REVISION = "0001_campaign_foundation"
GOAL_2_REVISION = "0002_persistent_job_orchestration"


def _config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))
    return config


def test_goal_one_database_upgrades_to_goal_two_without_rewriting_goal_one(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade" / "auraly.db"
    config = _config(database_path)
    command.upgrade(config, GOAL_1_REVISION)
    engine = create_engine(sqlite_url(database_path))
    assert "jobs" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(sqlite_url(database_path))
    assert {"jobs", "job_attempts", "job_events"}.issubset(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            GOAL_2_REVISION
        )
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().casefold() == "wal"
    engine.dispose()


def test_fresh_head_has_job_constraints_foreign_keys_and_no_blob_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh" / "auraly.db"
    command.upgrade(_config(database_path), "head")
    engine = create_engine(sqlite_url(database_path))
    inspector = inspect(engine)

    expected_checks = {
        "jobs": {
            "ck_jobs_job_attempt_bounds",
            "ck_jobs_job_priority",
            "ck_jobs_job_reference_scope",
            "ck_jobs_job_retry_safety",
            "ck_jobs_job_status",
            "ck_jobs_job_status_fields",
        },
        "job_attempts": {
            "ck_job_attempts_job_attempt_number",
            "ck_job_attempts_job_attempt_status",
        },
    }
    for table_name, checks in expected_checks.items():
        assert {item["name"] for item in inspector.get_check_constraints(table_name)} == checks
    for table_name in ("jobs", "job_attempts", "job_events"):
        columns = inspector.get_columns(table_name)
        assert all("BLOB" not in str(column["type"]).upper() for column in columns)
        assert not {
            "secret",
            "token",
            "cookie",
            "signed_url",
            "media",
            "browser_profile",
        }.intersection(column["name"] for column in columns)

    job_foreign_keys = {item["constrained_columns"][0]: item for item in inspector.get_foreign_keys("jobs")}
    assert job_foreign_keys["campaign_id"]["referred_table"] == "campaigns"
    assert job_foreign_keys["scene_variant_id"]["referred_table"] == "scene_variants"
    assert job_foreign_keys["campaign_id"]["options"].get("ondelete") == "RESTRICT"
    assert job_foreign_keys["scene_variant_id"]["options"].get("ondelete") == "RESTRICT"
    assert {item["name"] for item in inspector.get_unique_constraints("jobs")} == {
        "uq_jobs_idempotency_key"
    }
    engine.dispose()


def test_finished_attempts_and_events_are_immutable_audit_records(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    command.upgrade(_config(database_path), "head")
    engine = create_engine(sqlite_url(database_path))
    now = "2026-08-10 12:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, job_type, status, priority, idempotency_key, request_fingerprint, "
                "input_json, attempt_count, max_attempts, retry_safety, created_at, updated_at, queued_at) "
                "VALUES ('job-1', 'fake.success', 'queued', 0, 'key-1', :fingerprint, "
                "'{}', 0, 3, 'idempotent', :now, :now, :now)"
            ),
            {"fingerprint": "a" * 64, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO job_attempts "
                "(id, job_id, attempt_number, worker_id, status, started_at, lease_expires_at, "
                "finished_at) VALUES ('attempt-1', 'job-1', 1, 'worker-1', 'completed', "
                ":now, :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO job_events (id, job_id, event_type, timestamp, metadata_json) "
                "VALUES ('event-1', 'job-1', 'job.created', :now, '{}')"
            ),
            {"now": now},
        )

    with pytest.raises(DBAPIError, match="finished job attempt is immutable"):
        with engine.begin() as connection:
            connection.execute(text("UPDATE job_attempts SET status='failed' WHERE id='attempt-1'"))
    with pytest.raises(DBAPIError, match="job attempt history is immutable"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM job_attempts WHERE id='attempt-1'"))
    with pytest.raises(DBAPIError, match="job event history is immutable"):
        with engine.begin() as connection:
            connection.execute(text("UPDATE job_events SET event_type='job.failed' WHERE id='event-1'"))
    with pytest.raises(DBAPIError, match="job event history is immutable"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM job_events WHERE id='event-1'"))
    with pytest.raises(DBAPIError, match="job attempt history is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT OR REPLACE INTO job_attempts "
                    "(id, job_id, attempt_number, worker_id, status, started_at, "
                    "lease_expires_at, finished_at) VALUES "
                    "('attempt-1', 'job-1', 1, 'attacker', 'terminal_failure', :now, :now, :now)"
                ),
                {"now": now},
            )
    with pytest.raises(DBAPIError, match="job event history is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT OR REPLACE INTO job_events "
                    "(id, job_id, event_type, timestamp, metadata_json) "
                    "VALUES ('event-1', 'job-1', 'job.failed', :now, '{}')"
                ),
                {"now": now},
            )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT worker_id, status FROM job_attempts WHERE id='attempt-1'")
        ).one() == ("worker-1", "completed")
        assert connection.execute(
            text("SELECT event_type FROM job_events WHERE id='event-1'")
        ).scalar_one() == "job.created"
    engine.dispose()


def test_database_rejects_scene_variant_from_a_different_campaign(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    campaign_service = CampaignService.for_database(database_path)
    first = campaign_service.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    second_data = deepcopy(valid_campaign_data())
    second_data["campaignId"] = "second-campaign"
    for variant in second_data["sceneVariants"]:
        variant["variantId"] = f"second-{variant['variantId']}"
    second = campaign_service.create_campaign(CampaignCreate.model_validate(second_data))
    campaign_service.close()

    engine = create_engine(sqlite_url(database_path))
    now = "2026-08-10 12:00:00+00:00"
    with pytest.raises(DBAPIError, match="SceneVariant must belong to Campaign"):
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(
                text(
                    "INSERT INTO jobs "
                    "(id, job_type, campaign_id, scene_variant_id, status, priority, "
                    "idempotency_key, request_fingerprint, input_json, attempt_count, "
                    "max_attempts, retry_safety, created_at, updated_at, queued_at) "
                    "VALUES ('mismatched-job', 'fake.success', :campaign, :scene, 'queued', 0, "
                    "'mismatched-key', :fingerprint, '{}', 0, 3, 'idempotent', :now, :now, :now)"
                ),
                {
                    "campaign": first.campaign_id,
                    "scene": second.scene_variants[0].scene_variant_id,
                    "fingerprint": "b" * 64,
                    "now": now,
                },
            )

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, job_type, campaign_id, scene_variant_id, status, priority, "
                "idempotency_key, request_fingerprint, input_json, attempt_count, "
                "max_attempts, retry_safety, created_at, updated_at, queued_at) "
                "VALUES ('valid-scene-job', 'fake.success', :campaign, :scene, 'queued', 0, "
                "'valid-scene-key', :fingerprint, '{}', 0, 3, 'idempotent', :now, :now, :now)"
            ),
            {
                "campaign": first.campaign_id,
                "scene": first.scene_variants[0].scene_variant_id,
                "fingerprint": "d" * 64,
                "now": now,
            },
        )
    with pytest.raises(DBAPIError, match="referenced SceneVariant campaign is immutable"):
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(
                text("UPDATE scene_variants SET campaign_id=:campaign WHERE id=:scene"),
                {
                    "campaign": second.campaign_id,
                    "scene": first.scene_variants[0].scene_variant_id,
                },
            )
    engine.dispose()

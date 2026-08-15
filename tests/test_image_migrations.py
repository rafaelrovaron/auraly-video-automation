from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from auraly_pipeline.campaigns.persistence import migrate_database, sqlite_url


NOW = "2026-08-15T12:00:00+00:00"
SCENE_ID = "11111111-1111-4111-8111-111111111111"
SECOND_SCENE_ID = "22222222-2222-4222-8222-222222222222"


def _config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sqlite_url(database))
    return config


def _insert_campaign_scene_job(connection: Connection, *, job_id: str = "job-1") -> None:
    connection.execute(
        text(
            "INSERT INTO campaigns (id,character,proof_object,voice_preset,edit_preset,"
            "budget_json,config_json,status,created_at,updated_at) VALUES "
            "('campaign-1','character','proof','voice','edit','{}','{}','draft',:now,:now)"
        ),
        {"now": NOW},
    )
    connection.execute(
        text(
            "INSERT INTO scene_variants (id,campaign_id,variant_id,location,time_atmosphere,"
            "action,prompt,proof_object,status,created_at,updated_at) VALUES "
            "(:scene,'campaign-1','scene-1','studio',NULL,'pose','prompt',NULL,'not_started',"
            ":now,:now)"
        ),
        {"scene": SCENE_ID, "now": NOW},
    )
    connection.execute(
        text(
            "INSERT INTO jobs (id,job_type,campaign_id,scene_variant_id,status,priority,"
            "idempotency_key,request_fingerprint,input_json,attempt_count,max_attempts,"
            "retry_safety,created_at,updated_at,queued_at) VALUES "
            "(:job,'image.generate','campaign-1',:scene,'queued',0,:key,:sha,'{}',0,3,"
            "'idempotent',:now,:now,:now)"
        ),
        {
            "job": job_id,
            "scene": SCENE_ID,
            "key": f"image-{job_id}",
            "sha": "a" * 64,
            "now": NOW,
        },
    )


def _insert_generation(
    connection: Connection,
    *,
    generation_id: str,
    job_id: str,
    generation_number: int,
    campaign_id: str = "campaign-1",
    scene_variant_id: str = SCENE_ID,
) -> None:
    connection.execute(
        text(
            "INSERT INTO image_generations (id,campaign_id,scene_variant_id,job_id,"
            "generation_number,idempotency_key,request_fingerprint,prompt_snapshot,prompt_sha256,"
            "reference_image_path,reference_image_sha256,provider,executor,provider_state,"
            "created_at,updated_at) VALUES (:id,:campaign,:scene,:job,:number,:key,:fingerprint,"
            "'prompt',:prompt_sha,NULL,NULL,'google_flow','local_fake','queued',:now,:now)"
        ),
        {
            "id": generation_id,
            "campaign": campaign_id,
            "scene": scene_variant_id,
            "job": job_id,
            "number": generation_number,
            "key": f"generation-{generation_id}",
            "fingerprint": "b" * 64,
            "prompt_sha": "c" * 64,
            "now": NOW,
        },
    )


def _insert_candidate(
    connection: Connection,
    *,
    candidate_id: str,
    generation_id: str,
    candidate_index: int,
) -> None:
    connection.execute(
        text(
            "INSERT INTO image_candidates (id,image_generation_id,candidate_index,source_path,"
            "sha256,width,height,size_bytes,format,review_status,created_at,updated_at) VALUES "
            "(:id,:generation,:candidate_index,:path,:sha,16,16,128,'png','pending_review',:now,:now)"
        ),
        {
            "id": candidate_id,
            "generation": generation_id,
            "candidate_index": candidate_index,
            "path": f"campaigns/campaign-1/images/{candidate_id}.png",
            "sha": "d" * 64,
            "now": NOW,
        },
    )


def _insert_two_scene_approval_fixture(connection: Connection) -> None:
    _insert_campaign_scene_job(connection, job_id="job-1")
    connection.execute(
        text(
            "INSERT INTO scene_variants (id,campaign_id,variant_id,location,time_atmosphere,"
            "action,prompt,proof_object,status,created_at,updated_at) VALUES "
            "(:scene,'campaign-1','scene-2','studio',NULL,'pose','prompt',NULL,'not_started',"
            ":now,:now)"
        ),
        {"scene": SECOND_SCENE_ID, "now": NOW},
    )
    connection.execute(
        text(
            "INSERT INTO jobs (id,job_type,campaign_id,scene_variant_id,status,priority,"
            "idempotency_key,request_fingerprint,input_json,attempt_count,max_attempts,"
            "retry_safety,created_at,updated_at,queued_at) VALUES "
            "('job-2','image.generate','campaign-1',:scene,'queued',0,'image-job-2',:sha,"
            "'{}',0,3,'idempotent',:now,:now,:now)"
        ),
        {"scene": SECOND_SCENE_ID, "sha": "a" * 64, "now": NOW},
    )
    _insert_generation(
        connection, generation_id="generation-1", job_id="job-1", generation_number=1
    )
    _insert_generation(
        connection,
        generation_id="generation-2",
        job_id="job-2",
        generation_number=2,
        scene_variant_id=SECOND_SCENE_ID,
    )
    _insert_candidate(
        connection,
        candidate_id="candidate-1",
        generation_id="generation-1",
        candidate_index=0,
    )
    _insert_candidate(
        connection,
        candidate_id="candidate-2",
        generation_id="generation-2",
        candidate_index=1,
    )
    for candidate_id in ("candidate-1", "candidate-2"):
        connection.execute(
            text(
                "UPDATE image_candidates SET review_status='approved', approved_at=:now, "
                "approved_by='operator' WHERE id=:candidate_id"
            ),
            {"candidate_id": candidate_id, "now": NOW},
        )


def test_image_migration_upgrades_0003_database_and_creates_image_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "upgrade.db"
    config = _config(database)
    command.upgrade(config, "0003_voice_master")
    assert "image_generations" not in inspect(create_engine(sqlite_url(database))).get_table_names()

    command.upgrade(config, "head")

    engine = create_engine(sqlite_url(database))
    inspector = inspect(engine)
    assert {"image_generations", "image_candidates"}.issubset(inspector.get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0004_image_domain"
        )
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert {
        "enforce_image_generation_scene_campaign_insert",
        "enforce_image_generation_scene_campaign_update",
        "prevent_image_generation_intent_update",
        "prevent_image_candidate_artifact_update",
        "enforce_single_approved_image_candidate_insert",
        "enforce_single_approved_image_candidate_update",
    }.issubset(triggers)
    engine.dispose()


def test_fresh_database_reaches_image_domain_head(tmp_path: Path) -> None:
    database = tmp_path / "fresh" / "auraly.db"

    migrate_database(database)

    engine = create_engine(sqlite_url(database))
    assert {"image_generations", "image_candidates"}.issubset(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0004_image_domain"
        )
    engine.dispose()


def test_image_rows_enforce_generation_and_candidate_number_constraints(
    tmp_path: Path,
) -> None:
    database = tmp_path / "constraints.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        _insert_campaign_scene_job(connection)

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_generation(
                connection,
                generation_id="generation-invalid",
                job_id="job-1",
                generation_number=0,
            )

    with engine.begin() as connection:
        _insert_generation(
            connection,
            generation_id="generation-1",
            job_id="job-1",
            generation_number=1,
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_candidate(
                connection,
                candidate_id="candidate-invalid",
                generation_id="generation-1",
                candidate_index=-1,
            )
    engine.dispose()


def test_migration_trigger_rejects_campaign_scene_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "campaign-guard.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.begin() as connection:
        _insert_campaign_scene_job(connection)

    with pytest.raises(IntegrityError, match="SceneVariant must belong to Campaign"):
        with engine.begin() as connection:
            _insert_generation(
                connection,
                generation_id="generation-1",
                job_id="job-1",
                generation_number=1,
                campaign_id="different-campaign",
            )
    engine.dispose()


def test_migration_trigger_rejects_second_approved_candidate_for_scene_variant(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approval-guard.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.begin() as connection:
        _insert_campaign_scene_job(connection, job_id="job-1")
        connection.execute(
            text(
                "INSERT INTO jobs (id,job_type,campaign_id,scene_variant_id,status,priority,"
                "idempotency_key,request_fingerprint,input_json,attempt_count,max_attempts,"
                "retry_safety,created_at,updated_at,queued_at) VALUES "
                "('job-2','image.generate','campaign-1',:scene,'queued',0,'image-job-2',:sha,"
                "'{}',0,3,'idempotent',:now,:now,:now)"
            ),
            {"scene": SCENE_ID, "sha": "a" * 64, "now": NOW},
        )
        _insert_generation(
            connection, generation_id="generation-1", job_id="job-1", generation_number=1
        )
        _insert_generation(
            connection, generation_id="generation-2", job_id="job-2", generation_number=2
        )
        _insert_candidate(
            connection,
            candidate_id="candidate-1",
            generation_id="generation-1",
            candidate_index=0,
        )
        _insert_candidate(
            connection,
            candidate_id="candidate-2",
            generation_id="generation-2",
            candidate_index=0,
        )
        connection.execute(
            text(
                "UPDATE image_candidates SET review_status='approved', approved_at=:now, "
                "approved_by='operator' WHERE id='candidate-1'"
            ),
            {"now": NOW},
        )

    with pytest.raises(IntegrityError, match="approved candidate already exists"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE image_candidates SET review_status='approved', approved_at=:now, "
                    "approved_by='operator' WHERE id='candidate-2'"
                ),
                {"now": NOW},
            )
    engine.dispose()


def test_migration_prevents_reassigning_approved_candidate_to_another_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "candidate-ownership.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.begin() as connection:
        _insert_two_scene_approval_fixture(connection)

    with pytest.raises(IntegrityError, match="candidate artifact identity is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE image_candidates SET image_generation_id='generation-1' "
                    "WHERE id='candidate-2'"
                )
            )
    engine.dispose()


def test_migration_prevents_moving_generation_scene_identity_after_creation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "generation-ownership.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.begin() as connection:
        _insert_two_scene_approval_fixture(connection)

    with pytest.raises(IntegrityError, match="generation intent is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE image_generations SET scene_variant_id=:scene "
                    "WHERE id='generation-2'"
                ),
                {"scene": SCENE_ID},
            )
    engine.dispose()


def test_image_migration_downgrade_removes_image_tables(tmp_path: Path) -> None:
    database = tmp_path / "downgrade.db"
    config = _config(database)
    command.upgrade(config, "head")

    command.downgrade(config, "0003_voice_master")

    engine = create_engine(sqlite_url(database))
    assert not {"image_generations", "image_candidates"}.intersection(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0003_voice_master"
        )
    engine.dispose()

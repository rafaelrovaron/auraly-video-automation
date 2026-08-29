from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auraly_pipeline.images.db_models as image_db_models
from auraly_pipeline.campaigns.persistence import migrate_database, sqlite_url


NOW = "2026-08-15T12:00:00+00:00"

INVALID_RUN_CASES = (
    {"required_candidate_count": 1},
    {"required_resolution": "1K"},
    {"provider_workspace_path": "workspace/abc", "provider_workspace_fingerprint": None},
    {"grid_evidence_path": "inspection/grid.png", "grid_evidence_sha256": None},
    {"dispatch_intent_at": None, "dispatch_confirmed_at": NOW},
)

INVALID_SLOT_CASES = (
    {"slot_index": -1},
    {"slot_index": 2},
    {"state": "ingested", "image_candidate_id": None},
    {"state": "pending", "image_candidate_id": "candidate-1"},
)


def _database(tmp_path: Path):
    database = tmp_path / "auraly.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(
            text(
                "INSERT INTO campaigns (id,character,proof_object,voice_preset,edit_preset,"
                "budget_json,config_json,status,created_at,updated_at) VALUES "
                "('campaign-1','character','proof','voice','edit','{}','{}','draft',:now,:now)"
            ),
            {"now": NOW},
        )
        for scene_id, variant_id in (("scene-1", "scene-1"), ("scene-2", "scene-2")):
            connection.execute(
                text(
                    "INSERT INTO scene_variants (id,campaign_id,variant_id,location,time_atmosphere,"
                    "action,prompt,proof_object,status,created_at,updated_at) VALUES "
                    "(:scene,'campaign-1',:variant,'studio',NULL,'pose','prompt',NULL,'not_started',"
                    ":now,:now)"
                ),
                {"scene": scene_id, "variant": variant_id, "now": NOW},
            )
        for index, scene_id in enumerate(("scene-1", "scene-2"), start=1):
            connection.execute(
                text(
                    "INSERT INTO jobs (id,job_type,campaign_id,scene_variant_id,status,priority,"
                    "idempotency_key,request_fingerprint,input_json,attempt_count,max_attempts,"
                    "retry_safety,created_at,updated_at,queued_at) VALUES "
                    "(:job,'image.generate','campaign-1',:scene,'queued',0,:key,:sha,'{}',0,3,"
                    "'idempotent',:now,:now,:now)"
                ),
                {
                    "job": f"job-{index}",
                    "scene": scene_id,
                    "key": f"image-{index}",
                    "sha": "a" * 64,
                    "now": NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO image_generations (id,campaign_id,scene_variant_id,job_id,"
                    "generation_number,idempotency_key,request_fingerprint,prompt_snapshot,prompt_sha256,"
                    "reference_image_path,reference_image_sha256,provider,executor,provider_state,"
                    "created_at,updated_at) VALUES (:generation,'campaign-1',:scene,:job,:number,:key,"
                    ":fingerprint,'prompt',:prompt_sha,NULL,NULL,'google_flow','local_fake','queued',"
                    ":now,:now)"
                ),
                {
                    "generation": f"generation-{index}",
                    "scene": scene_id,
                    "job": f"job-{index}",
                    "number": index,
                    "key": f"generation-{index}",
                    "fingerprint": "b" * 64,
                    "prompt_sha": "c" * 64,
                    "now": NOW,
                },
            )
        connection.execute(
            text(
                "INSERT INTO image_candidates (id,image_generation_id,candidate_index,source_path,"
                "sha256,width,height,size_bytes,format,review_status,created_at,updated_at) VALUES "
                "('candidate-1','generation-1',0,'campaigns/campaign-1/images/candidate-1.png',"
                ":sha,16,16,128,'png','pending_review',:now,:now)"
            ),
            {"sha": "d" * 64, "now": NOW},
        )
    return engine


def _run_values(generation_id: str = "generation-1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "run-1",
        "image_generation_id": generation_id,
        "stage": "prepared",
        "required_candidate_count": 2,
        "required_resolution": "2K",
        "provider_workspace_path": None,
        "provider_workspace_fingerprint": None,
        "dispatch_attempt_number": 1,
        "dispatch_intent_at": None,
        "dispatch_confirmed_at": None,
        "grid_evidence_path": None,
        "grid_evidence_sha256": None,
        "last_failure_code": None,
        "provider_action_approved_by": "operator",
        "provider_action_approved_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def _insert_run(connection, **overrides: object) -> None:
    connection.execute(
        text(
            "INSERT INTO flow_generation_runs (id,image_generation_id,stage,required_candidate_count,"
            "required_resolution,provider_workspace_path,provider_workspace_fingerprint,"
            "dispatch_attempt_number,dispatch_intent_at,dispatch_confirmed_at,grid_evidence_path,"
            "grid_evidence_sha256,last_failure_code,provider_action_approved_by,"
            "provider_action_approved_at,created_at,updated_at) VALUES "
            "(:id,:image_generation_id,:stage,:required_candidate_count,:required_resolution,"
            ":provider_workspace_path,:provider_workspace_fingerprint,:dispatch_attempt_number,"
            ":dispatch_intent_at,:dispatch_confirmed_at,:grid_evidence_path,:grid_evidence_sha256,"
            ":last_failure_code,:provider_action_approved_by,:provider_action_approved_at,"
            ":created_at,:updated_at)"
        ),
        _run_values(**overrides),
    )


def _slot_values(run_id: str = "run-1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "slot-1",
        "flow_generation_run_id": run_id,
        "slot_index": 0,
        "provider_slot_fingerprint": None,
        "state": "pending",
        "download_intent_at": None,
        "staging_path": None,
        "staged_sha256": None,
        "image_candidate_id": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def _insert_slot(connection, **overrides: object) -> None:
    connection.execute(
        text(
            "INSERT INTO flow_candidate_slots (id,flow_generation_run_id,slot_index,"
            "provider_slot_fingerprint,state,download_intent_at,staging_path,staged_sha256,"
            "image_candidate_id,created_at,updated_at) VALUES "
            "(:id,:flow_generation_run_id,:slot_index,:provider_slot_fingerprint,:state,"
            ":download_intent_at,:staging_path,:staged_sha256,:image_candidate_id,:created_at,:updated_at)"
        ),
        _slot_values(**overrides),
    )


@pytest.mark.parametrize("overrides", INVALID_RUN_CASES)
def test_flow_run_constraints_reject_invalid_direct_inserts(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    engine = _database(tmp_path)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_run(connection, **overrides)
    engine.dispose()


@pytest.mark.parametrize("overrides", INVALID_SLOT_CASES)
def test_flow_slot_constraints_reject_invalid_direct_inserts(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    engine = _database(tmp_path)
    with engine.begin() as connection:
        _insert_run(connection)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_slot(connection, **overrides)
    engine.dispose()


def test_flow_run_constraints_reject_duplicate_generation_run(tmp_path: Path) -> None:
    engine = _database(tmp_path)
    with engine.begin() as connection:
        _insert_run(connection)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_run(connection, id="run-2")
    engine.dispose()


def test_flow_slot_constraints_reject_duplicate_index_and_candidate_link(tmp_path: Path) -> None:
    engine = _database(tmp_path)
    with engine.begin() as connection:
        _insert_run(connection)
        _insert_slot(connection, id="slot-1", state="ingested", image_candidate_id="candidate-1")
        _insert_run(connection, id="run-2", image_generation_id="generation-2")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_slot(connection, id="slot-2")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_slot(
                connection,
                id="slot-3",
                run_id="run-2",
                state="ingested",
                image_candidate_id="candidate-1",
            )
    engine.dispose()


def test_flow_slot_constraints_reject_candidate_from_another_generation(tmp_path: Path) -> None:
    engine = _database(tmp_path)
    with engine.begin() as connection:
        _insert_run(connection)
        _insert_run(connection, id="run-2", image_generation_id="generation-2")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_slot(
                connection,
                run_id="run-2",
                state="ingested",
                image_candidate_id="candidate-1",
            )
    engine.dispose()


def test_flow_run_row_loads_its_candidate_slots(tmp_path: Path) -> None:
    engine = _database(tmp_path)
    with engine.begin() as connection:
        _insert_run(connection)
        _insert_slot(connection, id="slot-1", slot_index=0)
        _insert_slot(connection, id="slot-2", slot_index=1)

    with Session(engine) as session:
        run = session.get(image_db_models.FlowGenerationRunRow, "run-1")
        assert run is not None
        assert {slot.id for slot in run.slots} == {"slot-1", "slot-2"}
    engine.dispose()

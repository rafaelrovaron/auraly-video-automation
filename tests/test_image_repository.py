from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import (
    create_sqlite_engine,
    migrate_database,
)
from auraly_pipeline.images.domain import (
    ImageCandidate,
    ImageCandidateReviewStatus,
    ImageGeneration,
    ImageGenerationState,
)
from auraly_pipeline.images.repository import ImageRepository


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
SCENE_ID = "11111111-1111-4111-8111-111111111111"


def _database(tmp_path: Path, *, job_ids: list[str]) -> tuple[Path, Engine]:
    database = tmp_path / "auraly.db"
    migrate_database(database)
    engine = create_sqlite_engine(database)
    with engine.begin() as connection:
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
        for index, job_id in enumerate(job_ids, start=1):
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
                    "key": f"image-job-{index}",
                    "sha": "a" * 64,
                    "now": NOW,
                },
            )
    return database, engine


def _sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, class_=Session)


def _generation(
    *,
    number: int,
    job_id: str,
    provider_state: ImageGenerationState = "queued",
    completed_at: datetime | None = None,
) -> ImageGeneration:
    return ImageGeneration(
        image_generation_id=str(uuid4()),
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        job_id=job_id,
        generation_number=number,
        idempotency_key=f"generation-{job_id}",
        request_fingerprint="b" * 64,
        prompt_snapshot="prompt",
        prompt_sha256="cf07194ee232eb531e15f690000d19846dea69cf05504782658afcfacb9228a2",
        provider="google_flow",
        executor="local_fake",
        provider_state=provider_state,
        created_at=NOW,
        updated_at=NOW,
        completed_at=completed_at,
    )


def _candidate(
    *,
    generation_id: str,
    index: int,
    review_status: ImageCandidateReviewStatus = "pending_review",
    superseded_by_candidate_id: str | None = None,
) -> ImageCandidate:
    return ImageCandidate(
        image_candidate_id=str(uuid4()),
        image_generation_id=generation_id,
        candidate_index=index,
        source_path=f"campaigns/campaign-1/images/candidate-{index:04d}.png",
        sha256=f"{index + 1:x}" * 64,
        width=16,
        height=16,
        size_bytes=128 + index,
        format="png",
        review_status=review_status,
        rejected_at=NOW if review_status == "rejected" else None,
        rejected_by="reviewer-1" if review_status == "rejected" else None,
        rejection_reason="Framing is too tight" if review_status == "rejected" else None,
        superseded_at=NOW if review_status == "superseded" else None,
        superseded_by_candidate_id=superseded_by_candidate_id,
        created_at=NOW,
        updated_at=NOW,
    )


def test_repository_lists_persisted_generations_and_candidates_in_numeric_order(
    tmp_path: Path,
) -> None:
    jobs = [str(uuid4()), str(uuid4())]
    _database_path, engine = _database(tmp_path, job_ids=jobs)
    sessions = _sessions(engine)
    repository = ImageRepository(sessions)
    generation_two = _generation(number=2, job_id=jobs[0])
    generation_one = _generation(number=1, job_id=jobs[1])
    candidate_one = _candidate(generation_id=generation_one.image_generation_id, index=1)
    candidate_zero = _candidate(generation_id=generation_one.image_generation_id, index=0)
    with sessions() as session:
        repository.create_generation_in_session(session, generation_two)
        repository.create_generation_in_session(session, generation_one)
        repository.create_candidate_in_session(session, candidate_one)
        repository.create_candidate_in_session(session, candidate_zero)
        session.commit()

    assert [item.generation_number for item in repository.list_generations(SCENE_ID)] == [1, 2]
    assert [
        item.candidate_index
        for item in repository.list_candidates(generation_one.image_generation_id)
    ] == [0, 1]
    persisted_generation = repository.get_generation(generation_one.image_generation_id)
    persisted_candidate = repository.get_candidate(candidate_zero.image_candidate_id)
    assert persisted_generation is not None
    assert persisted_generation.id == generation_one.image_generation_id
    assert persisted_candidate is not None
    assert persisted_candidate.id == candidate_zero.image_candidate_id
    engine.dispose()


def test_two_immediate_transactions_allocate_distinct_generation_numbers(
    tmp_path: Path,
) -> None:
    jobs = [str(uuid4()), str(uuid4())]
    _database_path, engine = _database(tmp_path, job_ids=jobs)
    sessions = _sessions(engine)
    repository = ImageRepository(sessions)

    with sessions() as first_session:
        first_session.execute(text("BEGIN IMMEDIATE"))
        first_number = repository.allocate_generation_number(first_session, SCENE_ID)
        repository.create_generation_in_session(
            first_session, _generation(number=first_number, job_id=jobs[0])
        )
        first_session.commit()
    with sessions() as second_session:
        second_session.execute(text("BEGIN IMMEDIATE"))
        second_number = repository.allocate_generation_number(second_session, SCENE_ID)
        repository.create_generation_in_session(
            second_session, _generation(number=second_number, job_id=jobs[1])
        )
        second_session.commit()

    assert (first_number, second_number) == (1, 2)
    engine.dispose()


def test_repository_preserves_zero_candidate_generation_and_history_after_restart(
    tmp_path: Path,
) -> None:
    jobs = [str(uuid4()), str(uuid4()), str(uuid4())]
    database, engine = _database(tmp_path, job_ids=jobs)
    sessions = _sessions(engine)
    repository = ImageRepository(sessions)
    completed = _generation(
        number=1, job_id=jobs[0], provider_state="completed", completed_at=NOW
    )
    rejected = _generation(number=2, job_id=jobs[1], provider_state="completed")
    superseded = _generation(number=3, job_id=jobs[2], provider_state="completed")
    rejected_candidate = _candidate(
        generation_id=rejected.image_generation_id, index=0, review_status="rejected"
    )
    superseded_candidate = _candidate(
        generation_id=superseded.image_generation_id,
        index=0,
        review_status="superseded",
        superseded_by_candidate_id=rejected_candidate.image_candidate_id,
    )
    with sessions() as session:
        repository.create_generation_in_session(session, completed)
        repository.create_generation_in_session(session, rejected)
        repository.create_generation_in_session(session, superseded)
        repository.create_candidate_in_session(session, rejected_candidate)
        repository.create_candidate_in_session(session, superseded_candidate)
        session.commit()
    engine.dispose()

    restarted_engine = create_sqlite_engine(database)
    restarted = ImageRepository(_sessions(restarted_engine))

    assert [item.id for item in restarted.list_generations(SCENE_ID)] == [
        completed.image_generation_id,
        rejected.image_generation_id,
        superseded.image_generation_id,
    ]
    assert restarted.list_candidates(completed.image_generation_id) == []
    restarted_completed = restarted.get_generation(completed.image_generation_id)
    assert restarted_completed is not None
    assert restarted_completed.completed_at == NOW.replace(tzinfo=None)
    restarted_rejected = restarted.list_candidates(rejected.image_generation_id)
    restarted_superseded = restarted.list_candidates(superseded.image_generation_id)
    assert [(item.id, item.review_status, item.rejected_by) for item in restarted_rejected] == [
        (rejected_candidate.image_candidate_id, "rejected", "reviewer-1")
    ]
    assert [
        (item.id, item.review_status, item.superseded_by_candidate_id)
        for item in restarted_superseded
    ] == [(superseded_candidate.image_candidate_id, "superseded", rejected_candidate.image_candidate_id)]
    restarted_engine.dispose()

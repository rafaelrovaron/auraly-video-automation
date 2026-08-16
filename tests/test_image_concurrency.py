from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images.domain import ImageCandidate, ImageGenerateRequest
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.images.service import (
    ImageApprovedCandidateExistsError,
    ImageService,
)
from auraly_pipeline.jobs.service import JobService
from tests.test_campaign_domain import valid_campaign_data


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _campaign(database: Path) -> tuple[str, str]:
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    return campaign.campaign_id, campaign.scene_variants[0].scene_variant_id


def _service(database: Path, *, work_root: Path | None = None) -> ImageService:
    return ImageService.for_database(database, clock=lambda: NOW, work_root=work_root)


def _request(campaign_id: str, scene_variant_id: str, key: str) -> ImageGenerateRequest:
    return ImageGenerateRequest(
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        idempotency_key=key,
        prompt_snapshot="A moonlit studio",
    )


def _add_candidate(database: Path, generation_id: str, index: int) -> ImageCandidate:
    candidate = ImageCandidate(
        image_candidate_id=str(uuid4()),
        image_generation_id=generation_id,
        candidate_index=index,
        source_path=f"campaigns/test/images/candidate-{index:04d}.png",
        sha256=f"{index + 1:x}" * 64,
        width=16,
        height=16,
        size_bytes=128 + index,
        format="png",
        review_status="pending_review",
        created_at=NOW,
        updated_at=NOW,
    )
    engine = create_sqlite_engine(database)
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        ImageRepository.create_candidate_in_session(session, candidate)
        session.commit()
    engine.dispose()
    return candidate


def test_concurrent_new_keys_allocate_unique_generation_numbers_for_one_scene_variant(
    tmp_path: Path,
) -> None:
    database = tmp_path / "generation-race.db"
    campaign_id, scene_variant_id = _campaign(database)
    first = _service(database)
    second = _service(database)
    barrier = Barrier(2)

    def submit(service_and_key: tuple[ImageService, str]) -> tuple[int, str, str]:
        service, key = service_and_key
        barrier.wait()
        submitted = service.generate(_request(campaign_id, scene_variant_id, key))
        return (
            submitted.generation.generation_number,
            submitted.generation.image_generation_id,
            submitted.job.job_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        submissions = list(executor.map(submit, [(first, "race-one"), (second, "race-two")]))

    assert sorted(item[0] for item in submissions) == [1, 2]
    assert len({item[1] for item in submissions}) == 2
    assert len({item[2] for item in submissions}) == 2
    assert [item.generation_number for item in first.list_generations(scene_variant_id)] == [1, 2]
    jobs = JobService.for_database(database)
    persisted_jobs = jobs.list_jobs()
    assert len(persisted_jobs) == 2
    assert {job.job_id for job in persisted_jobs} == {item[2] for item in submissions}
    jobs.close()
    first.close()
    second.close()


def test_same_idempotency_key_race_returns_one_generation_and_one_job(tmp_path: Path) -> None:
    database = tmp_path / "idempotency-race.db"
    campaign_id, scene_variant_id = _campaign(database)
    first = _service(database)
    second = _service(database)
    barrier = Barrier(2)

    def submit(service: ImageService) -> tuple[str, str, bool]:
        barrier.wait()
        submission = service.generate(_request(campaign_id, scene_variant_id, "same-key-race"))
        return submission.generation.image_generation_id, submission.job.job_id, submission.reused

    with ThreadPoolExecutor(max_workers=2) as executor:
        submissions = list(executor.map(submit, [first, second]))

    assert {item[0] for item in submissions} == {submissions[0][0]}
    assert {item[1] for item in submissions} == {submissions[0][1]}
    assert sorted(item[2] for item in submissions) == [False, True]
    assert len(first.list_generations(scene_variant_id)) == 1
    jobs = JobService.for_database(database)
    assert len(jobs.list_jobs()) == 1
    jobs.close()
    first.close()
    second.close()


def test_concurrent_candidate_approval_leaves_exactly_one_approved_candidate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approval-race.db"
    campaign_id, scene_variant_id = _campaign(database)
    setup = _service(database)
    first_candidate = _add_candidate(
        database,
        setup.generate(_request(campaign_id, scene_variant_id, "approval-one")).generation.image_generation_id,
        0,
    )
    second_candidate = _add_candidate(
        database,
        setup.generate(_request(campaign_id, scene_variant_id, "approval-two")).generation.image_generation_id,
        0,
    )
    setup.close()
    first = _service(database)
    second = _service(database)
    barrier = Barrier(2)

    def approve(service_and_candidate: tuple[ImageService, str]) -> tuple[str, str | None]:
        service, candidate_id = service_and_candidate
        barrier.wait()
        try:
            return service.approve_candidate(candidate_id, "reviewer-1").image_candidate_id, None
        except ImageApprovedCandidateExistsError as exc:
            return "", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                approve,
                [(first, first_candidate.image_candidate_id), (second, second_candidate.image_candidate_id)],
            )
        )

    approved = [candidate_id for candidate_id, error in outcomes if error is None]
    errors = [error for _candidate_id, error in outcomes if error is not None]
    assert len(approved) == 1
    assert errors == [ImageApprovedCandidateExistsError.public_message]
    assert "approved candidate already exists" not in errors[0]
    statuses = [
        first.get_candidate(candidate_id).review_status
        for candidate_id in (first_candidate.image_candidate_id, second_candidate.image_candidate_id)
    ]
    assert statuses.count("approved") == 1
    assert statuses.count("pending_review") == 1
    first.close()
    second.close()


@pytest.mark.parametrize(
    "reference_path",
    ["../outside/reference.png", r"..\outside\reference.png", r"C:\outside\reference.png"],
)
def test_image_artifact_path_rejects_traversal(reference_path: str, tmp_path: Path) -> None:
    database = tmp_path / "artifact-path.db"
    outside_root = tmp_path / "outside"
    campaign_id, scene_variant_id = _campaign(database)
    outside_root.mkdir()

    with pytest.raises(ValidationError, match="safe workspace-relative path"):
        ImageGenerateRequest(
            campaign_id=campaign_id,
            scene_variant_id=scene_variant_id,
            idempotency_key="traversal-reference",
            prompt_snapshot="A moonlit studio",
            reference_image_path=reference_path,
            reference_image_sha256="a" * 64,
        )
    assert list(outside_root.iterdir()) == []


def test_image_artifact_path_rejects_symlink_escape(tmp_path: Path) -> None:
    database = tmp_path / "artifact-path.db"
    work_root = tmp_path / "work"
    outside_root = tmp_path / "outside"
    campaign_id, scene_variant_id = _campaign(database)
    images = _service(database, work_root=work_root)
    generation = images.generate(_request(campaign_id, scene_variant_id, "symlink-escape"))
    images.close()
    escaped_generation_root = (
        work_root
        / "campaigns"
        / campaign_id
        / "images"
        / scene_variant_id
        / "generation-0001"
    )
    escaped_generation_root.parent.mkdir(parents=True)
    outside_root.mkdir()
    try:
        escaped_generation_root.symlink_to(outside_root, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows test account lacks the symlink creation privilege")
        raise

    jobs = JobService.for_database(database, work_root=work_root)
    completed = jobs.worker_once("artifact-path-worker")

    assert completed is not None
    assert completed.status == "failed"
    assert completed.last_error_code == "image_artifact_conflict"
    assert list(outside_root.iterdir()) == []
    jobs.close()
    restarted = _service(database, work_root=work_root)
    assert restarted.get_generation(generation.generation.image_generation_id).provider_state == "failed"
    restarted.close()

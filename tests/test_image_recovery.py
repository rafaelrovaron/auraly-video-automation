from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images.domain import ImageCandidate, ImageGenerateRequest
from auraly_pipeline.images.db_models import ImageGenerationRow
from auraly_pipeline.images.handler import deterministic_png_bytes
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.images.service import ImageService
from auraly_pipeline.jobs.service import JobService
from tests.test_campaign_domain import valid_campaign_data


RECOVERY_NOW = datetime(2026, 8, 16, 15, 0, tzinfo=UTC)


def _campaign(database: Path) -> tuple[str, str]:
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    return campaign.campaign_id, campaign.scene_variants[0].scene_variant_id


def _submit_generation(
    database: Path,
    work_root: Path,
    *,
    campaign_id: str,
    scene_variant_id: str,
    key: str,
) -> tuple[str, int]:
    images = ImageService.for_database(database, work_root=work_root)
    submitted = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign_id,
            scene_variant_id=scene_variant_id,
            idempotency_key=key,
            prompt_snapshot="A moonlit studio",
        )
    )
    images.close()
    return submitted.generation.image_generation_id, submitted.generation.generation_number


def _candidate_path(
    work_root: Path,
    campaign_id: str,
    scene_variant_id: str,
    generation_number: int,
    candidate_index: int,
) -> Path:
    return (
        work_root
        / "campaigns"
        / campaign_id
        / "images"
        / scene_variant_id
        / f"generation-{generation_number:04d}"
        / f"candidate-{candidate_index:04d}.png"
    )


def _record_candidate(
    database: Path,
    *,
    generation_id: str,
    candidate_index: int,
    source_path: Path,
    data: bytes,
) -> None:
    engine = create_sqlite_engine(database)
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    timestamp = datetime.now(UTC)
    with sessions() as session:
        ImageRepository.create_candidate_in_session(
            session,
            ImageCandidate(
                image_candidate_id=str(uuid4()),
                image_generation_id=generation_id,
                candidate_index=candidate_index,
                source_path=source_path.as_posix(),
                sha256=hashlib.sha256(data).hexdigest(),
                width=8,
                height=8,
                size_bytes=len(data),
                format="png",
                review_status="pending_review",
                created_at=timestamp,
                updated_at=timestamp,
            ),
        )
        session.commit()
    engine.dispose()


def _set_generation_state(database: Path, generation_id: str, state: str) -> None:
    engine = create_sqlite_engine(database)
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        generation = session.get(ImageGenerationRow, generation_id)
        assert generation is not None
        generation.provider_state = state
        session.commit()
    engine.dispose()


def _run_image_job(database: Path, work_root: Path):
    jobs = JobService.for_database(database, work_root=work_root)
    completed = jobs.worker_once("image-recovery")
    assert completed is not None
    jobs.close()
    return completed


def test_retry_reuses_valid_candidate_zero_and_creates_only_candidate_one(tmp_path: Path) -> None:
    database = tmp_path / "retry.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    generation_id, generation_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-recovery-retry",
    )
    expected_zero = deterministic_png_bytes(generation_id, 0)
    zero_path = _candidate_path(
        work_root, campaign_id, scene_variant_id, generation_number, candidate_index=0
    )
    zero_path.parent.mkdir(parents=True)
    zero_path.write_bytes(expected_zero)
    original_modified_at = zero_path.stat().st_mtime_ns
    _record_candidate(
        database,
        generation_id=generation_id,
        candidate_index=0,
        source_path=zero_path.relative_to(work_root),
        data=expected_zero,
    )
    _set_generation_state(database, generation_id, "generating")

    completed = _run_image_job(database, work_root)

    assert completed.status == "completed"
    assert zero_path.read_bytes() == expected_zero
    assert zero_path.stat().st_mtime_ns == original_modified_at
    images = ImageService.for_database(database, work_root=work_root)
    candidates = images.list_candidates(generation_id)
    assert [candidate.candidate_index for candidate in candidates] == [0, 1]
    assert (work_root / candidates[1].source_path).read_bytes() == deterministic_png_bytes(
        generation_id, 1
    )
    images.close()


def test_matching_orphan_file_is_reconciled_without_overwrite(tmp_path: Path) -> None:
    database = tmp_path / "orphan.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    generation_id, generation_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-recovery-orphan",
    )
    expected_zero = deterministic_png_bytes(generation_id, 0)
    zero_path = _candidate_path(
        work_root, campaign_id, scene_variant_id, generation_number, candidate_index=0
    )
    zero_path.parent.mkdir(parents=True)
    zero_path.write_bytes(expected_zero)
    original_modified_at = zero_path.stat().st_mtime_ns

    completed = _run_image_job(database, work_root)

    assert completed.status == "completed"
    assert zero_path.read_bytes() == expected_zero
    assert zero_path.stat().st_mtime_ns == original_modified_at
    images = ImageService.for_database(database, work_root=work_root)
    candidates = images.list_candidates(generation_id)
    assert [candidate.candidate_index for candidate in candidates] == [0, 1]
    assert candidates[0].sha256 == hashlib.sha256(expected_zero).hexdigest()
    images.close()


def test_candidate_row_without_file_blocks_job_and_generation(tmp_path: Path) -> None:
    database = tmp_path / "missing.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    generation_id, generation_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-recovery-missing",
    )
    expected_zero = deterministic_png_bytes(generation_id, 0)
    zero_path = _candidate_path(
        work_root, campaign_id, scene_variant_id, generation_number, candidate_index=0
    )
    _record_candidate(
        database,
        generation_id=generation_id,
        candidate_index=0,
        source_path=zero_path.relative_to(work_root),
        data=expected_zero,
    )
    images = ImageService.for_database(database, work_root=work_root)
    candidates_before = images.list_candidates(generation_id)
    assert len(candidates_before) == 1
    candidate_before = candidates_before[0]
    images.close()

    blocked = _run_image_job(database, work_root)

    assert blocked.status == "blocked"
    assert blocked.last_error_code == "image_artifact_missing"
    assert not zero_path.exists()
    images = ImageService.for_database(database, work_root=work_root)
    assert images.get_generation(generation_id).provider_state == "blocked"
    candidates_after = images.list_candidates(generation_id)
    assert len(candidates_after) == 1
    candidate_after = candidates_after[0]
    assert (
        candidate_after.image_candidate_id,
        candidate_after.candidate_index,
        candidate_after.source_path,
        candidate_after.sha256,
        candidate_after.width,
        candidate_after.height,
        candidate_after.format,
        candidate_after.size_bytes,
    ) == (
        candidate_before.image_candidate_id,
        candidate_before.candidate_index,
        candidate_before.source_path,
        candidate_before.sha256,
        candidate_before.width,
        candidate_before.height,
        candidate_before.format,
        candidate_before.size_bytes,
    )
    images.close()

    restarted = ImageService.for_database(database, work_root=work_root)
    assert restarted.get_generation(generation_id).provider_state == "blocked"
    restarted_candidate = restarted.get_candidate(candidate_before.image_candidate_id)
    assert restarted_candidate.source_path == candidate_before.source_path
    assert restarted_candidate.sha256 == candidate_before.sha256
    restarted.close()


def test_unexpected_existing_file_blocks_without_overwrite(tmp_path: Path) -> None:
    database = tmp_path / "conflict.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    generation_id, generation_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-recovery-conflict",
    )
    zero_path = _candidate_path(
        work_root, campaign_id, scene_variant_id, generation_number, candidate_index=0
    )
    unexpected = b"unexpected pre-existing artifact"
    zero_path.parent.mkdir(parents=True)
    zero_path.write_bytes(unexpected)

    blocked = _run_image_job(database, work_root)

    assert blocked.status == "blocked"
    assert blocked.last_error_code == "image_artifact_conflict"
    assert zero_path.read_bytes() == unexpected
    images = ImageService.for_database(database, work_root=work_root)
    assert images.get_generation(generation_id).provider_state == "blocked"
    images.close()


def test_stale_image_job_recovery_race_preserves_one_generation_and_job(tmp_path: Path) -> None:
    database = tmp_path / "stale-image-recovery.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    initial = ImageService.for_database(
        database, clock=lambda: RECOVERY_NOW, work_root=work_root
    )
    submission = initial.generate(
        ImageGenerateRequest(
            campaign_id=campaign_id,
            scene_variant_id=scene_variant_id,
            idempotency_key="stale-image-recovery",
            prompt_snapshot="A moonlit studio",
        )
    )
    claimer = JobService.for_database(database, clock=lambda: RECOVERY_NOW, work_root=work_root)
    claimed = claimer.claim_next_job("crashed-image-worker", lease_seconds=10)
    assert claimed is not None
    assert claimed.job_id == submission.job.job_id
    claimer.close()
    initial.close()

    expired = RECOVERY_NOW + timedelta(seconds=11)
    first_images = ImageService.for_database(database, clock=lambda: expired, work_root=work_root)
    second_images = ImageService.for_database(database, clock=lambda: expired, work_root=work_root)
    first = JobService.for_database(database, clock=lambda: expired, work_root=work_root)
    second = JobService.for_database(database, clock=lambda: expired, work_root=work_root)
    barrier = Barrier(2)

    def recover(jobs: JobService):
        barrier.wait()
        return jobs.recover_stale_jobs()

    with ThreadPoolExecutor(max_workers=2) as executor:
        recovered = list(executor.map(recover, [first, second]))

    assert sorted(len(items) for items in recovered) == [0, 1]
    assert first.recover_stale_jobs() == []
    persisted_jobs = first.list_jobs()
    assert [job.job_id for job in persisted_jobs] == [submission.job.job_id]
    generation = first_images.get_generation(submission.generation.image_generation_id)
    assert generation.job_id == submission.job.job_id
    assert second_images.list_generations(scene_variant_id) == [generation]
    assert [event.event_type for event in persisted_jobs[0].events].count("job.recovered") == 1
    assert first_images.list_candidates(generation.image_generation_id) == []

    resumed = JobService.for_database(database, clock=lambda: expired, work_root=work_root)
    completed = resumed.worker_once("resumed-image-worker")
    assert completed is not None
    assert completed.status == "completed"
    assert resumed.worker_once("resumed-image-worker") is None
    resumed.close()
    restarted = ImageService.for_database(database, clock=lambda: expired, work_root=work_root)
    assert [candidate.candidate_index for candidate in restarted.list_candidates(generation.image_generation_id)] == [
        0,
        1,
    ]
    restarted.close()
    first.close()
    second.close()
    first_images.close()
    second_images.close()

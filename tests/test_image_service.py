from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images.domain import ImageCandidate, ImageGenerateRequest
from auraly_pipeline.images.db_models import ImageGenerationRow
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.images.service import (
    ImageCandidateNotFoundError,
    ImageError,
    ImageGenerationNotFoundError,
    ImageIdempotencyConflictError,
    ImageService,
)
from auraly_pipeline.jobs.domain import JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.handlers import JobExecutionContext
from tests.test_campaign_domain import valid_campaign_data


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class SubmissionOnlyImageHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        raise AssertionError(f"Task 5 must not execute image job {context.job_id}")


def _campaign(database: Path) -> tuple[str, str]:
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    return campaign.campaign_id, campaign.scene_variants[0].scene_variant_id


def _service(database: Path) -> ImageService:
    return ImageService.for_database(
        database,
        clock=lambda: NOW,
        handlers={"image.generate": SubmissionOnlyImageHandler()},
    )


def _request(
    campaign_id: str,
    scene_variant_id: str,
    *,
    key: str,
    prompt: str = "A moonlit studio",
) -> ImageGenerateRequest:
    return ImageGenerateRequest(
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        idempotency_key=key,
        prompt_snapshot=prompt,
    )


def _playwright_request(
    campaign_id: str,
    scene_variant_id: str,
    *,
    key: str,
) -> ImageGenerateRequest:
    return ImageGenerateRequest(
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        idempotency_key=key,
        prompt_snapshot="A moonlit studio",
        reference_image_path="references/avatar.png",
        reference_image_sha256="a" * 64,
        executor="playwright_python",
        generation_contract_version="flow-generation-v1",
        provider_action_confirmed=True,
        provider_action_approved_by="operator-1",
    )


def test_generate_creates_one_linked_generation_and_queued_job(tmp_path: Path) -> None:
    database = tmp_path / "generate.db"
    campaign_id, scene_variant_id = _campaign(database)
    service = _service(database)

    submitted = service.generate(
        _request(campaign_id, scene_variant_id, key="image-generate-1")
    )

    assert submitted.reused is False
    assert submitted.generation.campaign_id == submitted.job.campaign_id == campaign_id
    assert submitted.generation.scene_variant_id == submitted.job.scene_variant_id == scene_variant_id
    assert submitted.generation.job_id == submitted.job.job_id
    assert submitted.generation.generation_number == 1
    assert submitted.generation.provider_state == "queued"
    assert submitted.job.status == "queued"
    assert [event.event_type for event in submitted.job.events] == ["job.created", "job.queued"]
    assert service.get_generation(submitted.generation.image_generation_id) == submitted.generation
    service.close()


def test_generate_same_key_and_fingerprint_returns_original_generation_and_job(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reuse.db"
    campaign_id, scene_variant_id = _campaign(database)
    service = _service(database)
    request = _request(campaign_id, scene_variant_id, key="image-reuse")

    first = service.generate(request)
    reused = service.generate(request)

    assert reused.reused is True
    assert reused.generation == first.generation
    assert reused.job == first.job
    assert len(service.list_generations(scene_variant_id)) == 1
    service.close()


def test_generate_same_key_and_changed_fingerprint_raises_image_idempotency_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conflict.db"
    campaign_id, scene_variant_id = _campaign(database)
    service = _service(database)
    service.generate(_request(campaign_id, scene_variant_id, key="image-conflict"))

    with pytest.raises(ImageIdempotencyConflictError) as raised:
        service.generate(
            _request(
                campaign_id,
                scene_variant_id,
                key="image-conflict",
                prompt="A changed prompt",
            )
        )

    assert raised.value.code == "image_idempotency_conflict"
    assert raised.value.public_message == (
        "The idempotency key is already used by a different image generation request."
    )
    assert len(service.list_generations(scene_variant_id)) == 1
    service.close()


def test_generate_rejects_playwright_before_persistence_until_flow_submission_is_atomic(
    tmp_path: Path,
) -> None:
    database = tmp_path / "playwright-rejected.db"
    campaign_id, scene_variant_id = _campaign(database)
    service = _service(database)
    request = _playwright_request(campaign_id, scene_variant_id, key="flow-not-ready")

    with pytest.raises(ImageError) as raised:
        service.generate(request)

    assert raised.value.code == "image_operation_failed"
    assert raised.value.public_message == "The image operation failed safely."
    engine = create_sqlite_engine(database)
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == 0
        assert session.scalar(select(func.count()).select_from(ImageGenerationRow)) == 0
    engine.dispose()

    fake = service.generate(_request(campaign_id, scene_variant_id, key="flow-not-ready"))
    assert fake.reused is False
    service.close()


def test_regenerate_same_prompt_with_new_key_creates_next_generation_number(
    tmp_path: Path,
) -> None:
    database = tmp_path / "regenerate.db"
    campaign_id, scene_variant_id = _campaign(database)
    service = _service(database)
    first_request = _request(campaign_id, scene_variant_id, key="image-first")
    first = service.generate(first_request)

    regenerated = service.regenerate(
        _request(campaign_id, scene_variant_id, key="image-second")
    )

    assert regenerated.reused is False
    assert regenerated.generation.generation_number == 2
    assert regenerated.generation.image_generation_id != first.generation.image_generation_id
    assert regenerated.job.job_id != first.job.job_id
    assert regenerated.generation.prompt_sha256 == first.generation.prompt_sha256
    assert [item.generation_number for item in service.list_generations(scene_variant_id)] == [1, 2]
    with pytest.raises(ImageIdempotencyConflictError):
        service.regenerate(first_request)
    service.close()


def test_get_and_list_candidate_contracts_and_not_found_errors(tmp_path: Path) -> None:
    database = tmp_path / "reads.db"
    campaign_id, scene_variant_id = _campaign(database)
    service = _service(database)
    submitted = service.generate(_request(campaign_id, scene_variant_id, key="image-reads"))
    candidate = ImageCandidate(
        image_candidate_id="44444444-4444-4444-8444-444444444444",
        image_generation_id=submitted.generation.image_generation_id,
        candidate_index=0,
        source_path="campaigns/test/images/candidate-0000.png",
        sha256="a" * 64,
        width=16,
        height=16,
        size_bytes=128,
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

    assert service.get_candidate(candidate.image_candidate_id) == candidate
    assert service.list_candidates(submitted.generation.image_generation_id) == [candidate]
    with pytest.raises(ImageGenerationNotFoundError) as generation_error:
        service.get_generation("55555555-5555-4555-8555-555555555555")
    with pytest.raises(ImageCandidateNotFoundError) as candidate_error:
        service.get_candidate("66666666-6666-4666-8666-666666666666")
    assert generation_error.value.code == "image_generation_not_found"
    assert candidate_error.value.code == "image_candidate_not_found"
    service.close()


def test_service_restart_preserves_completed_generation_reads(tmp_path: Path) -> None:
    database = tmp_path / "restart-completed.db"
    campaign_id, scene_variant_id = _campaign(database)
    service = _service(database)
    submitted = service.generate(_request(campaign_id, scene_variant_id, key="restart-completed"))
    generation_id = submitted.generation.image_generation_id
    service.close()

    engine = create_sqlite_engine(database)
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        generation = session.get(ImageGenerationRow, generation_id)
        assert generation is not None
        generation.provider_state = "completed"
        generation.completed_at = NOW
        session.commit()
    engine.dispose()

    restarted = _service(database)
    restored = restarted.get_generation(generation_id)

    assert restored.provider_state == "completed"
    assert restored.completed_at == NOW
    restarted.close()

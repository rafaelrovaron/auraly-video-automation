from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images.domain import ImageCandidate, ImageGenerateRequest
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.images.service import (
    ImageApprovedCandidateExistsError,
    ImageCandidateNotFoundError,
    ImageCandidateSceneMismatchError,
    ImageService,
    ImageTransitionError,
)
from tests.test_campaign_domain import valid_campaign_data


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _campaign(database: Path) -> tuple[str, list[str]]:
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    return campaign.campaign_id, [item.scene_variant_id for item in campaign.scene_variants]


def _service(
    database: Path, *, clock: Callable[[], datetime] | None = None
) -> ImageService:
    return ImageService.for_database(database, clock=clock or (lambda: NOW))


def _generation(
    service: ImageService, campaign_id: str, scene_variant_id: str, *, key: str
) -> str:
    return service.generate(
        ImageGenerateRequest(
            campaign_id=campaign_id,
            scene_variant_id=scene_variant_id,
            idempotency_key=key,
            prompt_snapshot="A moonlit studio",
        )
    ).generation.image_generation_id


def _add_candidate(
    database: Path, image_generation_id: str, *, index: int, sha256: str | None = None
) -> ImageCandidate:
    candidate = ImageCandidate(
        image_candidate_id=str(uuid4()),
        image_generation_id=image_generation_id,
        candidate_index=index,
        source_path=f"campaigns/test/images/candidate-{index:04d}.png",
        sha256=sha256 or f"{index + 1:x}" * 64,
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


def _add_database_valid_domain_invalid_candidate(
    database: Path,
    image_generation_id: str,
    *,
    review_status: str,
) -> str:
    candidate_id = str(uuid4())
    engine = create_sqlite_engine(database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO image_candidates "
                "(id,image_generation_id,candidate_index,source_path,sha256,width,height,"
                "size_bytes,format,review_status,approved_at,approved_by,created_at,updated_at) "
                "VALUES (:id,:generation,0,:source,:sha,16,16,128,'png',:status,"
                ":approved_at,:approved_by,:now,:now)"
            ),
            {
                "id": candidate_id,
                "generation": image_generation_id,
                "source": r"C:\\outside\\candidate.png",
                "sha": "a" * 64,
                "status": review_status,
                "approved_at": NOW if review_status == "approved" else None,
                "approved_by": "reviewer-1" if review_status == "approved" else None,
                "now": NOW,
            },
        )
    engine.dispose()
    return candidate_id


def _raw_review_status(database: Path, candidate_id: str) -> str:
    engine = create_sqlite_engine(database)
    with engine.connect() as connection:
        review_status = connection.execute(
            text("SELECT review_status FROM image_candidates WHERE id=:id"),
            {"id": candidate_id},
        ).scalar_one()
    engine.dispose()
    return str(review_status)


def test_first_pending_candidate_approval_succeeds_and_second_direct_approval_conflicts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approval.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    first = _add_candidate(database, _generation(service, campaign_id, scenes[0], key="first"), index=0)
    second = _add_candidate(database, _generation(service, campaign_id, scenes[0], key="second"), index=0)

    approved = service.approve_candidate(first.image_candidate_id, "reviewer-1")

    assert approved.review_status == "approved"
    assert approved.approved_at == NOW
    assert approved.approved_by == "reviewer-1"
    with pytest.raises(ImageApprovedCandidateExistsError):
        service.approve_candidate(second.image_candidate_id, "reviewer-2")
    assert service.get_candidate(second.image_candidate_id).review_status == "pending_review"
    service.close()


def test_replace_approved_candidate_atomically_supersedes_old_and_approves_new(
    tmp_path: Path,
) -> None:
    database = tmp_path / "replacement.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    old = _add_candidate(database, _generation(service, campaign_id, scenes[0], key="old"), index=0)
    new = _add_candidate(database, _generation(service, campaign_id, scenes[0], key="new"), index=0)
    service.approve_candidate(old.image_candidate_id, "reviewer-1")

    approved = service.replace_approved_candidate(scenes[0], new.image_candidate_id, "reviewer-2")

    superseded = service.get_candidate(old.image_candidate_id)
    assert approved.review_status == "approved"
    assert approved.approved_by == "reviewer-2"
    assert superseded.review_status == "superseded"
    assert superseded.superseded_at == NOW
    assert superseded.superseded_by_candidate_id == new.image_candidate_id
    service.close()


def test_replace_rejected_candidate_retains_immutable_rejection_audit(tmp_path: Path) -> None:
    database = tmp_path / "rejected-replacement.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    old = _add_candidate(database, _generation(service, campaign_id, scenes[0], key="old"), index=0)
    new = _add_candidate(database, _generation(service, campaign_id, scenes[0], key="new"), index=0)
    service.approve_candidate(old.image_candidate_id, "reviewer-1")
    service.close()
    rejection_time = datetime(2026, 8, 16, 12, 1, tzinfo=UTC)
    replacement_time = datetime(2026, 8, 16, 12, 2, tzinfo=UTC)
    review_times = iter((rejection_time, replacement_time))
    service = _service(database, clock=lambda: next(review_times))
    artifact_path = tmp_path / new.source_path
    artifact_path.parent.mkdir(parents=True)
    artifact_bytes = b"immutable-candidate-artifact"
    artifact_path.write_bytes(artifact_bytes)
    rejected = service.reject_candidate(new.image_candidate_id, "reviewer-2", "Framing is too tight")
    rejection_audit = (rejected.rejected_at, rejected.rejected_by, rejected.rejection_reason)
    artifact_evidence = (
        rejected.source_path,
        rejected.sha256,
        rejected.width,
        rejected.height,
        rejected.size_bytes,
        rejected.format,
    )

    approved = service.replace_approved_candidate(scenes[0], new.image_candidate_id, "reviewer-3")

    assert approved.review_status == "approved"
    assert (approved.rejected_at, approved.rejected_by, approved.rejection_reason) == rejection_audit
    assert approved.rejected_at == rejection_time
    assert approved.approved_at == replacement_time
    assert approved.rejected_at != approved.approved_at
    assert (
        approved.source_path,
        approved.sha256,
        approved.width,
        approved.height,
        approved.size_bytes,
        approved.format,
    ) == artifact_evidence
    assert artifact_path.is_file()
    assert artifact_path.read_bytes() == artifact_bytes
    service.close()


def test_review_rejects_cross_scene_candidate_and_rolls_back_partial_replacement(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cross-scene.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    old = _add_candidate(database, _generation(service, campaign_id, scenes[0], key="old"), index=0)
    other = _add_candidate(database, _generation(service, campaign_id, scenes[1], key="other"), index=0)
    service.approve_candidate(old.image_candidate_id, "reviewer-1")

    with pytest.raises(ImageCandidateSceneMismatchError):
        service.replace_approved_candidate(scenes[0], other.image_candidate_id, "reviewer-2")

    assert service.get_candidate(old.image_candidate_id).review_status == "approved"
    assert service.get_candidate(other.image_candidate_id).review_status == "pending_review"
    service.close()


def test_review_refuses_invalid_transitions_and_missing_candidates(tmp_path: Path) -> None:
    database = tmp_path / "invalid-transitions.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    rejected = _add_candidate(
        database, _generation(service, campaign_id, scenes[0], key="rejected"), index=0
    )
    approved = _add_candidate(
        database, _generation(service, campaign_id, scenes[1], key="approved"), index=0
    )

    with pytest.raises(ImageCandidateNotFoundError):
        service.approve_candidate("11111111-1111-4111-8111-111111111111", "reviewer-1")
    service.reject_candidate(rejected.image_candidate_id, "reviewer-1", "Not usable")
    with pytest.raises(ImageTransitionError):
        service.approve_candidate(rejected.image_candidate_id, "reviewer-2")
    with pytest.raises(ImageTransitionError):
        service.replace_approved_candidate(scenes[0], rejected.image_candidate_id, "reviewer-2")
    service.approve_candidate(approved.image_candidate_id, "reviewer-1")
    with pytest.raises(ImageTransitionError):
        service.reject_candidate(approved.image_candidate_id, "reviewer-2", "No longer wanted")
    service.close()


def test_review_validates_actor_and_sanitized_rejection_reason(tmp_path: Path) -> None:
    database = tmp_path / "sanitization.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    candidate = _add_candidate(
        database, _generation(service, campaign_id, scenes[0], key="candidate"), index=0
    )

    with pytest.raises(ValueError, match="approved_by"):
        service.approve_candidate(candidate.image_candidate_id, "reviewer\nsecret")
    with pytest.raises(ValueError, match="rejected_by"):
        service.reject_candidate(candidate.image_candidate_id, "reviewer\nsecret", "Not usable")
    with pytest.raises(ValueError):
        service.reject_candidate(candidate.image_candidate_id, "reviewer-1", "token=secret")
    assert service.get_candidate(candidate.image_candidate_id).review_status == "pending_review"
    service.close()


def test_reject_rejects_empty_rejection_reason_before_mutation(tmp_path: Path) -> None:
    database = tmp_path / "empty-reason.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    candidate = _add_candidate(
        database, _generation(service, campaign_id, scenes[0], key="candidate"), index=0
    )

    with pytest.raises(ValueError, match="rejection_reason"):
        service.reject_candidate(candidate.image_candidate_id, "reviewer-1", "")

    assert service.get_candidate(candidate.image_candidate_id).review_status == "pending_review"
    service.close()


def test_reject_rejects_whitespace_only_rejection_reason_before_mutation(tmp_path: Path) -> None:
    database = tmp_path / "whitespace-reason.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    candidate = _add_candidate(
        database, _generation(service, campaign_id, scenes[0], key="candidate"), index=0
    )

    with pytest.raises(ValueError, match="rejection_reason"):
        service.reject_candidate(candidate.image_candidate_id, "reviewer-1", "   ")

    assert service.get_candidate(candidate.image_candidate_id).review_status == "pending_review"
    service.close()


def test_review_rolls_back_when_database_valid_candidate_fails_domain_validation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "domain-validation.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    generation_id = _generation(service, campaign_id, scenes[0], key="candidate")
    candidate_id = _add_database_valid_domain_invalid_candidate(
        database, generation_id, review_status="pending_review"
    )

    with pytest.raises(ValueError, match="safe workspace-relative path"):
        service.approve_candidate(candidate_id, "reviewer-1")

    assert _raw_review_status(database, candidate_id) == "pending_review"
    service.close()


@pytest.mark.parametrize("invalid_candidate", ["old", "new"])
def test_replace_rolls_back_when_either_affected_candidate_fails_domain_validation(
    tmp_path: Path, invalid_candidate: str
) -> None:
    database = tmp_path / f"replacement-domain-{invalid_candidate}.db"
    campaign_id, scenes = _campaign(database)
    service = _service(database)
    old_generation = _generation(service, campaign_id, scenes[0], key="old")
    new_generation = _generation(service, campaign_id, scenes[0], key="new")
    if invalid_candidate == "old":
        old_id = _add_database_valid_domain_invalid_candidate(
            database, old_generation, review_status="approved"
        )
        new_id = _add_candidate(database, new_generation, index=0).image_candidate_id
    else:
        old_id = _add_candidate(database, old_generation, index=0).image_candidate_id
        service.approve_candidate(old_id, "reviewer-1")
        new_id = _add_database_valid_domain_invalid_candidate(
            database, new_generation, review_status="pending_review"
        )

    with pytest.raises(ValueError, match="safe workspace-relative path"):
        service.replace_approved_candidate(scenes[0], new_id, "reviewer-2")

    assert _raw_review_status(database, old_id) == "approved"
    assert _raw_review_status(database, new_id) == "pending_review"
    service.close()

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auraly_pipeline.images.domain import (
    ImageCandidate,
    ImageGenerateRequest,
    ImageGeneration,
    generation_request_fingerprint,
)


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
SCENE_ID = "11111111-1111-4111-8111-111111111111"
GENERATION_ID = "22222222-2222-4222-8222-222222222222"


def _request(*, idempotency_key: str) -> ImageGenerateRequest:
    return ImageGenerateRequest(
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        idempotency_key=idempotency_key,
        prompt_snapshot="A moonlit studio",
        reference_image_path="campaigns/campaign-1/references/hero.png",
        reference_image_sha256="a" * 64,
    )


def _generation() -> ImageGeneration:
    request = _request(idempotency_key="image-a")
    return ImageGeneration(
        image_generation_id=GENERATION_ID,
        campaign_id=request.campaign_id,
        scene_variant_id=request.scene_variant_id,
        job_id="33333333-3333-4333-8333-333333333333",
        generation_number=1,
        idempotency_key=request.idempotency_key,
        request_fingerprint=generation_request_fingerprint(request),
        prompt_snapshot=request.prompt_snapshot,
        prompt_sha256=request.prompt_sha256,
        reference_image_path=request.reference_image_path,
        reference_image_sha256=request.reference_image_sha256,
        provider="google_flow",
        executor="local_fake",
        provider_state="queued",
        created_at=NOW,
        updated_at=NOW,
    )


def _candidate(**changes: object) -> ImageCandidate:
    values: dict[str, object] = {
        "image_candidate_id": "44444444-4444-4444-8444-444444444444",
        "image_generation_id": GENERATION_ID,
        "candidate_index": 0,
        "source_path": "campaigns/campaign-1/images/scene/generation-0001/candidate-0000.png",
        "sha256": "b" * 64,
        "width": 16,
        "height": 16,
        "size_bytes": 128,
        "format": "png",
        "review_status": "pending_review",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return ImageCandidate.model_validate(values)


def test_generation_fingerprint_is_canonical_and_excludes_submission_identity() -> None:
    first = _request(idempotency_key="image-a")
    second = _request(idempotency_key="image-b")

    assert first.prompt_sha256 == "a8c3ef9d3052406ab1e8ed43dfaf695a34ede84ebc005a7aba3e7527431348d5"
    assert generation_request_fingerprint(first) == generation_request_fingerprint(second)
    assert generation_request_fingerprint(first) == (
        "90f93a8bfae15819b0f1b798a0969e271c89cdd1fa47292d8cbd9e65e86809d2"
    )


@pytest.mark.parametrize(
    ("factory", "changes"),
    [
        (_generation, {"provider_state": "downloading"}),
        (_generation, {"prompt_sha256": "not-a-sha"}),
        (_candidate, {"candidate_index": -1}),
        (_candidate, {"width": 0}),
        (_candidate, {"sha256": "not-a-sha"}),
        (_candidate, {"review_status": "selected"}),
    ],
)
def test_image_contract_rejects_invalid_state_hash_and_artifact_facts(
    factory: Callable[..., ImageGeneration | ImageCandidate],
    changes: dict[str, object],
) -> None:
    model = factory()

    with pytest.raises(ValidationError):
        type(model).model_validate({**model.model_dump(), **changes})


@pytest.mark.parametrize(
    ("reference_image_path", "reference_image_sha256"),
    [("campaigns/campaign-1/reference.png", None), (None, "a" * 64)],
)
def test_image_request_requires_reference_path_and_hash_together(
    reference_image_path: str | None,
    reference_image_sha256: str | None,
) -> None:
    with pytest.raises(ValidationError):
        ImageGenerateRequest(
            campaign_id="campaign-1",
            scene_variant_id=SCENE_ID,
            idempotency_key="image-a",
            prompt_snapshot="A moonlit studio",
            reference_image_path=reference_image_path,
            reference_image_sha256=reference_image_sha256,
        )

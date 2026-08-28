from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import re

import pytest
from pydantic import ValidationError

from auraly_pipeline.images.domain import (
    FlowCandidateSlot,
    FlowGenerationRun,
    ImageCandidate,
    ImageGenerateRequest,
    ImageGeneration,
    ensure_flow_run_transition,
    ensure_flow_slot_transition,
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


def _playwright_request(**changes: object) -> ImageGenerateRequest:
    values: dict[str, object] = {
        "campaign_id": "campaign-1",
        "scene_variant_id": SCENE_ID,
        "idempotency_key": "flow-1",
        "prompt_snapshot": "A moonlit studio",
        "reference_image_path": "campaigns/campaign-1/references/hero.png",
        "reference_image_sha256": "a" * 64,
        "executor": "playwright_python",
        "generation_contract_version": "flow-generation-v1",
        "provider_action_confirmed": True,
        "provider_action_approved_by": "operator-1",
    }
    values.update(changes)
    return ImageGenerateRequest.model_validate(values)


def _flow_run(**changes: object) -> FlowGenerationRun:
    values: dict[str, object] = {
        "flow_generation_run_id": "55555555-5555-4555-8555-555555555555",
        "image_generation_id": GENERATION_ID,
        "stage": "prepared",
        "required_candidate_count": 2,
        "required_resolution": "2K",
        "dispatch_attempt_number": 1,
        "provider_action_approved_by": "operator-1",
        "provider_action_approved_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return FlowGenerationRun.model_validate(values)


def _flow_slot(**changes: object) -> FlowCandidateSlot:
    values: dict[str, object] = {
        "flow_candidate_slot_id": "66666666-6666-4666-8666-666666666666",
        "flow_generation_run_id": "55555555-5555-4555-8555-555555555555",
        "slot_index": 0,
        "state": "pending",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return FlowCandidateSlot.model_validate(values)


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


def test_local_fake_request_keeps_v1_defaults_and_fingerprint() -> None:
    request = ImageGenerateRequest(
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        idempotency_key="fake-1",
        prompt_snapshot="safe prompt",
    )

    assert request.executor == "local_fake"
    assert request.generation_contract_version == "image-generation-v1"
    assert request.provider_action_confirmed is False
    assert request.provider_action_approved_by is None
    assert generation_request_fingerprint(request) == (
        "cfcade191a4bb02881fe7ed626a0cc746967053b10d9e9cf637f9cd179db46f6"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"reference_image_path": None, "reference_image_sha256": None},
        {"provider_action_confirmed": False},
        {"provider_action_approved_by": "PRIVATE-operator"},
        {"generation_contract_version": "image-generation-v1"},
        {"required_candidate_count": 1},
        {"required_output_resolution": "1K"},
    ],
)
def test_playwright_request_rejects_missing_or_unsafe_fixed_contract(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _playwright_request(**changes)


def test_playwright_request_accepts_exact_fixed_contract() -> None:
    request = _playwright_request()

    assert request.required_candidate_count == 2
    assert request.required_output_resolution == "2K"
    assert re.fullmatch(r"[0-9a-f]{64}", generation_request_fingerprint(request))


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("prepared", "inputs_verified"),
        ("inputs_verified", "dispatch_intent_recorded"),
        ("dispatch_intent_recorded", "dispatch_confirmed"),
        ("dispatch_confirmed", "candidates_observed"),
        ("candidates_observed", "downloading"),
        ("downloading", "completed"),
        ("ambiguous", "dispatch_confirmed"),
        ("ambiguous", "candidates_observed"),
        ("ambiguous", "downloading"),
        ("ambiguous", "prepared"),
    ],
)
def test_flow_run_transition_accepts_only_authorized_progressions(
    current: str, target: str
) -> None:
    ensure_flow_run_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("inputs_verified", "prepared"),
        ("completed", "downloading"),
        ("blocked", "prepared"),
        ("failed", "prepared"),
    ],
)
def test_flow_run_transition_rejects_backwards_or_terminal_changes(
    current: str, target: str
) -> None:
    with pytest.raises(ValueError):
        ensure_flow_run_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "observed"),
        ("observed", "download_intent_recorded"),
        ("download_intent_recorded", "downloaded"),
        ("downloaded", "ingested"),
    ],
)
def test_flow_slot_transition_accepts_only_download_progressions(
    current: str, target: str
) -> None:
    ensure_flow_slot_transition(current, target)


def test_flow_slot_rejects_out_of_range_index_and_unpaired_artifact_facts() -> None:
    with pytest.raises(ValidationError):
        _flow_slot(slot_index=2)
    with pytest.raises(ValidationError):
        _flow_slot(staging_path="campaigns/campaign-1/staging.png")
    with pytest.raises(ValidationError):
        _flow_slot(staging_path="C:/private/staging.png", staged_sha256="c" * 64)


def test_flow_slot_ingested_requires_candidate_and_prior_download() -> None:
    with pytest.raises(ValidationError):
        _flow_slot(state="ingested", image_candidate_id=None)
    with pytest.raises(ValidationError):
        _flow_slot(
            state="ingested",
            image_candidate_id="77777777-7777-4777-8777-777777777777",
        )


def test_flow_run_rejects_confirmation_without_intent_and_early_grid_evidence() -> None:
    with pytest.raises(ValidationError):
        _flow_run(
            stage="dispatch_confirmed",
            dispatch_confirmed_at=NOW,
        )
    with pytest.raises(ValidationError):
        _flow_run(
            stage="inputs_verified",
            grid_evidence_path="campaigns/campaign-1/grid.png",
            grid_evidence_sha256="d" * 64,
        )

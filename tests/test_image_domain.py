from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
import re
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

import auraly_pipeline.images as images

from auraly_pipeline.images.domain import (
    FlowCandidateSlot,
    FlowCandidateSlotState,
    FlowGenerationRun,
    FlowGenerationStage,
    FlowReconciliationReason,
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


def test_playwright_fingerprint_tracks_generation_intent_not_approval_audit() -> None:
    request = _playwright_request()
    fingerprint = generation_request_fingerprint(request)

    assert fingerprint != generation_request_fingerprint(
        request.model_copy(
            update={"reference_image_path": "campaigns/campaign-1/references/other.png"}
        )
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"reference_image_sha256": "b" * 64})
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"required_candidate_count": 1})
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"required_output_resolution": "1K"})
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"prompt_snapshot": "A different moonlit studio"})
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"executor": "local_fake"})
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"provider": "different_provider"})
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"scene_variant_id": "99999999-9999-4999-8999-999999999999"})
    )
    assert fingerprint != generation_request_fingerprint(
        request.model_copy(update={"generation_contract_version": "image-generation-v1"})
    )
    assert fingerprint == generation_request_fingerprint(
        request.model_copy(update={"provider_action_approved_by": "operator-2"})
    )


def test_playwright_fingerprint_matches_approved_canonical_payload_golden() -> None:
    request = _playwright_request()

    assert generation_request_fingerprint(request) == (
        "7e3b448e73592d91c255c5bafab9e53e88474cc6a1cf48e6044023ed7881583b"
    )


def test_executor_authorization_contract_rejects_missing_or_fake_authorization() -> None:
    with pytest.raises(ValidationError):
        _playwright_request(provider_action_approved_by=None)
    for changes in (
        {"provider_action_confirmed": True},
        {"provider_action_approved_by": "operator-1"},
    ):
        with pytest.raises(ValidationError):
            ImageGenerateRequest(
                campaign_id="campaign-1",
                scene_variant_id=SCENE_ID,
                idempotency_key="fake-authorization",
                prompt_snapshot="safe prompt",
                **changes,
            )


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
        ("prepared", "ambiguous"),
        ("inputs_verified", "ambiguous"),
        ("prepared", "unknown"),
        ("unknown", "prepared"),
    ],
)
def test_flow_run_transition_rejects_pre_dispatch_ambiguity_and_unknown_stages(
    current: str, target: str
) -> None:
    with pytest.raises(ValueError):
        ensure_flow_run_transition(current, target)


def test_flow_run_transition_matrix_is_exact_and_closed() -> None:
    expected_stages = {
        "prepared",
        "inputs_verified",
        "dispatch_intent_recorded",
        "dispatch_confirmed",
        "candidates_observed",
        "downloading",
        "completed",
        "ambiguous",
        "blocked",
        "failed",
    }
    expected_targets = {
        "prepared": {"inputs_verified", "blocked", "failed"},
        "inputs_verified": {"dispatch_intent_recorded", "blocked", "failed"},
        "dispatch_intent_recorded": {"dispatch_confirmed", "ambiguous", "blocked", "failed"},
        "dispatch_confirmed": {"candidates_observed", "blocked", "failed"},
        "candidates_observed": {"downloading", "blocked", "failed"},
        "downloading": {"completed", "blocked", "failed"},
        "completed": set(),
        "ambiguous": {
            "prepared",
            "dispatch_confirmed",
            "candidates_observed",
            "downloading",
            "blocked",
            "failed",
        },
        "blocked": set(),
        "failed": set(),
    }

    assert set(get_args(FlowGenerationStage)) == expected_stages
    for current in expected_stages:
        for target in expected_stages:
            if target in expected_targets[current]:
                ensure_flow_run_transition(current, target)
            else:
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


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("observed", "pending"),
        ("ingested", "blocked"),
        ("blocked", "observed"),
        ("pending", "unknown"),
    ],
)
def test_flow_slot_transition_rejects_backwards_terminal_and_unknown_states(
    current: str, target: str
) -> None:
    with pytest.raises(ValueError):
        ensure_flow_slot_transition(current, target)


def test_flow_slot_transition_matrix_is_exact_and_closed() -> None:
    expected_states = {
        "pending",
        "observed",
        "download_intent_recorded",
        "downloaded",
        "ingested",
        "blocked",
    }
    expected_targets = {
        "pending": {"observed", "blocked"},
        "observed": {"download_intent_recorded", "blocked"},
        "download_intent_recorded": {"downloaded", "blocked"},
        "downloaded": {"ingested", "blocked"},
        "ingested": set(),
        "blocked": set(),
    }

    assert set(get_args(FlowCandidateSlotState)) == expected_states
    for current in expected_states:
        for target in expected_states:
            if target in expected_targets[current]:
                ensure_flow_slot_transition(current, target)
            else:
                with pytest.raises(ValueError):
                    ensure_flow_slot_transition(current, target)


def test_flow_slot_rejects_out_of_range_index_and_unpaired_artifact_facts() -> None:
    with pytest.raises(ValidationError):
        _flow_slot(slot_index=-1)
    with pytest.raises(ValidationError):
        _flow_slot(slot_index=2)
    with pytest.raises(ValidationError):
        _flow_slot(staging_path="campaigns/campaign-1/staging.png")
    with pytest.raises(ValidationError):
        _flow_slot(staged_sha256="c" * 64)
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


def _complete_ingested_slot_values() -> dict[str, object]:
    return {
        "state": "ingested",
        "provider_slot_fingerprint": "f" * 64,
        "download_intent_at": NOW,
        "staging_path": "campaigns/campaign-1/staging.png",
        "staged_sha256": "c" * 64,
        "image_candidate_id": "77777777-7777-4777-8777-777777777777",
    }


def test_flow_slot_accepts_complete_ingested_checkpoint() -> None:
    slot = _flow_slot(**_complete_ingested_slot_values())

    assert slot.state == "ingested"


@pytest.mark.parametrize(
    "removed_fields",
    [
        pytest.param(("provider_slot_fingerprint",), id="fingerprint"),
        pytest.param(("download_intent_at",), id="download-intent"),
        pytest.param(("staging_path", "staged_sha256"), id="staging-pair"),
        pytest.param(("image_candidate_id",), id="candidate-link"),
    ],
)
def test_flow_slot_ingested_requires_each_independent_checkpoint_fact(
    removed_fields: tuple[str, ...],
) -> None:
    values = _complete_ingested_slot_values()
    for field in removed_fields:
        values.pop(field)

    with pytest.raises(ValidationError):
        _flow_slot(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "observed"},
        {"state": "download_intent_recorded", "download_intent_at": NOW},
        {"state": "download_intent_recorded", "provider_slot_fingerprint": "f" * 64},
        {
            "state": "downloaded",
            "download_intent_at": NOW,
            "staging_path": "campaigns/campaign-1/staging.png",
            "staged_sha256": "c" * 64,
        },
        {
            "state": "downloaded",
            "provider_slot_fingerprint": "f" * 64,
            "staging_path": "campaigns/campaign-1/staging.png",
            "staged_sha256": "c" * 64,
        },
        {
            "state": "downloaded",
            "provider_slot_fingerprint": "f" * 64,
            "download_intent_at": NOW,
        },
    ],
)
def test_flow_slot_rejects_each_missing_required_checkpoint_predecessor(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _flow_slot(**changes)


@pytest.mark.parametrize(
    ("state", "changes"),
    [
        ("pending", {}),
        ("observed", {"provider_slot_fingerprint": "f" * 64}),
        (
            "download_intent_recorded",
            {"provider_slot_fingerprint": "f" * 64, "download_intent_at": NOW},
        ),
        (
            "downloaded",
            {
                "provider_slot_fingerprint": "f" * 64,
                "download_intent_at": NOW,
                "staging_path": "campaigns/campaign-1/staging.png",
                "staged_sha256": "c" * 64,
            },
        ),
        ("blocked", {"provider_slot_fingerprint": "f" * 64}),
    ],
)
def test_flow_slot_rejects_candidate_link_before_ingested(
    state: str,
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _flow_slot(
            state=state,
            image_candidate_id="77777777-7777-4777-8777-777777777777",
            **changes,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "pending", "provider_slot_fingerprint": "f" * 64},
        {"state": "pending", "download_intent_at": NOW},
        {
            "state": "pending",
            "staging_path": "campaigns/campaign-1/staging.png",
            "staged_sha256": "c" * 64,
        },
        {"state": "observed", "provider_slot_fingerprint": "f" * 64, "download_intent_at": NOW},
        {
            "state": "observed",
            "provider_slot_fingerprint": "f" * 64,
            "staging_path": "campaigns/campaign-1/staging.png",
            "staged_sha256": "c" * 64,
        },
        {
            "state": "download_intent_recorded",
            "provider_slot_fingerprint": "f" * 64,
            "download_intent_at": NOW,
            "staging_path": "campaigns/campaign-1/staging.png",
            "staged_sha256": "c" * 64,
        },
        {"state": "blocked", "download_intent_at": NOW},
        {
            "state": "blocked",
            "provider_slot_fingerprint": "f" * 64,
            "staging_path": "campaigns/campaign-1/staging.png",
            "staged_sha256": "c" * 64,
        },
    ],
)
def test_flow_slot_rejects_facts_from_later_download_states(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _flow_slot(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"provider_slot_fingerprint": "f" * 64},
        {"provider_slot_fingerprint": "f" * 64, "download_intent_at": NOW},
        {
            "provider_slot_fingerprint": "f" * 64,
            "download_intent_at": NOW,
            "staging_path": "campaigns/campaign-1/staging.png",
            "staged_sha256": "c" * 64,
        },
    ],
)
def test_blocked_flow_slot_preserves_only_consistent_recovery_facts(
    changes: dict[str, object],
) -> None:
    assert _flow_slot(state="blocked", **changes).state == "blocked"


@pytest.mark.parametrize(
    ("state", "changes"),
    [
        ("pending", {}),
        ("observed", {"provider_slot_fingerprint": "f" * 64}),
        (
            "download_intent_recorded",
            {"provider_slot_fingerprint": "f" * 64, "download_intent_at": NOW},
        ),
        (
            "downloaded",
            {
                "provider_slot_fingerprint": "f" * 64,
                "download_intent_at": NOW,
                "staging_path": "campaigns/campaign-1/staging.png",
                "staged_sha256": "c" * 64,
            },
        ),
        (
            "ingested",
            {
                "provider_slot_fingerprint": "f" * 64,
                "download_intent_at": NOW,
                "staging_path": "campaigns/campaign-1/staging.png",
                "staged_sha256": "c" * 64,
                "image_candidate_id": "77777777-7777-4777-8777-777777777777",
            },
        ),
    ],
)
def test_flow_slot_accepts_only_its_exact_checkpoint_facts(
    state: str,
    changes: dict[str, object],
) -> None:
    assert _flow_slot(state=state, **changes).state == state


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


@pytest.mark.parametrize(
    "changes",
    [
        {"stage": "prepared", "dispatch_intent_at": NOW},
        {"stage": "inputs_verified", "dispatch_confirmed_at": NOW},
        {
            "stage": "dispatch_intent_recorded",
            "dispatch_intent_at": NOW,
            "dispatch_confirmed_at": NOW,
        },
        {"stage": "ambiguous"},
        {
            "stage": "ambiguous",
            "dispatch_intent_at": NOW,
            "dispatch_confirmed_at": NOW,
        },
    ],
)
def test_flow_run_rejects_checkpoint_timestamps_that_break_dispatch_boundary(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _flow_run(**changes)


def test_flow_run_rejects_grid_evidence_while_dispatch_is_ambiguous() -> None:
    with pytest.raises(ValidationError):
        _flow_run(
            stage="ambiguous",
            dispatch_intent_at=NOW,
            grid_evidence_path="campaigns/campaign-1/inspection/grid.png",
            grid_evidence_sha256="d" * 64,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_action_approved_at", NOW.replace(tzinfo=None)),
        ("created_at", NOW.replace(tzinfo=None)),
        ("updated_at", NOW.replace(tzinfo=None)),
        ("dispatch_intent_at", NOW.replace(tzinfo=None)),
        ("dispatch_confirmed_at", NOW.replace(tzinfo=None)),
        ("created_at", NOW.astimezone(timezone(timedelta(hours=-3)))),
    ],
)
def test_flow_run_requires_utc_aware_timestamps(field: str, value: datetime) -> None:
    changes: dict[str, object] = {field: value}
    if field == "dispatch_intent_at":
        changes["stage"] = "dispatch_intent_recorded"
    if field == "dispatch_confirmed_at":
        changes.update(
            stage="dispatch_confirmed",
            dispatch_intent_at=NOW,
        )
    with pytest.raises(ValidationError):
        _flow_run(**changes)


def test_flow_run_requires_utc_chronology() -> None:
    with pytest.raises(ValidationError):
        _flow_run(created_at=NOW, updated_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValidationError):
        _flow_run(provider_action_approved_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError):
        _flow_run(
            stage="dispatch_confirmed",
            dispatch_intent_at=NOW + timedelta(seconds=1),
            dispatch_confirmed_at=NOW,
        )


@pytest.mark.parametrize(
    "workspace_path",
    [
        "workspace/abc",
        "fx/tools/flow?workspace=abc",
        "fx/tools/flow#workspace",
        "https://labs.google/fx/tools/flow",
    ],
)
def test_flow_run_rejects_workspace_paths_outside_safe_flow_family(
    workspace_path: str,
) -> None:
    with pytest.raises(ValidationError):
        _flow_run(
            provider_workspace_path=workspace_path,
            provider_workspace_fingerprint="e" * 64,
        )


def test_flow_run_requires_both_workspace_and_grid_evidence_pairs() -> None:
    with pytest.raises(ValidationError):
        _flow_run(provider_workspace_path="fx/tools/flow/workspace-1")
    with pytest.raises(ValidationError):
        _flow_run(provider_workspace_fingerprint="e" * 64)
    checkpoint = {
        "stage": "candidates_observed",
        "dispatch_intent_at": NOW,
        "dispatch_confirmed_at": NOW,
    }
    with pytest.raises(ValidationError):
        _flow_run(**checkpoint, grid_evidence_path="campaigns/campaign-1/inspection/grid.png")
    with pytest.raises(ValidationError):
        _flow_run(**checkpoint, grid_evidence_sha256="d" * 64)
    run = _flow_run(
        **checkpoint,
        provider_workspace_path="fx/tools/flow/workspace-1",
        provider_workspace_fingerprint="e" * 64,
        grid_evidence_path="campaigns/campaign-1/inspection/grid.png",
        grid_evidence_sha256="d" * 64,
    )
    assert run.provider_workspace_path == "fx/tools/flow/workspace-1"


def test_flow_run_accepts_only_allowlisted_failure_codes() -> None:
    for code in (
        "flow_runtime_busy",
        "flow_authentication_required",
        "flow_ui_contract_failed",
        "flow_input_verification_failed",
        "flow_dispatch_ambiguous",
        "flow_candidate_grid_ambiguous",
        "flow_download_failed",
        "flow_artifact_invalid",
        "flow_artifact_conflict",
        "flow_recovery_blocked",
        "flow_diagnostic_sanitization_failed",
        "flow_browser_close_failed",
        "image_job_integrity_failed",
    ):
        assert _flow_run(last_failure_code=code).last_failure_code == code
    with pytest.raises(ValidationError):
        _flow_run(last_failure_code="unrecognized_failure")


def test_flow_slot_requires_utc_chronology() -> None:
    with pytest.raises(ValidationError):
        _flow_slot(created_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _flow_slot(created_at=NOW, updated_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValidationError):
        _flow_slot(
            state="download_intent_recorded",
            provider_slot_fingerprint="f" * 64,
            download_intent_at=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValidationError):
        _flow_slot(
            state="download_intent_recorded",
            provider_slot_fingerprint="f" * 64,
            created_at=NOW + timedelta(seconds=1),
            updated_at=NOW + timedelta(seconds=1),
            download_intent_at=NOW,
        )


def test_flow_reconciliation_reason_rejects_unknown_value() -> None:
    reason_adapter = TypeAdapter(FlowReconciliationReason)
    for reason in (
        "no_dispatch_proven",
        "existing_dispatch_reconciled",
        "staged_artifact_reconciled",
        "completed_generation_reconciled",
    ):
        assert reason_adapter.validate_python(reason) == reason
    with pytest.raises(ValidationError):
        reason_adapter.validate_python("unknown_reconciliation")


def test_images_package_exports_only_stable_flow_contracts() -> None:
    expected_exports = {
        "FlowCandidateSlot",
        "FlowCandidateSlotState",
        "FlowGenerationRun",
        "FlowGenerationStage",
        "FlowReconciliationReason",
        "ensure_flow_run_transition",
        "ensure_flow_slot_transition",
    }

    assert set(images.__all__) == expected_exports
    assert images.FlowGenerationRun is FlowGenerationRun
    assert images.FlowCandidateSlot is FlowCandidateSlot
    assert images.FlowGenerationStage is FlowGenerationStage
    assert images.FlowCandidateSlotState is FlowCandidateSlotState
    assert images.FlowReconciliationReason is FlowReconciliationReason
    assert callable(images.ensure_flow_run_transition)
    assert callable(images.ensure_flow_slot_transition)
    for private_or_unsupported_name in (
        "_FLOW_RUN_TRANSITIONS",
        "_FLOW_SLOT_TRANSITIONS",
        "FlowGenerationRunRow",
        "FlowCandidateSlotRow",
        "ImageRepository",
        "Page",
    ):
        assert not hasattr(images, private_or_unsupported_name)

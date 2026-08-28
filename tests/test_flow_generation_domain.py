from __future__ import annotations

import pytest
from pydantic import ValidationError

from auraly_pipeline import flow
from auraly_pipeline.flow.generation_domain import (
    FlowCandidateObservation,
    FlowDispatchAmbiguousError,
    FlowDownloadCorrelationError,
    FlowGenerationFailedStep,
    FlowGenerationObservation,
    FlowGenerationUiContractError,
    FlowWorkspaceIdentity,
)


def test_candidate_observation_contains_only_safe_identity() -> None:
    """Removing a safe candidate field must break durable slot binding."""
    observation = FlowCandidateObservation(
        fingerprint="a" * 64,
        semantic_order=0,
        completed=True,
    )

    assert observation.model_dump() == {
        "fingerprint": "a" * 64,
        "semantic_order": 0,
        "completed": True,
    }


def test_generation_contract_rejects_raw_url_or_prompt_fields() -> None:
    """Adding browser/provider payload fields would leak private provider state."""
    fields = set(FlowCandidateObservation.model_fields)

    assert {"url", "thumbnail_url", "prompt", "dom", "html"}.isdisjoint(fields)
    with pytest.raises(ValidationError):
        FlowCandidateObservation(
            fingerprint="a" * 64,
            semantic_order=0,
            completed=True,
            url="https://provider.invalid/private",  # type: ignore[call-arg]
        )


def test_workspace_identity_is_relative_and_hash_bound() -> None:
    """Accepting origins or tokens would make persisted recovery routing unsafe."""
    identity = FlowWorkspaceIdentity(
        workspace_path="fx/tools/flow/workspaces/validated-workspace",
        fingerprint="b" * 64,
    )

    assert identity.model_dump() == {
        "workspace_path": "fx/tools/flow/workspaces/validated-workspace",
        "fingerprint": "b" * 64,
    }
    for unsafe_path in ("/fx/tools/flow", "../workspace", "https://provider.invalid/workspace"):
        with pytest.raises(ValidationError):
            FlowWorkspaceIdentity(workspace_path=unsafe_path, fingerprint="b" * 64)


def test_generation_observation_carries_only_verification_facts() -> None:
    """Returning a prompt or reference path would cross the browser privacy boundary."""
    observation = FlowGenerationObservation(reference_verified=True, prompt_verified=True)

    assert observation.model_dump() == {"reference_verified": True, "prompt_verified": True}
    assert {"prompt", "reference_path", "url", "html"}.isdisjoint(
        FlowGenerationObservation.model_fields
    )


@pytest.mark.parametrize(
    "failed_step",
    (
        "open_workspace",
        "upload_reference",
        "verify_reference",
        "fill_prompt",
        "verify_prompt",
        "record_dispatch_intent",
        "dispatch_generate",
        "confirm_dispatch",
        "observe_candidates",
        "capture_grid_evidence",
        "request_2k",
        "capture_download",
        "close_browser",
    ),
)
def test_generation_errors_accept_only_the_approved_failed_steps(failed_step: str) -> None:
    """Unknown failure labels would leak implementation detail into Job diagnostics."""
    error = FlowGenerationUiContractError(failed_step=failed_step)  # type: ignore[arg-type]

    assert error.failed_step == failed_step
    assert str(error) == ""


def test_generation_errors_reject_private_or_unapproved_failed_steps() -> None:
    """A raw browser exception must not become an observable failure code."""
    with pytest.raises(ValueError):
        FlowGenerationUiContractError(failed_step=r"C:\\private\\browser-error")  # type: ignore[arg-type]


def test_typed_dispatch_and_download_errors_have_fixed_safe_steps() -> None:
    """Wrong error mapping could permit retry policy to treat an ambiguous mutation as safe."""
    dispatch_error = FlowDispatchAmbiguousError()
    download_error = FlowDownloadCorrelationError()

    assert dispatch_error.failed_step == "confirm_dispatch"
    assert download_error.failed_step == "capture_download"
    assert str(dispatch_error) == ""
    assert str(download_error) == ""


def test_flow_package_exports_generation_contracts() -> None:
    """Dropping exports would force callers to reach into browser implementation modules."""
    assert flow.FlowCandidateObservation is FlowCandidateObservation
    assert flow.FlowGenerationFailedStep is FlowGenerationFailedStep


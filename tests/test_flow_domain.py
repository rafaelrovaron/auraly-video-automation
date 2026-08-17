from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from auraly_pipeline import flow
from auraly_pipeline.flow.domain import (
    FLOW_URL,
    FlowAuthenticationTimeoutError,
    FlowBrowserLaunchError,
    FlowDiagnosticSanitizationError,
    FlowFailureEvidence,
    FlowPreflightResult,
    FlowRuntimeBusyError,
    FlowRuntimeObservation,
    FlowUiContractError,
    FlowUnexpectedStateError,
)


TIMESTAMP = datetime(2026, 8, 16, tzinfo=UTC)
NON_READY_STATUSES = (
    "authentication_required",
    "human_intervention_required",
    "runtime_busy",
    "browser_launch_failed",
    "ui_contract_failed",
)
FAILED_STEPS = (
    "validate_config",
    "acquire_runtime_lock",
    "launch_browser",
    "navigate_flow",
    "await_manual_authentication",
    "verify_flow_ui",
    "sanitize_diagnostics",
    "close_browser",
)


def test_flow_package_exports_its_stable_contract_types() -> None:
    assert flow.FLOW_URL == FLOW_URL
    assert flow.FlowPreflightResult is FlowPreflightResult


def test_ready_result_requires_authenticated_ui_ready_and_no_failure_fields() -> None:
    result = FlowPreflightResult.ready(timestamp=TIMESTAMP)

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=False)

    assert payload == {
        "schemaVersion": "1.0",
        "success": True,
        "status": "ready",
        "flowUrl": FLOW_URL,
        "authenticated": True,
        "uiReady": True,
        "failedStep": None,
        "failedLocator": None,
        "diagnosticRunId": None,
        "screenshot": None,
        "trace": None,
        "timestamp": "2026-08-16T00:00:00Z",
    }


@pytest.mark.parametrize("status", ("ready", *NON_READY_STATUSES))
def test_statuses_are_exact_and_success_matches_ready(status: str) -> None:
    if status == "ready":
        result = FlowPreflightResult.ready(timestamp=TIMESTAMP)
    else:
        result = FlowPreflightResult.failure(
            status=status,  # type: ignore[arg-type]
            authenticated=False,
            ui_ready=False,
            failed_step="launch_browser",
            timestamp=TIMESTAMP,
        )

    assert result.status == status
    assert result.success is (status == "ready")


@pytest.mark.parametrize("failed_step", FAILED_STEPS)
def test_failure_accepts_every_allowlisted_failed_step(failed_step: str) -> None:
    result = FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=False,
        ui_ready=False,
        failed_step=failed_step,  # type: ignore[arg-type]
        timestamp=TIMESTAMP,
    )

    assert result.failed_step == failed_step


@pytest.mark.parametrize(
    ("success", "status", "authenticated", "ui_ready", "failed_step"),
    (
        (True, "authentication_required", True, True, None),
        (False, "ready", False, False, "launch_browser"),
        (True, "ready", False, True, None),
        (True, "ready", True, False, None),
        (True, "ready", True, True, "verify_flow_ui"),
        (False, "runtime_busy", False, False, None),
    ),
)
def test_public_result_rejects_inconsistent_ready_and_non_ready_fields(
    success: bool,
    status: str,
    authenticated: bool,
    ui_ready: bool,
    failed_step: str | None,
) -> None:
    with pytest.raises(ValidationError):
        FlowPreflightResult(
            success=success,
            status=status,  # type: ignore[arg-type]
            flow_url=FLOW_URL,
            authenticated=authenticated,
            ui_ready=ui_ready,
            failed_step=failed_step,  # type: ignore[arg-type]
            timestamp=TIMESTAMP,
        )


@pytest.mark.parametrize(
    "field,value",
    (
        ("status", "not-a-status"),
        ("failed_step", "click_generate"),
        ("failed_locator", "FIRST_BUTTON"),
        ("flow_url", "https://example.invalid/flow"),
    ),
)
def test_public_result_rejects_values_outside_the_allowlists(field: str, value: str) -> None:
    payload: dict[str, object] = {
        "success": False,
        "status": "runtime_busy",
        "flow_url": FLOW_URL,
        "authenticated": False,
        "ui_ready": False,
        "failed_step": "acquire_runtime_lock",
        "timestamp": TIMESTAMP,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        FlowPreflightResult(**payload)


def test_non_ready_result_rejects_success_true_and_private_artifact_paths() -> None:
    with pytest.raises(ValidationError):
        FlowPreflightResult(
            success=True,
            status="ui_contract_failed",
            flow_url=FLOW_URL,
            authenticated=True,
            ui_ready=False,
            screenshot=r"C:\Users\private\screenshot.png",
            timestamp=TIMESTAMP,
        )


@pytest.mark.parametrize(
    "diagnostic_run_id",
    (
        "",
        "../run",
        "run/child",
        r"C:\Users\private",
        "20260816T000000Z-PRIVATE",
        "20260816T000000Z-0123456g",
    ),
)
def test_failure_rejects_unsafe_diagnostic_run_ids(diagnostic_run_id: str) -> None:
    with pytest.raises(ValidationError):
        FlowPreflightResult.failure(
            status="ui_contract_failed",
            authenticated=True,
            ui_ready=False,
            failed_step="verify_flow_ui",
            diagnostic_run_id=diagnostic_run_id,
            timestamp=TIMESTAMP,
        )


@pytest.mark.parametrize("artifact_field", ("screenshot", "trace"))
def test_artifacts_are_safe_fixed_names_and_require_a_diagnostic_run(
    artifact_field: str,
) -> None:
    base = {
        "status": "ui_contract_failed",
        "authenticated": True,
        "ui_ready": False,
        "failed_step": "verify_flow_ui",
        "timestamp": TIMESTAMP,
    }
    with pytest.raises(ValidationError):
        FlowPreflightResult.failure(**(base | {artifact_field: "unexpected.png"}))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FlowPreflightResult.failure(
            **(base | {artifact_field: "screenshot.png" if artifact_field == "screenshot" else "trace.zip"})
        )  # type: ignore[arg-type]

    result = FlowPreflightResult.failure(
        **(
            base
            | {
                "diagnostic_run_id": "20260816T000000Z-a1b2c3d4",
                artifact_field: "screenshot.png" if artifact_field == "screenshot" else "trace.zip",
            }
        )
    )  # type: ignore[arg-type]
    assert getattr(result, artifact_field) in {"screenshot.png", "trace.zip"}


def test_ready_rejects_all_diagnostic_fields() -> None:
    with pytest.raises(ValidationError):
        FlowPreflightResult(
            success=True,
            status="ready",
            flow_url=FLOW_URL,
            authenticated=True,
            ui_ready=True,
            diagnostic_run_id="20260816T000000Z-a1b2c3d4",
            timestamp=TIMESTAMP,
        )


@pytest.mark.parametrize(
    "timestamp",
    (datetime(2026, 8, 16), datetime(2026, 8, 16, tzinfo=timezone(timedelta(hours=1)))),
)
def test_public_result_requires_a_utc_timestamp(timestamp: datetime) -> None:
    with pytest.raises(ValidationError):
        FlowPreflightResult.ready(timestamp=timestamp)


def test_failure_factory_is_the_only_non_ready_public_constructor() -> None:
    result = FlowPreflightResult.failure(
        status="runtime_busy",
        authenticated=False,
        ui_ready=False,
        failed_step="acquire_runtime_lock",
        timestamp=TIMESTAMP,
    )

    assert result.success is False
    assert result.status == "runtime_busy"
    assert result.screenshot is None
    assert result.trace is None


def test_runtime_observation_and_evidence_are_frozen_internal_values() -> None:
    observation = FlowRuntimeObservation()
    evidence = FlowFailureEvidence(
        screenshot_png=b"image", raw_trace_path=Path("transient.zip"), deny_values=("secret",)
    )

    assert observation.status == "ready"
    assert observation.authenticated is True
    assert observation.ui_ready is True
    assert evidence.raw_trace_path == Path("transient.zip")
    with pytest.raises(FrozenInstanceError):
        observation.ui_ready = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        evidence.raw_trace_path = Path("other.zip")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("error", "status", "failed_step", "authenticated", "ui_ready", "trusted_page"),
    (
        (FlowRuntimeBusyError(), "runtime_busy", "acquire_runtime_lock", False, False, False),
        (FlowBrowserLaunchError(), "browser_launch_failed", "launch_browser", False, False, False),
        (
            FlowAuthenticationTimeoutError(),
            "authentication_required",
            "await_manual_authentication",
            False,
            False,
            False,
        ),
        (
            FlowUnexpectedStateError(failed_step="navigate_flow"),
            "human_intervention_required",
            "navigate_flow",
            False,
            False,
            False,
        ),
        (FlowUiContractError(), "ui_contract_failed", "verify_flow_ui", True, False, True),
        (
            FlowDiagnosticSanitizationError(),
            "human_intervention_required",
            "sanitize_diagnostics",
            True,
            False,
            True,
        ),
    ),
)
def test_internal_errors_have_fixed_sanitized_mappings(
    error: BaseException,
    status: str,
    failed_step: str,
    authenticated: bool,
    ui_ready: bool,
    trusted_page: bool,
) -> None:
    assert getattr(error, "status") == status
    assert getattr(error, "failed_step") == failed_step
    assert getattr(error, "authenticated") is authenticated
    assert getattr(error, "ui_ready") is ui_ready
    assert getattr(error, "trusted_page") is trusted_page
    assert str(error) == ""


def test_launch_error_allows_only_approved_explicit_phase() -> None:
    error = FlowBrowserLaunchError(failed_step="validate_config")
    assert error.failed_step == "validate_config"


@pytest.mark.parametrize("failed_step", ("click_generate", r"C:\\private\\step"))
def test_unexpected_state_error_rejects_non_allowlisted_failed_steps(failed_step: str) -> None:
    with pytest.raises(ValueError):
        FlowUnexpectedStateError(failed_step=failed_step)  # type: ignore[arg-type]


@pytest.mark.parametrize("failed_locator", ("FIRST_BUTTON", r"C:\\Users\\private"))
def test_unexpected_state_error_rejects_non_allowlisted_or_private_locators(
    failed_locator: str,
) -> None:
    with pytest.raises(ValueError):
        FlowUnexpectedStateError(
            failed_step="navigate_flow", failed_locator=failed_locator  # type: ignore[arg-type]
        )


def test_unexpected_state_error_accepts_allowlisted_phase_and_locator() -> None:
    error = FlowUnexpectedStateError(
        failed_step="close_browser", failed_locator="ACCOUNT_IDENTITY"
    )

    assert error.failed_step == "close_browser"
    assert error.failed_locator == "ACCOUNT_IDENTITY"

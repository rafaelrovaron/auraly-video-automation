"""Versioned, sanitized contracts for Google Flow browser preflight."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Literal, Self

from pydantic import Field, model_validator

from auraly_pipeline.models import ContractModel

FLOW_URL: Literal["https://flow.google.com/"] = "https://flow.google.com/"

FlowPreflightStatus = Literal[
    "ready",
    "authentication_required",
    "human_intervention_required",
    "runtime_busy",
    "browser_launch_failed",
    "ui_contract_failed",
]
NonReadyFlowPreflightStatus = Literal[
    "authentication_required",
    "human_intervention_required",
    "runtime_busy",
    "browser_launch_failed",
    "ui_contract_failed",
]
FlowFailedStep = Literal[
    "validate_config",
    "acquire_runtime_lock",
    "launch_browser",
    "navigate_flow",
    "await_manual_authentication",
    "verify_flow_ui",
    "sanitize_diagnostics",
    "close_browser",
]
FlowLocatorName = Literal[
    "FLOW_WORKSPACE", "CREATE_ENTRY_POINT", "PROMPT_INPUT", "ACCOUNT_IDENTITY"
]
FlowPreflightControl = Literal[
    "flow.upload_menu_button",
    "flow.upload_menuitem_send",
    "flow.prompt_host",
    "flow.prompt_editor",
    "flow.generate_button",
]
FlowFailureCategory = Literal[
    "missing",
    "ambiguous",
    "not_visible",
    "unexpected_state",
    "route_mismatch",
    "workspace_mismatch",
    "authentication_required",
    "locator_contract_failed",
    "diagnostic_failure",
]
FlowDiagnosticProcessing = Literal["sanitized", "partially_sanitized", "failed"]

_SAFE_DIAGNOSTIC_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
_FLOW_FAILED_STEPS: frozenset[str] = frozenset(
    {
        "validate_config",
        "acquire_runtime_lock",
        "launch_browser",
        "navigate_flow",
        "await_manual_authentication",
        "verify_flow_ui",
        "sanitize_diagnostics",
        "close_browser",
    }
)
_FLOW_LOCATORS: frozenset[str] = frozenset(
    {"FLOW_WORKSPACE", "CREATE_ENTRY_POINT", "PROMPT_INPUT", "ACCOUNT_IDENTITY"}
)
_BROWSER_LAUNCH_STEPS: frozenset[str] = frozenset(
    {"validate_config", "launch_browser", "navigate_flow"}
)


def _default_failure_category(
    status: FlowPreflightStatus, failed_step: FlowFailedStep
) -> FlowFailureCategory:
    if failed_step == "sanitize_diagnostics":
        return "diagnostic_failure"
    if failed_step == "await_manual_authentication":
        return "authentication_required"
    if failed_step == "navigate_flow":
        return "route_mismatch"
    if status == "ui_contract_failed":
        return "locator_contract_failed"
    return "unexpected_state"


class FlowPrimaryFailure(ContractModel):
    """Only allowlisted preflight facts; never provider text or browser data."""

    phase: FlowFailedStep
    control: FlowPreflightControl | None = None
    category: FlowFailureCategory
    expected_cardinality: Literal[1] | None = None
    observed_cardinality: Literal[0, 1, "2+"] | None = None
    visible: bool | None = None
    enabled: bool | None = None
    ambiguity_detected: bool = False
    unsafe_fallback_required: bool = False


class FlowPreflightResult(ContractModel):
    """The stable, allowlisted result emitted by a Flow preflight."""

    schema_version: Literal["1.0"] = "1.0"
    success: bool
    status: FlowPreflightStatus
    flow_url: Literal["https://flow.google.com/"] = FLOW_URL
    authenticated: bool
    ui_ready: bool
    failed_step: FlowFailedStep | None = None
    failed_locator: FlowLocatorName | None = None
    primary_failure: FlowPrimaryFailure | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    diagnostic_processing: FlowDiagnosticProcessing | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    diagnostic_run_id: str | None = None
    screenshot: Literal["screenshot.png"] | None = None
    trace: Literal["trace.zip"] | None = None
    timestamp: datetime

    @model_validator(mode="after")
    def require_consistent_public_state(self) -> Self:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("timestamp must be UTC")

        if self.success:
            if self.status != "ready":
                raise ValueError("successful preflight must be ready")
            if not self.authenticated or not self.ui_ready:
                raise ValueError("ready preflight requires authenticated UI")
            if any(
                value is not None
                for value in (
                    self.failed_step,
                    self.failed_locator,
                    self.primary_failure,
                    self.diagnostic_processing,
                    self.diagnostic_run_id,
                    self.screenshot,
                    self.trace,
                )
            ):
                raise ValueError("ready preflight cannot publish failure fields")
            return self

        if self.status == "ready":
            raise ValueError("non-ready preflight cannot have ready status")
        if self.failed_step is None:
            raise ValueError("non-ready preflight requires a failed step")
        if self.primary_failure is not None and self.primary_failure.phase != self.failed_step:
            if self.failed_step != "close_browser" and (
                self.failed_step != "sanitize_diagnostics" or self.diagnostic_processing != "failed"
            ):
                raise ValueError("primary failure phase mismatch requires a secondary failure")
        if self.diagnostic_processing == "failed" and (
            self.screenshot is not None or self.trace is not None
        ):
            raise ValueError("failed diagnostic processing cannot publish artifacts")
        if self.diagnostic_run_id is not None and not _SAFE_DIAGNOSTIC_RUN_ID.fullmatch(
            self.diagnostic_run_id
        ):
            raise ValueError("diagnostic run ID is invalid")
        if (self.screenshot is not None or self.trace is not None) and self.diagnostic_run_id is None:
            raise ValueError("artifacts require a diagnostic run ID")
        return self

    @classmethod
    def ready(cls, *, timestamp: datetime) -> Self:
        return cls(
            success=True,
            status="ready",
            flow_url=FLOW_URL,
            authenticated=True,
            ui_ready=True,
            failed_step=None,
            failed_locator=None,
            diagnostic_run_id=None,
            screenshot=None,
            trace=None,
            timestamp=timestamp,
        )

    @classmethod
    def failure(
        cls,
        *,
        status: NonReadyFlowPreflightStatus,
        authenticated: bool,
        ui_ready: bool,
        failed_step: FlowFailedStep,
        timestamp: datetime,
        failed_locator: FlowLocatorName | None = None,
        primary_failure: FlowPrimaryFailure | None = None,
        diagnostic_processing: FlowDiagnosticProcessing | None = None,
        diagnostic_run_id: str | None = None,
        screenshot: Literal["screenshot.png"] | None = None,
        trace: Literal["trace.zip"] | None = None,
    ) -> Self:
        resolved_primary = primary_failure or FlowPrimaryFailure(
            phase=failed_step, category=_default_failure_category(status, failed_step)
        )
        return cls(
            success=False,
            status=status,
            flow_url=FLOW_URL,
            authenticated=authenticated,
            ui_ready=ui_ready,
            failed_step=failed_step,
            failed_locator=failed_locator,
            primary_failure=resolved_primary,
            diagnostic_processing=(
                diagnostic_processing
                if diagnostic_processing is not None
                else "failed" if failed_step == "sanitize_diagnostics" else None
            ),
            diagnostic_run_id=diagnostic_run_id,
            screenshot=screenshot,
            trace=trace,
            timestamp=timestamp,
        )


@dataclass(frozen=True)
class FlowRuntimeObservation:
    """The only successful observation the runtime may report internally."""

    status: Literal["ready"] = "ready"
    authenticated: Literal[True] = True
    ui_ready: Literal[True] = True


@dataclass(frozen=True)
class FlowFailureEvidence:
    """Transient failure evidence; it is never a public result payload."""

    screenshot_png: bytes | None = None
    raw_trace_path: Path | None = None
    deny_values: tuple[str, ...] = ()
    trusted_page: bool = False


class FlowRuntimeError(RuntimeError):
    """Typed internal failure with allowlisted scalar fields only."""

    status: FlowPreflightStatus
    failed_step: FlowFailedStep
    authenticated: bool
    ui_ready: bool
    failed_locator: FlowLocatorName | None
    trusted_page: bool
    evidence: FlowFailureEvidence
    primary_failure: FlowPrimaryFailure | None

    def __init__(
        self,
        *,
        status: FlowPreflightStatus,
        failed_step: FlowFailedStep,
        authenticated: bool,
        ui_ready: bool,
        failed_locator: FlowLocatorName | None = None,
        trusted_page: bool,
        evidence: FlowFailureEvidence | None = None,
        primary_failure: FlowPrimaryFailure | None = None,
    ) -> None:
        super().__init__()
        self.status = status
        self.failed_step = failed_step
        self.authenticated = authenticated
        self.ui_ready = ui_ready
        self.failed_locator = failed_locator
        self.trusted_page = trusted_page
        self.evidence = evidence if evidence is not None else FlowFailureEvidence()
        self.primary_failure = primary_failure or FlowPrimaryFailure(
            phase=failed_step,
            category=_default_failure_category(status, failed_step),
        )


class FlowRuntimeBusyError(FlowRuntimeError):
    def __init__(self, *, evidence: FlowFailureEvidence | None = None) -> None:
        super().__init__(
            status="runtime_busy",
            failed_step="acquire_runtime_lock",
            authenticated=False,
            ui_ready=False,
            trusted_page=False,
            evidence=evidence,
        )


class FlowBrowserLaunchError(FlowRuntimeError):
    def __init__(
        self,
        *,
        failed_step: FlowFailedStep = "launch_browser",
        evidence: FlowFailureEvidence | None = None,
    ) -> None:
        if failed_step not in _BROWSER_LAUNCH_STEPS:
            raise ValueError("browser launch failures require an approved launch phase")
        super().__init__(
            status="browser_launch_failed",
            failed_step=failed_step,
            authenticated=False,
            ui_ready=False,
            trusted_page=False,
            evidence=evidence,
        )


class FlowAuthenticationTimeoutError(FlowRuntimeError):
    def __init__(self, *, evidence: FlowFailureEvidence | None = None) -> None:
        super().__init__(
            status="authentication_required",
            failed_step="await_manual_authentication",
            authenticated=False,
            ui_ready=False,
            trusted_page=False,
            evidence=evidence,
        )


class FlowUnexpectedStateError(FlowRuntimeError):
    def __init__(
        self,
        *,
        failed_step: FlowFailedStep,
        authenticated: bool = False,
        ui_ready: bool = False,
        failed_locator: FlowLocatorName | None = None,
        trusted_page: bool = False,
        evidence: FlowFailureEvidence | None = None,
        failure_category: FlowFailureCategory | None = None,
        primary_failure: FlowPrimaryFailure | None = None,
    ) -> None:
        if failed_step not in _FLOW_FAILED_STEPS:
            raise ValueError("unexpected state requires an allowlisted failed step")
        if failed_locator is not None and failed_locator not in _FLOW_LOCATORS:
            raise ValueError("unexpected state requires an allowlisted failed locator")
        resolved_primary = primary_failure
        if resolved_primary is None and failure_category is not None:
            resolved_primary = FlowPrimaryFailure(phase=failed_step, category=failure_category)
        super().__init__(
            status="human_intervention_required",
            failed_step=failed_step,
            authenticated=authenticated,
            ui_ready=ui_ready,
            failed_locator=failed_locator,
            trusted_page=trusted_page,
            evidence=evidence,
            primary_failure=resolved_primary,
        )


class FlowUiContractError(FlowRuntimeError):
    def __init__(
        self,
        *,
        failed_locator: FlowLocatorName | None = None,
        evidence: FlowFailureEvidence | None = None,
        primary_failure: FlowPrimaryFailure | None = None,
    ) -> None:
        super().__init__(
            status="ui_contract_failed",
            failed_step="verify_flow_ui",
            authenticated=True,
            ui_ready=False,
            failed_locator=failed_locator,
            trusted_page=True,
            evidence=evidence,
            primary_failure=primary_failure,
        )


class FlowDiagnosticSanitizationError(FlowRuntimeError):
    def __init__(
        self,
        *,
        evidence: FlowFailureEvidence | None = None,
        primary_failure: FlowPrimaryFailure | None = None,
    ) -> None:
        super().__init__(
            status="human_intervention_required",
            failed_step="sanitize_diagnostics",
            authenticated=True,
            ui_ready=False,
            trusted_page=True,
            evidence=evidence,
            primary_failure=primary_failure,
        )

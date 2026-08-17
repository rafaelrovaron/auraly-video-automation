"""Versioned, sanitized contracts for Google Flow browser preflight."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Literal, Self

from pydantic import model_validator

from auraly_pipeline.models import ContractModel

FLOW_URL: Literal["https://labs.google/fx/tools/flow"] = "https://labs.google/fx/tools/flow"

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


class FlowPreflightResult(ContractModel):
    """The stable, allowlisted result emitted by a Flow preflight."""

    schema_version: Literal["1.0"] = "1.0"
    success: bool
    status: FlowPreflightStatus
    flow_url: Literal["https://labs.google/fx/tools/flow"] = FLOW_URL
    authenticated: bool
    ui_ready: bool
    failed_step: FlowFailedStep | None = None
    failed_locator: FlowLocatorName | None = None
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
        diagnostic_run_id: str | None = None,
        screenshot: Literal["screenshot.png"] | None = None,
        trace: Literal["trace.zip"] | None = None,
    ) -> Self:
        return cls(
            success=False,
            status=status,
            flow_url=FLOW_URL,
            authenticated=authenticated,
            ui_ready=ui_ready,
            failed_step=failed_step,
            failed_locator=failed_locator,
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


class FlowRuntimeError(RuntimeError):
    """Typed internal failure with allowlisted scalar fields only."""

    status: FlowPreflightStatus
    failed_step: FlowFailedStep
    authenticated: bool
    ui_ready: bool
    failed_locator: FlowLocatorName | None
    trusted_page: bool
    evidence: FlowFailureEvidence

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
    ) -> None:
        super().__init__()
        self.status = status
        self.failed_step = failed_step
        self.authenticated = authenticated
        self.ui_ready = ui_ready
        self.failed_locator = failed_locator
        self.trusted_page = trusted_page
        self.evidence = evidence if evidence is not None else FlowFailureEvidence()


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
    ) -> None:
        if failed_step not in _FLOW_FAILED_STEPS:
            raise ValueError("unexpected state requires an allowlisted failed step")
        if failed_locator is not None and failed_locator not in _FLOW_LOCATORS:
            raise ValueError("unexpected state requires an allowlisted failed locator")
        super().__init__(
            status="human_intervention_required",
            failed_step=failed_step,
            authenticated=authenticated,
            ui_ready=ui_ready,
            failed_locator=failed_locator,
            trusted_page=trusted_page,
            evidence=evidence,
        )


class FlowUiContractError(FlowRuntimeError):
    def __init__(
        self,
        *,
        failed_locator: FlowLocatorName | None = None,
        evidence: FlowFailureEvidence | None = None,
    ) -> None:
        super().__init__(
            status="ui_contract_failed",
            failed_step="verify_flow_ui",
            authenticated=True,
            ui_ready=False,
            failed_locator=failed_locator,
            trusted_page=True,
            evidence=evidence,
        )


class FlowDiagnosticSanitizationError(FlowRuntimeError):
    def __init__(self, *, evidence: FlowFailureEvidence | None = None) -> None:
        super().__init__(
            status="human_intervention_required",
            failed_step="sanitize_diagnostics",
            authenticated=True,
            ui_ready=False,
            trusted_page=True,
            evidence=evidence,
        )

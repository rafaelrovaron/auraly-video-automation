"""Sanitized contracts for the Flow image-generation browser boundary."""

from __future__ import annotations

from collections.abc import Mapping
import re
from types import MappingProxyType
from typing import Literal
from weakref import WeakKeyDictionary

from pydantic import Field, field_validator

from auraly_pipeline.models import ContractModel
from .domain import FlowPrimaryFailure


FlowGenerationLocatorName = Literal[
    "REFERENCE_INPUT",
    "UPLOAD_COMPLETE",
    "GENERATION_PROMPT",
    "GENERATE_CONTROL",
    "GENERATING_INDICATOR",
    "CANDIDATE_GRID",
    "CANDIDATE_SLOT",
    "CANDIDATE_2K_ACTION",
]
FlowGenerationFailedStep = Literal[
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
]
SafeCardinality = Literal[0, 1, "2+"]
FlowCandidateBaselineFailureCategory = Literal[
    "grid_absent_blocked",
    "grid_ambiguous",
    "candidate_hidden",
    "candidate_disabled",
    "candidate_identity_missing",
    "candidate_identity_invalid",
    "candidate_incomplete",
    "candidate_duplicate_fingerprint",
    "candidate_cardinality_invalid",
    "loading_state_present",
    "other_contract_failure",
]

_SAFE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_SAFE_WORKSPACE_PATH = re.compile(
    rf"^(?:fx/tools/flow(?:/[a-z0-9][a-z0-9_-]*)+|project/{_PROJECT_ID})$"
)
_FAILED_STEPS: frozenset[str] = frozenset(
    {
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
    }
)
_LOCATOR_NAMES: frozenset[str] = frozenset(
    {
        "REFERENCE_INPUT",
        "UPLOAD_COMPLETE",
        "GENERATION_PROMPT",
        "GENERATE_CONTROL",
        "GENERATING_INDICATOR",
        "CANDIDATE_GRID",
        "CANDIDATE_SLOT",
        "CANDIDATE_2K_ACTION",
    }
)
_ERROR_FACTS: WeakKeyDictionary[
    BaseException,
    tuple[
        FlowGenerationFailedStep,
        FlowGenerationLocatorName | None,
        FlowPrimaryFailure | None,
        FlowCandidateBaselineFailure | None,
    ],
] = WeakKeyDictionary()
_EMPTY_PERSISTED_FIELDS: Mapping[str, object] = MappingProxyType({})


class FlowWorkspaceIdentity(ContractModel):
    """A restart-safe Flow workspace identity without origin, query, or fragment."""

    workspace_path: str
    fingerprint: str

    @field_validator("workspace_path")
    @classmethod
    def require_allowlisted_workspace_path(cls, value: str) -> str:
        if not _SAFE_WORKSPACE_PATH.fullmatch(value):
            raise ValueError("workspace path is not allowlisted")
        return value

    @field_validator("fingerprint")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SAFE_SHA256.fullmatch(value):
            raise ValueError("fingerprint must be a SHA-256")
        return value


class FlowCandidateObservation(ContractModel):
    """The minimum safe identity used to bind a candidate slot for recovery."""

    fingerprint: str
    semantic_order: int = Field(ge=0)
    completed: Literal[True]

    @field_validator("fingerprint")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if not _SAFE_SHA256.fullmatch(value):
            raise ValueError("fingerprint must be a SHA-256")
        return value


class FlowGenerationObservation(ContractModel):
    """Only verified browser facts that may cross into a durable checkpoint."""

    reference_verified: bool
    prompt_verified: bool

    @property
    def persisted_fields(self) -> Mapping[str, object]:
        """Compatibility view proving no private browser value is persisted."""
        return _EMPTY_PERSISTED_FIELDS


class FlowCandidateBaselineFailure(ContractModel):
    """Bounded, non-sensitive facts explaining a rejected candidate baseline."""

    phase: Literal["candidate_baseline"] = "candidate_baseline"
    category: FlowCandidateBaselineFailureCategory
    grid_count: SafeCardinality
    listitem_count: SafeCardinality
    visible_candidate_count: SafeCardinality
    admissible_candidate_count: SafeCardinality
    blocker_present: bool
    loading_state_present: bool
    duplicate_fingerprint_detected: bool
    hidden_candidate_detected: bool
    invalid_identity_detected: bool
    incomplete_candidate_detected: bool
    malformed_grid_detected: bool


class FlowGenerationRuntimeError(RuntimeError):
    """Typed internal generation failure with allowlisted scalar fields only."""

    def __init__(
        self,
        *,
        failed_step: FlowGenerationFailedStep,
        failed_locator: FlowGenerationLocatorName | None = None,
        primary_failure: FlowPrimaryFailure | None = None,
        candidate_baseline_failure: FlowCandidateBaselineFailure | None = None,
    ) -> None:
        if failed_step not in _FAILED_STEPS:
            raise ValueError("generation error requires an allowlisted failed step")
        if failed_locator is not None and failed_locator not in _LOCATOR_NAMES:
            raise ValueError("generation error requires an allowlisted locator")
        RuntimeError.__init__(self)
        _ERROR_FACTS[self] = (
            failed_step,
            failed_locator,
            primary_failure,
            candidate_baseline_failure,
        )

    @property
    def failed_step(self) -> FlowGenerationFailedStep:
        return _ERROR_FACTS[self][0]

    @property
    def failed_locator(self) -> FlowGenerationLocatorName | None:
        return _ERROR_FACTS[self][1]

    @property
    def primary_failure(self) -> FlowPrimaryFailure | None:
        return _ERROR_FACTS[self][2]

    @property
    def candidate_baseline_failure(self) -> FlowCandidateBaselineFailure | None:
        return _ERROR_FACTS[self][3]

    def __setattr__(self, name: str, value: object) -> None:
        if name in {
            "_failed_step",
            "_failed_locator",
            "_primary_failure",
            "_candidate_baseline_failure",
        }:
            raise AttributeError("generation error facts are read-only")
        super().__setattr__(name, value)


class FlowGenerationUiContractError(FlowGenerationRuntimeError):
    """A required semantic generation element is absent, ambiguous, or unusable."""

    def __init__(
        self,
        *,
        failed_step: FlowGenerationFailedStep = "observe_candidates",
        failed_locator: FlowGenerationLocatorName | None = None,
        primary_failure: FlowPrimaryFailure | None = None,
        candidate_baseline_failure: FlowCandidateBaselineFailure | None = None,
    ) -> None:
        super().__init__(
            failed_step=failed_step,
            failed_locator=failed_locator,
            primary_failure=primary_failure,
            candidate_baseline_failure=candidate_baseline_failure,
        )


class FlowDispatchAmbiguousError(FlowGenerationRuntimeError):
    """A Generate click may have mutated the provider but lacks positive confirmation."""

    def __init__(self) -> None:
        super().__init__(failed_step="confirm_dispatch")


class FlowDownloadCorrelationError(FlowGenerationRuntimeError):
    """A download cannot be proven to originate from its exact 2K slot action."""

    def __init__(self) -> None:
        super().__init__(failed_step="capture_download")

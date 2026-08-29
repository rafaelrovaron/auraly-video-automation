"""Sanitized contracts for the Flow image-generation browser boundary."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from auraly_pipeline.models import ContractModel


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

_SAFE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_WORKSPACE_PATH = re.compile(r"^fx/tools/flow(?:/[a-z0-9][a-z0-9_-]*)+$")
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


class FlowGenerationRuntimeError(RuntimeError):
    """Typed internal generation failure with allowlisted scalar fields only."""

    def __init__(
        self,
        *,
        failed_step: FlowGenerationFailedStep,
        failed_locator: FlowGenerationLocatorName | None = None,
    ) -> None:
        if failed_step not in _FAILED_STEPS:
            raise ValueError("generation error requires an allowlisted failed step")
        if failed_locator is not None and failed_locator not in _LOCATOR_NAMES:
            raise ValueError("generation error requires an allowlisted locator")
        RuntimeError.__init__(self)
        self._failed_step = failed_step
        self._failed_locator = failed_locator

    @property
    def failed_step(self) -> FlowGenerationFailedStep:
        return self._failed_step

    @property
    def failed_locator(self) -> FlowGenerationLocatorName | None:
        return self._failed_locator

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"_failed_step", "_failed_locator"} and hasattr(self, name):
            raise AttributeError("generation error facts are read-only")
        super().__setattr__(name, value)


class FlowGenerationUiContractError(FlowGenerationRuntimeError):
    """A required semantic generation element is absent, ambiguous, or unusable."""

    def __init__(
        self,
        *,
        failed_step: FlowGenerationFailedStep = "observe_candidates",
        failed_locator: FlowGenerationLocatorName | None = None,
    ) -> None:
        super().__init__(failed_step=failed_step, failed_locator=failed_locator)


class FlowDispatchAmbiguousError(FlowGenerationRuntimeError):
    """A Generate click may have mutated the provider but lacks positive confirmation."""

    def __init__(self) -> None:
        super().__init__(failed_step="confirm_dispatch")


class FlowDownloadCorrelationError(FlowGenerationRuntimeError):
    """A download cannot be proven to originate from its exact 2K slot action."""

    def __init__(self) -> None:
        super().__init__(failed_step="capture_download")

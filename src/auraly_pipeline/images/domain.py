from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import ConfigDict, Field, computed_field, field_validator, model_validator

from auraly_pipeline.jobs.domain import Job
from auraly_pipeline.metadata_security import (
    validate_safe_error_message,
    validate_safe_identifier,
)
from auraly_pipeline.models import ContractModel


ImageProvider = Literal["google_flow"]
ImageExecutor = Literal["local_fake", "playwright_python"]
ImageGenerationState = Literal[
    "created", "queued", "generating", "completed", "failed", "blocked"
]
ImageCandidateReviewStatus = Literal[
    "pending_review", "approved", "rejected", "superseded"
]
FlowGenerationStage = Literal[
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
]
FlowCandidateSlotState = Literal[
    "pending",
    "observed",
    "download_intent_recorded",
    "downloaded",
    "ingested",
    "blocked",
]
FlowReconciliationReason = Literal[
    "no_dispatch_proven",
    "existing_dispatch_reconciled",
    "staged_artifact_reconciled",
    "completed_generation_reconciled",
]

_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_WORKSPACE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _validate_workspace_path(value: str) -> str:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or "\x00" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or ".." in windows.parts
        or _SAFE_WORKSPACE_PATH.fullmatch(value) is None
    ):
        raise ValueError("expected a safe workspace-relative path")
    return value


def _validate_flow_workspace_path(value: str) -> str:
    safe_path = _validate_workspace_path(value)
    if PurePosixPath(safe_path).parts[:3] != ("fx", "tools", "flow"):
        raise ValueError("expected an allowlisted relative Flow workspace route")
    return safe_path


def _validate_utc_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be a UTC-aware timestamp")
    return value


class ImageContract(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class ImageGenerateRequest(ImageContract):
    campaign_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    scene_variant_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    idempotency_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", min_length=1, max_length=200
    )
    prompt_snapshot: str = Field(min_length=1, max_length=20_000)
    reference_image_path: str | None = None
    reference_image_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    provider: ImageProvider = "google_flow"
    executor: ImageExecutor = "local_fake"
    generation_contract_version: Literal[
        "image-generation-v1", "flow-generation-v1"
    ] = "image-generation-v1"
    required_candidate_count: Literal[2] = 2
    required_output_resolution: Literal["2K"] = "2K"
    provider_action_confirmed: bool = False
    provider_action_approved_by: str | None = Field(default=None, max_length=120)
    fake_artifact_format_version: Literal["fake-png-v1"] = "fake-png-v1"

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return validate_safe_identifier(value, "idempotency_key", max_length=200)

    @field_validator("reference_image_path")
    @classmethod
    def validate_reference_path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_workspace_path(value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt_snapshot.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def validate_executor_contract(self) -> Self:
        if (self.reference_image_path is None) != (self.reference_image_sha256 is None):
            raise ValueError("reference image path and SHA-256 must be supplied together")
        if self.executor == "local_fake":
            if self.generation_contract_version != "image-generation-v1":
                raise ValueError("local_fake requires image-generation-v1")
            if self.provider_action_confirmed:
                raise ValueError("local_fake does not permit provider-action confirmation")
            if self.provider_action_approved_by is not None:
                raise ValueError("local_fake does not permit a provider-action approver")
            return self
        if self.generation_contract_version != "flow-generation-v1":
            raise ValueError("playwright_python requires flow-generation-v1")
        if self.reference_image_path is None:
            raise ValueError("playwright_python requires a reference image")
        if not self.provider_action_confirmed:
            raise ValueError("playwright_python requires provider-action confirmation")
        if self.provider_action_approved_by is None:
            raise ValueError("playwright_python requires a provider-action approver")
        validate_safe_identifier(
            self.provider_action_approved_by,
            "provider_action_approved_by",
            max_length=120,
        )
        return self


def generation_request_fingerprint(request: ImageGenerateRequest) -> str:
    canonical_payload: dict[str, str | int | None]
    if request.executor == "local_fake":
        canonical_payload = {
            "executor": request.executor,
            "fakeArtifactFormatVersion": request.fake_artifact_format_version,
            "generationContractVersion": request.generation_contract_version,
            "promptSha256": request.prompt_sha256,
            "provider": request.provider,
            "referenceImageSha256": request.reference_image_sha256,
            "sceneVariantId": request.scene_variant_id,
        }
    else:
        canonical_payload = {
            "executor": request.executor,
            "generationContractVersion": request.generation_contract_version,
            "promptSha256": request.prompt_sha256,
            "provider": request.provider,
            "referenceImagePath": request.reference_image_path,
            "referenceImageSha256": request.reference_image_sha256,
            "requiredCandidateCount": request.required_candidate_count,
            "requiredOutputResolution": request.required_output_resolution,
            "sceneVariantId": request.scene_variant_id,
        }
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ImageGeneration(ImageContract):
    image_generation_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    campaign_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    scene_variant_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    job_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    generation_number: int = Field(gt=0)
    idempotency_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", min_length=1, max_length=200
    )
    request_fingerprint: str = Field(pattern=_SHA256_PATTERN, max_length=64)
    prompt_snapshot: str = Field(min_length=1, max_length=20_000)
    prompt_sha256: str = Field(pattern=_SHA256_PATTERN, max_length=64)
    reference_image_path: str | None = None
    reference_image_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    provider: ImageProvider
    executor: ImageExecutor
    provider_state: ImageGenerationState
    created_at: datetime
    updated_at: datetime
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return validate_safe_identifier(value, "idempotency_key", max_length=200)

    @field_validator("reference_image_path")
    @classmethod
    def validate_reference_path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_workspace_path(value)

    @model_validator(mode="after")
    def validate_intent(self) -> Self:
        if hashlib.sha256(self.prompt_snapshot.encode("utf-8")).hexdigest() != self.prompt_sha256:
            raise ValueError("prompt_sha256 does not match prompt_snapshot")
        if (self.reference_image_path is None) != (self.reference_image_sha256 is None):
            raise ValueError("reference image path and SHA-256 must be supplied together")
        return self


class ImageCandidate(ImageContract):
    image_candidate_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    image_generation_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    candidate_index: int = Field(ge=0)
    source_path: str
    sha256: str = Field(pattern=_SHA256_PATTERN, max_length=64)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    size_bytes: int = Field(gt=0)
    format: str = Field(pattern=r"^[a-z0-9]+$", min_length=1, max_length=20)
    review_status: ImageCandidateReviewStatus
    approved_at: datetime | None = None
    approved_by: str | None = Field(default=None, max_length=120)
    rejected_at: datetime | None = None
    rejected_by: str | None = Field(default=None, max_length=120)
    rejection_reason: str | None = Field(default=None, max_length=512)
    superseded_at: datetime | None = None
    superseded_by_candidate_id: str | None = Field(
        default=None, pattern=_UUID_PATTERN, max_length=36
    )
    created_at: datetime
    updated_at: datetime

    _source_path = field_validator("source_path")(_validate_workspace_path)

    @model_validator(mode="after")
    def validate_review_audit(self) -> Self:
        if (self.approved_at is None) != (self.approved_by is None):
            raise ValueError("approval timestamp and actor must be supplied together")
        rejected = (self.rejected_at, self.rejected_by, self.rejection_reason)
        if any(value is not None for value in rejected) and not all(
            value is not None for value in rejected
        ):
            raise ValueError("rejection timestamp, actor, and reason must be supplied together")
        if (self.superseded_at is None) != (self.superseded_by_candidate_id is None):
            raise ValueError("supersession timestamp and successor must be supplied together")
        if self.review_status == "approved" and self.approved_at is None:
            raise ValueError("approved candidate requires approval metadata")
        if self.review_status == "rejected" and self.rejected_at is None:
            raise ValueError("rejected candidate requires rejection metadata")
        if self.review_status == "superseded" and self.superseded_at is None:
            raise ValueError("superseded candidate requires supersession metadata")
        if self.approved_by is not None:
            validate_safe_identifier(self.approved_by, "approved_by", max_length=120)
        if self.rejected_by is not None:
            validate_safe_identifier(self.rejected_by, "rejected_by", max_length=120)
        if self.rejection_reason is not None:
            validate_safe_error_message(self.rejection_reason, "rejection_reason")
        return self


_FLOW_RUN_STAGES = frozenset(
    {
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
)
_FLOW_SLOT_STATES = frozenset(
    {
        "pending",
        "observed",
        "download_intent_recorded",
        "downloaded",
        "ingested",
        "blocked",
    }
)
_FLOW_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "prepared": frozenset({"inputs_verified", "blocked", "failed"}),
    "inputs_verified": frozenset({"dispatch_intent_recorded", "blocked", "failed"}),
    "dispatch_intent_recorded": frozenset(
        {"dispatch_confirmed", "ambiguous", "blocked", "failed"}
    ),
    "dispatch_confirmed": frozenset({"candidates_observed", "blocked", "failed"}),
    "candidates_observed": frozenset({"downloading", "blocked", "failed"}),
    "downloading": frozenset({"completed", "blocked", "failed"}),
    "ambiguous": frozenset(
        {"prepared", "dispatch_confirmed", "candidates_observed", "downloading", "blocked", "failed"}
    ),
    "completed": frozenset(),
    "blocked": frozenset(),
    "failed": frozenset(),
}
_FLOW_SLOT_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"observed", "blocked"}),
    "observed": frozenset({"download_intent_recorded", "blocked"}),
    "download_intent_recorded": frozenset({"downloaded", "blocked"}),
    "downloaded": frozenset({"ingested", "blocked"}),
    "ingested": frozenset(),
    "blocked": frozenset(),
}
_FLOW_FAILURE_CODES = frozenset(
    {
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
    }
)


def ensure_flow_run_transition(
    current: FlowGenerationStage, target: FlowGenerationStage
) -> None:
    if current not in _FLOW_RUN_STAGES or target not in _FLOW_RUN_STAGES:
        raise ValueError("unknown Flow generation stage")
    if target not in _FLOW_RUN_TRANSITIONS[current]:
        raise ValueError(f"illegal Flow generation transition: {current} -> {target}")


def ensure_flow_slot_transition(
    current: FlowCandidateSlotState, target: FlowCandidateSlotState
) -> None:
    if current not in _FLOW_SLOT_STATES or target not in _FLOW_SLOT_STATES:
        raise ValueError("unknown Flow candidate slot state")
    if target not in _FLOW_SLOT_TRANSITIONS[current]:
        raise ValueError(f"illegal Flow candidate slot transition: {current} -> {target}")


class FlowGenerationRun(ImageContract):
    flow_generation_run_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    image_generation_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    stage: FlowGenerationStage
    required_candidate_count: Literal[2] = 2
    required_resolution: Literal["2K"] = "2K"
    provider_workspace_path: str | None = None
    provider_workspace_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    dispatch_attempt_number: int = Field(default=1, ge=1)
    dispatch_intent_at: datetime | None = None
    dispatch_confirmed_at: datetime | None = None
    grid_evidence_path: str | None = None
    grid_evidence_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    last_failure_code: str | None = Field(default=None, max_length=120)
    provider_action_approved_by: str = Field(min_length=1, max_length=120)
    provider_action_approved_at: datetime
    created_at: datetime
    updated_at: datetime

    _provider_workspace_path = field_validator("provider_workspace_path")(
        lambda value: None if value is None else _validate_flow_workspace_path(value)
    )
    _grid_evidence_path = field_validator("grid_evidence_path")(
        lambda value: None if value is None else _validate_workspace_path(value)
    )

    @field_validator("provider_action_approved_by")
    @classmethod
    def validate_approval_actor(cls, value: str) -> str:
        return validate_safe_identifier(value, "provider_action_approved_by", max_length=120)

    @field_validator("last_failure_code")
    @classmethod
    def validate_failure_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        safe_code = validate_safe_identifier(value, "last_failure_code", max_length=120)
        if safe_code not in _FLOW_FAILURE_CODES:
            raise ValueError("last_failure_code is not an allowlisted Flow failure code")
        return safe_code

    @field_validator(
        "provider_action_approved_at",
        "created_at",
        "updated_at",
        "dispatch_intent_at",
        "dispatch_confirmed_at",
    )
    @classmethod
    def validate_utc_timestamps(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "timestamp")
        return _validate_utc_timestamp(value, field_name)

    @model_validator(mode="after")
    def validate_flow_checkpoint(self) -> Self:
        if (self.provider_workspace_path is None) != (
            self.provider_workspace_fingerprint is None
        ):
            raise ValueError("workspace path and fingerprint must be supplied together")
        if (self.grid_evidence_path is None) != (self.grid_evidence_sha256 is None):
            raise ValueError("grid evidence path and SHA-256 must be supplied together")
        if self.dispatch_confirmed_at is not None and self.dispatch_intent_at is None:
            raise ValueError("dispatch confirmation requires a prior dispatch intent")
        if self.grid_evidence_path is not None and self.stage not in {
            "candidates_observed",
            "downloading",
            "completed",
            "blocked",
            "failed",
        }:
            raise ValueError("grid evidence requires candidate observation")
        if self.grid_evidence_path is not None and self.dispatch_confirmed_at is None:
            raise ValueError("grid evidence requires confirmed dispatch")
        if self.stage in {"prepared", "inputs_verified"}:
            if self.dispatch_intent_at is not None or self.dispatch_confirmed_at is not None:
                raise ValueError("pre-dispatch stage must not retain dispatch checkpoints")
        elif self.stage == "dispatch_intent_recorded":
            if self.dispatch_intent_at is None:
                raise ValueError("dispatch intent stage requires an intent timestamp")
            if self.dispatch_confirmed_at is not None:
                raise ValueError("dispatch intent stage must not retain a confirmation timestamp")
        elif self.stage == "ambiguous":
            if self.dispatch_intent_at is None:
                raise ValueError("ambiguous stage requires a durable dispatch intent")
            if self.dispatch_confirmed_at is not None:
                raise ValueError("ambiguous stage must not retain a confirmation timestamp")
        elif self.stage in {"dispatch_confirmed", "candidates_observed", "downloading", "completed"}:
            if self.dispatch_intent_at is None or self.dispatch_confirmed_at is None:
                raise ValueError("post-dispatch stage requires intent and confirmation timestamps")
        if self.provider_action_approved_at > self.created_at:
            raise ValueError("provider-action approval must precede run creation")
        if self.updated_at < self.created_at:
            raise ValueError("run updated_at must not precede created_at")
        if self.dispatch_intent_at is not None:
            if self.dispatch_intent_at < self.created_at or self.dispatch_intent_at > self.updated_at:
                raise ValueError("dispatch intent timestamp is outside run chronology")
        if self.dispatch_confirmed_at is not None:
            dispatch_intent_at = self.dispatch_intent_at
            if dispatch_intent_at is None:
                raise ValueError("dispatch confirmation requires a prior dispatch intent")
            if self.dispatch_confirmed_at < dispatch_intent_at:
                raise ValueError("dispatch confirmation must not precede intent")
            if self.dispatch_confirmed_at > self.updated_at:
                raise ValueError("dispatch confirmation timestamp is outside run chronology")
        return self


class FlowCandidateSlot(ImageContract):
    flow_candidate_slot_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    flow_generation_run_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    slot_index: int = Field(ge=0, le=1)
    provider_slot_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    state: FlowCandidateSlotState
    download_intent_at: datetime | None = None
    staging_path: str | None = None
    staged_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    image_candidate_id: str | None = Field(default=None, pattern=_UUID_PATTERN, max_length=36)
    created_at: datetime
    updated_at: datetime

    _staging_path = field_validator("staging_path")(
        lambda value: None if value is None else _validate_workspace_path(value)
    )

    @field_validator("created_at", "updated_at", "download_intent_at")
    @classmethod
    def validate_utc_timestamps(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "timestamp")
        return _validate_utc_timestamp(value, field_name)

    @model_validator(mode="after")
    def validate_slot_checkpoint(self) -> Self:
        if (self.staging_path is None) != (self.staged_sha256 is None):
            raise ValueError("staging path and SHA-256 must be supplied together")
        if self.state == "ingested":
            if self.image_candidate_id is None:
                raise ValueError("ingested slot requires an image candidate")
            if self.download_intent_at is None or self.staging_path is None:
                raise ValueError("ingested slot requires a prior downloaded artifact")
        elif self.image_candidate_id is not None:
            raise ValueError("pre-ingestion slot must not link an image candidate")
        if self.state in {"download_intent_recorded", "downloaded", "ingested"}:
            if self.download_intent_at is None:
                raise ValueError("download checkpoint requires a download intent timestamp")
        if self.state in {"downloaded", "ingested"} and self.staging_path is None:
            raise ValueError("downloaded slot requires a staged artifact")
        if self.state in {"observed", "download_intent_recorded", "downloaded", "ingested"}:
            if self.provider_slot_fingerprint is None:
                raise ValueError("observed slot requires a provider fingerprint")
        if self.updated_at < self.created_at:
            raise ValueError("slot updated_at must not precede created_at")
        if self.download_intent_at is not None and (
            self.download_intent_at < self.created_at or self.download_intent_at > self.updated_at
        ):
            raise ValueError("download intent timestamp is outside slot chronology")
        return self


class ImageGenerationSubmission(ImageContract):
    generation: ImageGeneration
    job: Job
    reused: bool

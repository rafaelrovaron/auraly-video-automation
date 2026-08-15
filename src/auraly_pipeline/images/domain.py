from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
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
    executor: Literal["local_fake"] = "local_fake"
    generation_contract_version: Literal["image-generation-v1"] = "image-generation-v1"
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
    def validate_reference_pair(self) -> Self:
        if (self.reference_image_path is None) != (self.reference_image_sha256 is None):
            raise ValueError("reference image path and SHA-256 must be supplied together")
        return self


def generation_request_fingerprint(request: ImageGenerateRequest) -> str:
    canonical = json.dumps(
        {
            "executor": request.executor,
            "fakeArtifactFormatVersion": request.fake_artifact_format_version,
            "generationContractVersion": request.generation_contract_version,
            "promptSha256": request.prompt_sha256,
            "provider": request.provider,
            "referenceImageSha256": request.reference_image_sha256,
            "sceneVariantId": request.scene_variant_id,
        },
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


class ImageGenerationSubmission(ImageContract):
    generation: ImageGeneration
    job: Job
    reused: bool

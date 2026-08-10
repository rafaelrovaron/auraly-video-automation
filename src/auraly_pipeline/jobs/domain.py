from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, Field, JsonValue, computed_field, field_validator, model_validator

from auraly_pipeline.jobs.state_machine import JobStatus
from auraly_pipeline.metadata_security import (
    validate_safe_error_message,
    validate_safe_identifier,
    validate_safe_metadata,
)
from auraly_pipeline.models import ContractModel


class JobContract(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class JobAttemptStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"


class JobExecutionOutcome(StrEnum):
    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    BLOCKED = "blocked"


class RetrySafety(StrEnum):
    IDEMPOTENT = "idempotent"
    MANUAL_ONLY = "manual_only"
    RECONCILE_BEFORE_RETRY = "reconcile_before_retry"


class JobSubmit(JobContract):
    job_type: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$", max_length=100)
    campaign_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        max_length=80,
    )
    scene_variant_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        max_length=36,
    )
    idempotency_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        min_length=1,
        max_length=200,
    )
    input: dict[str, JsonValue] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=-100, le=100)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retry_safety: RetrySafety = RetrySafety.IDEMPOTENT
    status: Literal["queued"] = "queued"

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return validate_safe_identifier(value, "idempotency_key", max_length=200)

    @field_validator("job_type")
    @classmethod
    def validate_job_type_identifier(cls, value: str) -> str:
        return validate_safe_identifier(value, "job_type", max_length=100)

    @model_validator(mode="after")
    def validate_submission(self) -> Self:
        if self.scene_variant_id is not None and self.campaign_id is None:
            raise ValueError("scene_variant_id requires campaign_id")
        validate_safe_metadata(self.input, "input")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def request_fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "campaignId": self.campaign_id,
                "input": self.input,
                "jobType": self.job_type,
                "maxAttempts": self.max_attempts,
                "priority": self.priority,
                "retrySafety": self.retry_safety,
                "sceneVariantId": self.scene_variant_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class JobExecutionResult(JobContract):
    outcome: JobExecutionOutcome
    result: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=80,
    )
    error_message: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.error_code is not None:
            validate_safe_identifier(self.error_code, "error_code", max_length=80)
        validate_safe_metadata(self.result, "result")
        if self.outcome == JobExecutionOutcome.SUCCESS:
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("success outcome cannot contain error details")
        elif self.error_code is None or self.error_message is None:
            raise ValueError("failure outcome requires error details")
        else:
            validate_safe_error_message(self.error_message)
        return self


class JobAttempt(JobContract):
    attempt_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        max_length=36,
    )
    job_id: str
    attempt_number: int = Field(ge=1)
    worker_id: str
    status: JobAttemptStatus
    started_at: datetime
    lease_expires_at: datetime
    finished_at: datetime | None = None
    result: dict[str, JsonValue] | None = None
    error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=80,
    )
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_persisted_attempt(self) -> Self:
        validate_safe_identifier(self.attempt_id, "attempt_id", max_length=80)
        validate_safe_identifier(self.job_id, "job_id", max_length=80)
        validate_safe_identifier(self.worker_id, "worker_id", max_length=120)
        if self.result is not None:
            validate_safe_metadata(self.result, "result")
        if self.error_message is not None:
            validate_safe_error_message(self.error_message)
        if self.error_code is not None:
            validate_safe_identifier(self.error_code, "error_code", max_length=80)
        return self


class JobEvent(JobContract):
    event_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        max_length=36,
    )
    sequence: int = Field(ge=1)
    job_id: str
    event_type: str = Field(pattern=r"^job\.[a-z_]+$", max_length=80)
    timestamp: datetime
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_persisted_event(self) -> Self:
        validate_safe_identifier(self.event_id, "event_id", max_length=80)
        validate_safe_identifier(self.job_id, "job_id", max_length=80)
        validate_safe_metadata(self.metadata, "metadata")
        return self


class Job(JobContract):
    job_id: str
    job_type: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$", max_length=100)
    campaign_id: str | None = None
    scene_variant_id: str | None = None
    status: JobStatus
    priority: int
    idempotency_key: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)
    input: dict[str, JsonValue]
    output: dict[str, JsonValue] | None = None
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    retry_safety: RetrySafety
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    next_retry_at: datetime | None = None
    last_error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=80,
    )
    last_error_message: str | None = None
    attempts: list[JobAttempt] = Field(default_factory=list)
    events: list[JobEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_persisted_job(self) -> Self:
        validate_safe_identifier(self.job_id, "job_id", max_length=80)
        validate_safe_identifier(self.job_type, "job_type", max_length=100)
        validate_safe_identifier(self.idempotency_key, "idempotency_key", max_length=200)
        expected_fingerprint = JobSubmit(
            job_type=self.job_type,
            campaign_id=self.campaign_id,
            scene_variant_id=self.scene_variant_id,
            idempotency_key=self.idempotency_key,
            input=self.input,
            priority=self.priority,
            max_attempts=self.max_attempts,
            retry_safety=self.retry_safety,
        ).request_fingerprint
        if self.request_fingerprint != expected_fingerprint:
            raise ValueError("request_fingerprint does not match persisted job request")
        validate_safe_metadata(self.input, "input")
        if self.output is not None:
            validate_safe_metadata(self.output, "output")
        if self.worker_id is not None:
            validate_safe_identifier(self.worker_id, "worker_id", max_length=120)
        if self.last_error_message is not None:
            validate_safe_error_message(self.last_error_message, "last_error_message")
        if self.last_error_code is not None:
            validate_safe_identifier(self.last_error_code, "last_error_code", max_length=80)
        return self

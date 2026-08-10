from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auraly_pipeline.jobs.domain import (
    JobAttempt,
    JobAttemptStatus,
    JobExecutionOutcome,
    JobExecutionResult,
    JobSubmit,
    RetrySafety,
)


def valid_job_data() -> dict:
    return {
        "jobType": "fake.success",
        "campaignId": "eight-of-cups-pilot",
        "idempotencyKey": "voice-master:eight-of-cups-pilot:v1",
        "input": {"operation": "local-test", "version": 1},
        "priority": 10,
        "maxAttempts": 3,
    }


def test_job_submit_is_typed_and_has_deterministic_fingerprint() -> None:
    first = JobSubmit.model_validate(valid_job_data())
    reordered = deepcopy(valid_job_data())
    reordered["input"] = {"version": 1, "operation": "local-test"}
    second = JobSubmit.model_validate(reordered)

    assert first.job_type == "fake.success"
    assert first.status == "queued"
    assert first.retry_safety == RetrySafety.IDEMPOTENT
    assert first.request_fingerprint == second.request_fingerprint
    assert len(first.request_fingerprint) == 64

    manual = deepcopy(valid_job_data())
    manual["retrySafety"] = "manual_only"
    assert JobSubmit.model_validate(manual).request_fingerprint != first.request_fingerprint


def test_scene_variant_job_requires_campaign_reference() -> None:
    data = valid_job_data()
    data.pop("campaignId")
    data["sceneVariantId"] = "11111111-1111-4111-8111-111111111111"

    with pytest.raises(ValidationError, match="scene_variant_id requires campaign_id"):
        JobSubmit.model_validate(data)


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "token:do-not-store",
        "job-secret-v1",
        "credential:private",
        "api-key:do-not-store",
        "signed-url:do-not-store",
    ],
)
def test_job_submit_rejects_sensitive_idempotency_identifiers(unsafe_key: str) -> None:
    data = valid_job_data()
    data["idempotencyKey"] = unsafe_key

    with pytest.raises(ValidationError, match="idempotency_key contains a sensitive marker"):
        JobSubmit.model_validate(data)


@pytest.mark.parametrize(
    "sensitive_key",
    ["accessToken", "credential", "privateKey", "storageState", "media", "blob"],
)
def test_job_payload_rejects_sensitive_metadata(sensitive_key: str) -> None:
    data = valid_job_data()
    data["input"] = {sensitive_key: "SENSITIVE"}

    with pytest.raises(ValidationError, match="input contains"):
        JobSubmit.model_validate(data)


def test_job_payload_rejects_signed_urls_and_non_finite_numbers() -> None:
    signed = valid_job_data()
    signed["input"] = {
        "notes": "https://storage.example/object?" + "X-Amz-" + "Signature=SENSITIVE"
    }
    non_finite = valid_job_data()
    non_finite["input"] = {"value": float("nan")}

    with pytest.raises(ValidationError, match="input contains forbidden sensitive data"):
        JobSubmit.model_validate(signed)
    with pytest.raises(ValidationError, match="input contains a non-finite number"):
        JobSubmit.model_validate(non_finite)


def test_job_payload_rejects_embedded_media_data_urls() -> None:
    data = valid_job_data()
    data["input"] = {"content": "data:image/png;base64,SENSITIVE"}

    with pytest.raises(ValidationError, match="input contains forbidden sensitive data"):
        JobSubmit.model_validate(data)


def test_job_payload_rejects_generic_embedded_base64() -> None:
    data = valid_job_data()
    data["input"] = {"content": "A" * 1_024}

    with pytest.raises(ValidationError, match="input contains embedded base64 data"):
        JobSubmit.model_validate(data)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "https://storage.example/object?X-Amz-%53ignature=synthetic",
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljc2ln",
        "sk-" + "syntheticvalue1234",
        "AIza" + "SyntheticValue1234567890",
    ],
)
def test_job_payload_rejects_encoded_or_secret_bearing_values(unsafe_value: str) -> None:
    data = valid_job_data()
    data["input"] = {"notes": unsafe_value}

    with pytest.raises(ValidationError):
        JobSubmit.model_validate(data)


def test_handler_result_rejects_api_key_like_value() -> None:
    with pytest.raises(ValidationError):
        JobExecutionResult(
            outcome=JobExecutionOutcome.SUCCESS,
            result={"providerResponse": "sk-" + "syntheticvalue1234"},
        )


def test_persisted_attempt_rejects_raw_private_path_error() -> None:
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    with pytest.raises(ValidationError, match="unsafe diagnostic details"):
        JobAttempt(
            attempt_id="00000000-0000-4000-8000-000000000001",
            job_id="job-1",
            attempt_number=1,
            worker_id="worker-1",
            status=JobAttemptStatus.TERMINAL_FAILURE,
            started_at=now,
            lease_expires_at=now,
            finished_at=now,
            error_code="provider_failure",
            error_message="request failed at C:" + r"\Users\Private\request.json",
        )


def test_job_payload_rejects_secret_patterns_inside_generic_values() -> None:
    data = valid_job_data()
    data["input"] = {"notes": "authorization: Bearer do-not-store"}

    with pytest.raises(ValidationError, match="input contains forbidden sensitive data"):
        JobSubmit.model_validate(data)


def test_job_payload_rejects_oversized_metadata() -> None:
    data = valid_job_data()
    data["input"] = {"notes": "x" * 70_000}

    with pytest.raises(ValidationError, match="input exceeds the safe metadata size limit"):
        JobSubmit.model_validate(data)


def test_execution_result_requires_sanitized_failure_details() -> None:
    with pytest.raises(ValidationError, match="failure outcome requires error details"):
        JobExecutionResult(outcome=JobExecutionOutcome.RETRYABLE_FAILURE)

    result = JobExecutionResult(
        outcome=JobExecutionOutcome.TERMINAL_FAILURE,
        error_code="invalid_local_input",
        error_message="The deterministic local operation failed.",
    )

    assert result.result == {}

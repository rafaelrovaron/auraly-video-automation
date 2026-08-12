from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety


@dataclass(frozen=True)
class JobExecutionContext:
    job_id: str
    job_type: str
    input: dict[str, JsonValue]
    attempt_number: int
    campaign_id: str = ""


class JobHandler(Protocol):
    retry_safety: RetrySafety

    def execute(self, context: JobExecutionContext) -> JobExecutionResult: ...


class SuccessHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.SUCCESS,
            result={"attempt": context.attempt_number, "handler": context.job_type},
        )


class RetryOnceHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        if context.attempt_number == 1:
            return JobExecutionResult(
                outcome=JobExecutionOutcome.RETRYABLE_FAILURE,
                error_code="temporary_local_failure",
                error_message="The deterministic local operation can be retried.",
            )
        return JobExecutionResult(
            outcome=JobExecutionOutcome.SUCCESS,
            result={"attempt": context.attempt_number, "handler": context.job_type},
        )


class RetryAlwaysHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.RETRYABLE_FAILURE,
            error_code="temporary_local_failure",
            error_message="The deterministic local operation can be retried.",
        )


class PermanentFailureHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.TERMINAL_FAILURE,
            error_code="permanent_local_failure",
            error_message="The deterministic local operation failed permanently.",
        )


class BlockingHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.BLOCKED,
            error_code="local_prerequisite_blocked",
            error_message="The deterministic local prerequisite is not ready.",
        )


class SimulatedWorkerCrash(RuntimeError):
    """A deterministic test signal that intentionally leaves the lease active."""


class CrashHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        raise SimulatedWorkerCrash("simulated worker interruption")


def default_fake_handlers() -> dict[str, JobHandler]:
    return {
        "fake.success": SuccessHandler(),
        "fake.retry-once": RetryOnceHandler(),
        "fake.retry-always": RetryAlwaysHandler(),
        "fake.permanent-failure": PermanentFailureHandler(),
        "fake.blocked": BlockingHandler(),
        "fake.crash": CrashHandler(),
    }

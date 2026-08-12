from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.jobs.db_models import JobAttemptRow, JobEventRow, JobRow
from auraly_pipeline.jobs.domain import (
    Job,
    JobAttempt,
    JobAttemptStatus,
    JobEvent,
    JobExecutionOutcome,
    JobExecutionResult,
    JobSubmit,
    RetrySafety,
)
from auraly_pipeline.jobs.handlers import (
    JobExecutionContext,
    JobHandler,
    SimulatedWorkerCrash,
    default_fake_handlers,
)
from auraly_pipeline.jobs.repository import (
    DuplicateIdempotencyRace,
    JobClaimConflict,
    JobRepository,
)
from auraly_pipeline.jobs.state_machine import InvalidJobTransition, JobStatus
from auraly_pipeline.metadata_security import validate_safe_identifier


class JobError(RuntimeError):
    public_message = "The job operation failed safely."


class JobNotFoundError(JobError):
    public_message = "Job not found."


class JobIdempotencyConflictError(JobError):
    public_message = "The idempotency key is already used by a different job request."


class JobReferenceError(JobError):
    public_message = "The job references an unknown or mismatched Campaign or SceneVariant."


class JobPersistenceError(JobError):
    public_message = "The job could not be persisted safely."


class JobHandlerNotFoundError(JobError):
    public_message = "The requested deterministic local job type is not registered."


class JobRetrySafetyError(JobError):
    public_message = "The job retry-safety policy does not match the registered handler."


class JobTransitionError(JobError):
    public_message = "The requested job state transition is not allowed."


class JobClaimError(JobError):
    public_message = "The worker does not own the active job lease."


class JobService:
    def __init__(
        self,
        engine: Engine,
        repository: JobRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        handlers: Mapping[str, JobHandler] | None = None,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        resolved_handlers = default_fake_handlers() if handlers is None else handlers
        self._handlers = dict(resolved_handlers)

    @classmethod
    def for_database(
        cls,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        handlers: Mapping[str, JobHandler] | None = None,
    ) -> JobService:
        migrate_database(database_path)
        engine = create_sqlite_engine(database_path)
        session_factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
        resolved_handlers = handlers
        if resolved_handlers is None:
            from auraly_pipeline.voices.handler import VoiceGenerateHandler

            resolved_handlers = default_fake_handlers()
            resolved_handlers["voice.generate"] = VoiceGenerateHandler(
                session_factory,
                work_root=database_path.resolve().parent / "work",
            )
        return cls(
            engine,
            JobRepository(session_factory),
            clock=clock,
            handlers=resolved_handlers,
        )

    def close(self) -> None:
        self._engine.dispose()

    def submit_job(
        self,
        request: JobSubmit,
        *,
        before_commit: Callable[[Session, JobRow], None] | None = None,
    ) -> Job:
        handler = self._handlers.get(request.job_type)
        if handler is None:
            raise JobHandlerNotFoundError
        if getattr(handler, "retry_safety", None) != request.retry_safety:
            raise JobRetrySafetyError
        self._validate_references(request)
        existing = self._repository.get_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            if before_commit is not None:
                raise JobPersistenceError
            return self._reuse_or_conflict(existing, request)
        try:
            timestamp = self._as_utc(self._clock())
            row = (
                self._repository.create(request, timestamp)
                if before_commit is None
                else self._repository.create(
                    request,
                    timestamp,
                    before_commit=before_commit,
                )
            )
        except DuplicateIdempotencyRace:
            existing = self._repository.get_by_idempotency_key(request.idempotency_key)
            if existing is None:  # pragma: no cover - defensive transaction boundary
                raise JobPersistenceError
            return self._reuse_or_conflict(existing, request)
        except IntegrityError as exc:
            raise JobPersistenceError from exc
        return self._to_domain(row)

    def get_job(self, job_id: str) -> Job:
        row = self._repository.get_by_id(job_id)
        if row is None:
            raise JobNotFoundError
        return self._to_domain(row)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        campaign_id: str | None = None,
        scene_variant_id: str | None = None,
    ) -> list[Job]:
        return [
            self._to_domain(row)
            for row in self._repository.list_jobs(
                status=status.value if status is not None else None,
                campaign_id=campaign_id,
                scene_variant_id=scene_variant_id,
            )
        ]

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> Job | None:
        self._validate_worker(worker_id, lease_seconds)
        now = self._as_utc(self._clock())
        self._repository.recover_stale(now)
        self._repository.activate_due_retries(now)
        try:
            row = self._repository.claim_next(
                worker_id,
                now,
                lease_seconds,
                before_commit=self._validate_persisted_row,
            )
        except (IntegrityError, ValidationError, ValueError) as exc:
            raise JobPersistenceError from exc
        return None if row is None else self._to_domain(row)

    def worker_once(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> Job | None:
        claimed = self.claim_next_job(worker_id, lease_seconds=lease_seconds)
        if claimed is None:
            return None
        handler = self._handlers.get(claimed.job_type)
        if handler is None:
            result = JobExecutionResult(
                outcome=JobExecutionOutcome.BLOCKED,
                error_code="handler_not_registered",
                error_message="No deterministic local handler is registered for this persisted job.",
            )
        elif getattr(handler, "retry_safety", None) != claimed.retry_safety:
            result = JobExecutionResult(
                outcome=JobExecutionOutcome.BLOCKED,
                error_code="handler_retry_safety_mismatch",
                error_message="The registered handler retry policy no longer matches the persisted job.",
            )
        else:
            try:
                result = handler.execute(
                    JobExecutionContext(
                        job_id=claimed.job_id,
                        job_type=claimed.job_type,
                        campaign_id=claimed.campaign_id or "",
                        input=claimed.input,
                        attempt_number=claimed.attempt_count,
                    )
                )
                result = JobExecutionResult.model_validate(result.model_dump())
            except SimulatedWorkerCrash:
                raise
            except Exception:
                result = JobExecutionResult(
                    outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                    error_code="handler_execution_failed",
                    error_message="The deterministic local handler failed safely.",
                )
        try:
            row = self._repository.finish_claim(
                claimed.job_id,
                worker_id,
                claimed.attempt_count,
                result,
                self._as_utc(self._clock()),
                retry_delay_seconds=30 * claimed.attempt_count,
            )
        except JobClaimConflict as exc:
            raise JobClaimError from exc
        return self._to_domain(row)

    def cancel_job(self, job_id: str) -> Job:
        try:
            row = self._repository.cancel(job_id, self._as_utc(self._clock()))
        except InvalidJobTransition as exc:
            raise JobTransitionError from exc
        if row is None:
            raise JobNotFoundError
        return self._to_domain(row)

    def resume_job(self, job_id: str) -> Job:
        try:
            row = self._repository.resume(job_id, self._as_utc(self._clock()))
        except InvalidJobTransition as exc:
            raise JobTransitionError from exc
        if row is None:
            raise JobNotFoundError
        return self._to_domain(row)

    def resume_reconciled_job(self, job_id: str) -> Job:
        try:
            row = self._repository.resume_reconciled(job_id, self._as_utc(self._clock()))
        except InvalidJobTransition as exc:
            raise JobTransitionError from exc
        if row is None:
            raise JobNotFoundError
        return self._to_domain(row)

    def recover_stale_jobs(self) -> list[Job]:
        return [
            self._to_domain(row)
            for row in self._repository.recover_stale(self._as_utc(self._clock()))
        ]

    def renew_lease(
        self,
        job_id: str,
        worker_id: str,
        *,
        attempt_number: int,
        lease_seconds: int = 60,
    ) -> Job:
        self._validate_worker(worker_id, lease_seconds)
        try:
            row = self._repository.renew_lease(
                job_id,
                worker_id,
                attempt_number,
                self._as_utc(self._clock()),
                lease_seconds,
            )
        except JobClaimConflict as exc:
            raise JobClaimError from exc
        return self._to_domain(row)

    @staticmethod
    def _validate_worker(worker_id: str, lease_seconds: int) -> None:
        try:
            validate_safe_identifier(worker_id, "worker_id", max_length=120)
        except ValueError as exc:
            raise ValueError("worker_id must be a safe identifier") from exc
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")

    def _validate_references(self, request: JobSubmit) -> None:
        if request.campaign_id is not None and not self._repository.campaign_exists(
            request.campaign_id
        ):
            raise JobReferenceError("Campaign does not exist")
        if request.scene_variant_id is not None:
            assert request.campaign_id is not None
            if not self._repository.scene_matches_campaign(
                request.scene_variant_id, request.campaign_id
            ):
                raise JobReferenceError("SceneVariant does not belong to Campaign")

    def _reuse_or_conflict(self, row: JobRow, request: JobSubmit) -> Job:
        if row.request_fingerprint != request.request_fingerprint:
            raise JobIdempotencyConflictError
        return self._to_domain(row)

    @classmethod
    def _validate_persisted_row(cls, row: JobRow) -> None:
        cls._to_domain(row)

    @classmethod
    def _to_domain(cls, row: JobRow) -> Job:
        return Job(
            job_id=row.id,
            job_type=row.job_type,
            campaign_id=row.campaign_id,
            scene_variant_id=row.scene_variant_id,
            status=JobStatus(row.status),
            priority=row.priority,
            idempotency_key=row.idempotency_key,
            request_fingerprint=row.request_fingerprint,
            input=row.input_json,
            output=row.output_json,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            retry_safety=RetrySafety(row.retry_safety),
            worker_id=row.worker_id,
            lease_expires_at=cls._optional_utc(row.lease_expires_at),
            created_at=cls._as_utc(row.created_at),
            updated_at=cls._as_utc(row.updated_at),
            queued_at=cls._as_utc(row.queued_at),
            started_at=cls._optional_utc(row.started_at),
            completed_at=cls._optional_utc(row.completed_at),
            cancelled_at=cls._optional_utc(row.cancelled_at),
            next_retry_at=cls._optional_utc(row.next_retry_at),
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
            attempts=[cls._attempt_to_domain(attempt) for attempt in row.attempts],
            events=[cls._event_to_domain(event) for event in row.events],
        )

    @classmethod
    def _attempt_to_domain(cls, row: JobAttemptRow) -> JobAttempt:
        return JobAttempt(
            attempt_id=row.id,
            job_id=row.job_id,
            attempt_number=row.attempt_number,
            worker_id=row.worker_id,
            status=JobAttemptStatus(row.status),
            started_at=cls._as_utc(row.started_at),
            lease_expires_at=cls._as_utc(row.lease_expires_at),
            finished_at=cls._optional_utc(row.finished_at),
            result=row.result_json,
            error_code=row.error_code,
            error_message=row.error_message,
        )

    @classmethod
    def _event_to_domain(cls, row: JobEventRow) -> JobEvent:
        return JobEvent(
            event_id=row.id,
            sequence=row.sequence,
            job_id=row.job_id,
            event_type=row.event_type,
            timestamp=cls._as_utc(row.timestamp),
            metadata=row.metadata_json,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _optional_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else cls._as_utc(value)

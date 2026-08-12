from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, SceneVariantRow
from auraly_pipeline.jobs.db_models import JobAttemptRow, JobEventRow, JobRow
from auraly_pipeline.jobs.domain import JobExecutionResult, JobSubmit, RetrySafety
from auraly_pipeline.jobs.state_machine import InvalidJobTransition, JobStatus, ensure_transition


class DuplicateIdempotencyRace(RuntimeError):
    """Signals that another transaction inserted the idempotency key first."""


class JobClaimConflict(RuntimeError):
    """Signals that a worker no longer owns the active claim."""


class JobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _begin_immediate(session: Session) -> None:
        session.execute(text("BEGIN IMMEDIATE"))

    def get_by_id(self, job_id: str) -> JobRow | None:
        with self._session_factory() as session:
            return session.scalar(
                select(JobRow)
                .where(JobRow.id == job_id)
                .options(selectinload(JobRow.attempts), selectinload(JobRow.events))
            )

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRow | None:
        with self._session_factory() as session:
            return session.scalar(
                select(JobRow)
                .where(JobRow.idempotency_key == idempotency_key)
                .options(selectinload(JobRow.attempts), selectinload(JobRow.events))
            )

    def campaign_exists(self, campaign_id: str) -> bool:
        with self._session_factory() as session:
            return (
                session.scalar(select(CampaignRow.id).where(CampaignRow.id == campaign_id))
                is not None
            )

    def scene_matches_campaign(self, scene_variant_id: str, campaign_id: str) -> bool:
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(SceneVariantRow.id).where(
                        SceneVariantRow.id == scene_variant_id,
                        SceneVariantRow.campaign_id == campaign_id,
                    )
                )
                is not None
            )

    def create(
        self,
        request: JobSubmit,
        now: datetime,
        *,
        before_commit: Callable[[Session, JobRow], None] | None = None,
    ) -> JobRow:
        with self._session_factory() as session:
            row = JobRow(
                id=str(uuid4()),
                job_type=request.job_type,
                campaign_id=request.campaign_id,
                scene_variant_id=request.scene_variant_id,
                status="queued",
                priority=request.priority,
                idempotency_key=request.idempotency_key,
                request_fingerprint=request.request_fingerprint,
                input_json=request.input,
                output_json=None,
                attempt_count=0,
                max_attempts=request.max_attempts,
                retry_safety=request.retry_safety.value,
                worker_id=None,
                lease_expires_at=None,
                created_at=now,
                updated_at=now,
                queued_at=now,
                started_at=None,
                completed_at=None,
                cancelled_at=None,
                next_retry_at=None,
                last_error_code=None,
                last_error_message=None,
            )
            row.events.extend(
                [
                    JobEventRow(
                        id=str(uuid4()),
                        event_type="job.created",
                        timestamp=now,
                        metadata_json={"jobType": request.job_type},
                    ),
                    JobEventRow(
                        id=str(uuid4()),
                        event_type="job.queued",
                        timestamp=now,
                        metadata_json={},
                    ),
                ]
            )
            session.add(row)
            if before_commit is not None:
                before_commit(session, row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if "UNIQUE constraint failed: jobs.idempotency_key" in str(exc.orig):
                    raise DuplicateIdempotencyRace from exc
                raise
            return self._reload(session, row.id)

    def apply(
        self,
        job_id: str,
        *,
        mutate: Callable[[Session, JobRow], None],
    ) -> JobRow:
        with self._session_factory() as session:
            self._begin_immediate(session)
            row = session.get(JobRow, job_id)
            if row is None:
                session.rollback()
                raise JobClaimConflict
            mutate(session, row)
            session.commit()
            return self._reload(session, job_id)

    def list_jobs(
        self,
        *,
        status: str | None = None,
        campaign_id: str | None = None,
        scene_variant_id: str | None = None,
    ) -> list[JobRow]:
        statement: Select[tuple[JobRow]] = select(JobRow).options(
            selectinload(JobRow.attempts), selectinload(JobRow.events)
        )
        if status is not None:
            statement = statement.where(JobRow.status == status)
        if campaign_id is not None:
            statement = statement.where(JobRow.campaign_id == campaign_id)
        if scene_variant_id is not None:
            statement = statement.where(JobRow.scene_variant_id == scene_variant_id)
        statement = statement.order_by(JobRow.created_at, JobRow.id)
        with self._session_factory() as session:
            return list(session.scalars(statement).all())

    def claim_next(
        self,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        *,
        before_commit: Callable[[JobRow], None] | None = None,
    ) -> JobRow | None:
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session:
            candidate = (
                select(JobRow.id)
                .where(JobRow.status == JobStatus.QUEUED.value)
                .where(JobRow.attempt_count < JobRow.max_attempts)
                .order_by(JobRow.priority.desc(), JobRow.queued_at, JobRow.id)
                .limit(1)
                .scalar_subquery()
            )
            claimed_id = session.execute(
                update(JobRow)
                .where(JobRow.id == candidate, JobRow.status == JobStatus.QUEUED.value)
                .values(
                    status=JobStatus.RUNNING.value,
                    worker_id=worker_id,
                    lease_expires_at=lease_expires_at,
                    started_at=func.coalesce(JobRow.started_at, now),
                    updated_at=now,
                    attempt_count=JobRow.attempt_count + 1,
                )
                .returning(JobRow.id)
            ).scalar_one_or_none()
            if claimed_id is None:
                session.rollback()
                return None
            row = session.get(JobRow, claimed_id)
            if row is None:  # pragma: no cover - guarded by atomic UPDATE
                session.rollback()
                return None
            session.add(
                JobAttemptRow(
                    id=str(uuid4()),
                    job_id=row.id,
                    attempt_number=row.attempt_count,
                    worker_id=worker_id,
                    status="running",
                    started_at=now,
                    lease_expires_at=lease_expires_at,
                    finished_at=None,
                    result_json=None,
                    error_code=None,
                    error_message=None,
                )
            )
            session.add_all(
                [
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.claimed",
                        timestamp=now,
                        metadata_json={
                            "attemptNumber": row.attempt_count,
                            "leaseExpiresAt": lease_expires_at.isoformat(),
                            "workerId": worker_id,
                        },
                    ),
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.started",
                        timestamp=now,
                        metadata_json={"attemptNumber": row.attempt_count},
                    ),
                ]
            )
            session.flush()
            persisted = self._reload(session, row.id)
            if before_commit is not None:
                before_commit(persisted)
            session.commit()
            return persisted

    def finish_claim(
        self,
        job_id: str,
        worker_id: str,
        attempt_number: int,
        result: JobExecutionResult,
        now: datetime,
        retry_delay_seconds: int,
    ) -> JobRow:
        with self._session_factory() as session:
            self._begin_immediate(session)
            row = session.scalar(
                select(JobRow).where(
                    JobRow.id == job_id,
                    JobRow.status == JobStatus.RUNNING.value,
                    JobRow.worker_id == worker_id,
                    JobRow.attempt_count == attempt_number,
                    JobRow.lease_expires_at > now,
                )
            )
            if row is None:
                raise JobClaimConflict
            attempt = session.scalar(
                select(JobAttemptRow).where(
                    JobAttemptRow.job_id == job_id,
                    JobAttemptRow.attempt_number == attempt_number,
                    JobAttemptRow.worker_id == worker_id,
                    JobAttemptRow.status == "running",
                    JobAttemptRow.lease_expires_at > now,
                )
            )
            if attempt is None:  # pragma: no cover - database invariant defense
                raise JobClaimConflict

            attempt.finished_at = now
            attempt.result_json = result.result or None
            row.updated_at = now
            row.worker_id = None
            row.lease_expires_at = None
            event_type: str
            event_metadata: dict[str, object] = {"attemptNumber": row.attempt_count}

            if result.outcome == "success":
                target = JobStatus.COMPLETED
                attempt.status = "completed"
                row.output_json = result.result
                row.completed_at = now
                row.last_error_code = None
                row.last_error_message = None
                event_type = "job.completed"
            elif (
                result.outcome == "retryable_failure"
                and row.attempt_count < row.max_attempts
                and row.retry_safety == RetrySafety.IDEMPOTENT.value
            ):
                target = JobStatus.RETRY_SCHEDULED
                attempt.status = "retryable_failure"
                attempt.error_code = result.error_code
                attempt.error_message = result.error_message
                row.last_error_code = result.error_code
                row.last_error_message = result.error_message
                row.next_retry_at = now + timedelta(seconds=retry_delay_seconds)
                event_type = "job.retry_scheduled"
                event_metadata["nextRetryAt"] = row.next_retry_at.isoformat()
            elif result.outcome == "blocked" or (
                result.outcome == "retryable_failure" and row.attempt_count < row.max_attempts
            ):
                target = JobStatus.BLOCKED
                attempt.status = "blocked" if result.outcome == "blocked" else "retryable_failure"
                attempt.error_code = result.error_code
                attempt.error_message = result.error_message
                row.last_error_code = result.error_code
                row.last_error_message = result.error_message
                event_type = "job.blocked"
                if result.outcome == "retryable_failure":
                    event_metadata["reason"] = "automatic_retry_not_permitted"
                    event_metadata["retrySafety"] = row.retry_safety
            else:
                target = JobStatus.FAILED
                attempt.status = "terminal_failure"
                attempt.error_code = result.error_code
                attempt.error_message = result.error_message
                row.last_error_code = result.error_code
                row.last_error_message = result.error_message
                event_type = "job.failed"
                if result.outcome == "retryable_failure":
                    event_metadata["reason"] = "max_attempts_exhausted"

            ensure_transition(JobStatus(row.status), target)
            row.status = target.value
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=row.id,
                    event_type=event_type,
                    timestamp=now,
                    metadata_json=event_metadata,
                )
            )
            session.commit()
            return self._reload(session, row.id)

    def activate_due_retries(self, now: datetime) -> None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            rows = list(
                session.scalars(
                    select(JobRow)
                    .where(
                        JobRow.status == JobStatus.RETRY_SCHEDULED.value,
                        JobRow.next_retry_at <= now,
                    )
                    .order_by(JobRow.next_retry_at, JobRow.id)
                ).all()
            )
            for row in rows:
                ensure_transition(JobStatus(row.status), JobStatus.QUEUED)
                row.status = JobStatus.QUEUED.value
                row.next_retry_at = None
                row.queued_at = now
                row.updated_at = now
                session.add(
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.queued",
                        timestamp=now,
                        metadata_json={"reason": "retry_due"},
                    )
                )
            session.commit()

    def cancel(self, job_id: str, now: datetime) -> JobRow | None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            row = session.get(JobRow, job_id)
            if row is None:
                return None
            ensure_transition(JobStatus(row.status), JobStatus.CANCELLED)
            row.status = JobStatus.CANCELLED.value
            row.cancelled_at = now
            row.next_retry_at = None
            row.updated_at = now
            row.worker_id = None
            row.lease_expires_at = None
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=row.id,
                    event_type="job.cancelled",
                    timestamp=now,
                    metadata_json={},
                )
            )
            session.commit()
            return self._reload(session, row.id)

    def resume(self, job_id: str, now: datetime) -> JobRow | None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            row = session.get(JobRow, job_id)
            if row is None:
                return None
            if row.attempt_count >= row.max_attempts:
                raise InvalidJobTransition(
                    f"invalid job transition: {row.status} -> queued (attempt budget exhausted)"
                )
            if row.retry_safety == RetrySafety.RECONCILE_BEFORE_RETRY.value:
                raise InvalidJobTransition(
                    f"invalid job transition: {row.status} -> queued (reconciliation required)"
                )
            ensure_transition(JobStatus(row.status), JobStatus.QUEUED)
            previous = row.status
            row.status = JobStatus.QUEUED.value
            row.next_retry_at = None
            row.queued_at = now
            row.updated_at = now
            session.add_all(
                [
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.resumed",
                        timestamp=now,
                        metadata_json={"previousStatus": previous},
                    ),
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.queued",
                        timestamp=now,
                        metadata_json={"reason": "manual_resume"},
                    ),
                ]
            )
            session.commit()
            return self._reload(session, row.id)

    def resume_reconciled(self, job_id: str, now: datetime) -> JobRow | None:
        """Resume a blocked reconcile-before-retry job after domain-level reconciliation."""
        with self._session_factory() as session:
            self._begin_immediate(session)
            row = session.get(JobRow, job_id)
            if row is None:
                return None
            if (
                row.status != JobStatus.BLOCKED.value
                or row.retry_safety != RetrySafety.RECONCILE_BEFORE_RETRY.value
                or row.attempt_count >= row.max_attempts
            ):
                raise InvalidJobTransition("invalid reconciled job transition")
            row.status = JobStatus.QUEUED.value
            row.next_retry_at = None
            row.queued_at = now
            row.updated_at = now
            session.add_all(
                [
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.reconciled",
                        timestamp=now,
                        metadata_json={"previousStatus": "blocked"},
                    ),
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.queued",
                        timestamp=now,
                        metadata_json={"reason": "provider_reconciled_no_dispatch"},
                    ),
                ]
            )
            session.commit()
            return self._reload(session, row.id)

    def recover_stale(self, now: datetime) -> list[JobRow]:
        recovered_ids: list[str] = []
        with self._session_factory() as session:
            self._begin_immediate(session)
            rows = list(
                session.scalars(
                    select(JobRow)
                    .where(
                        JobRow.status == JobStatus.RUNNING.value,
                        JobRow.lease_expires_at <= now,
                    )
                    .options(selectinload(JobRow.attempts))
                    .order_by(JobRow.lease_expires_at, JobRow.id)
                ).all()
            )
            for row in rows:
                running_attempts = [item for item in row.attempts if item.status == "running"]
                attempt = next(
                    (item for item in running_attempts if item.attempt_number == row.attempt_count),
                    None,
                )
                if attempt is None or len(running_attempts) != 1:
                    missing_previous_worker = row.worker_id or "unknown"
                    for mismatched_attempt in running_attempts:
                        mismatched_attempt.status = "interrupted"
                        mismatched_attempt.finished_at = now
                        mismatched_attempt.error_code = "orchestration_state_corrupt"
                        mismatched_attempt.error_message = (
                            "The running attempt did not match the active job attempt."
                        )
                    current_attempt_exists = any(
                        item.attempt_number == row.attempt_count for item in row.attempts
                    )
                    if not current_attempt_exists and row.attempt_count >= 1:
                        session.add(
                            JobAttemptRow(
                                id=str(uuid4()),
                                job_id=row.id,
                                attempt_number=row.attempt_count,
                                worker_id=missing_previous_worker,
                                status="interrupted",
                                started_at=row.started_at or now,
                                lease_expires_at=row.lease_expires_at or now,
                                finished_at=now,
                                result_json=None,
                                error_code="orchestration_state_corrupt",
                                error_message="The active local attempt record was missing.",
                            )
                        )
                    row.worker_id = None
                    row.lease_expires_at = None
                    row.updated_at = now
                    row.last_error_code = "orchestration_state_corrupt"
                    row.last_error_message = "The active local attempt record was missing."
                    ensure_transition(JobStatus.RUNNING, JobStatus.FAILED)
                    row.status = JobStatus.FAILED.value
                    session.add_all(
                        [
                            JobEventRow(
                                id=str(uuid4()),
                                job_id=row.id,
                                event_type="job.recovered",
                                timestamp=now,
                                metadata_json={
                                    "attemptNumber": row.attempt_count,
                                    "reason": "missing_active_attempt",
                                },
                            ),
                            JobEventRow(
                                id=str(uuid4()),
                                job_id=row.id,
                                event_type="job.failed",
                                timestamp=now,
                                metadata_json={
                                    "attemptNumber": row.attempt_count,
                                    "reason": "orchestration_state_corrupt",
                                },
                            ),
                        ]
                    )
                    recovered_ids.append(row.id)
                    continue
                previous_worker = row.worker_id
                attempt.status = "interrupted"
                attempt.finished_at = now
                attempt.error_code = "worker_lease_expired"
                attempt.error_message = "The local worker lease expired before completion."
                row.worker_id = None
                row.lease_expires_at = None
                row.updated_at = now
                row.last_error_code = attempt.error_code
                row.last_error_message = attempt.error_message
                session.add(
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=row.id,
                        event_type="job.recovered",
                        timestamp=now,
                        metadata_json={
                            "attemptNumber": row.attempt_count,
                            "workerId": previous_worker or "unknown",
                        },
                    )
                )
                if (
                    row.attempt_count < row.max_attempts
                    and row.retry_safety == RetrySafety.IDEMPOTENT.value
                ):
                    ensure_transition(JobStatus.RUNNING, JobStatus.RETRY_SCHEDULED)
                    row.status = JobStatus.RETRY_SCHEDULED.value
                    row.next_retry_at = now
                    session.add(
                        JobEventRow(
                            id=str(uuid4()),
                            job_id=row.id,
                            event_type="job.retry_scheduled",
                            timestamp=now,
                            metadata_json={
                                "attemptNumber": row.attempt_count,
                                "nextRetryAt": now.isoformat(),
                                "reason": "stale_lease_recovery",
                            },
                        )
                    )
                elif row.attempt_count < row.max_attempts:
                    ensure_transition(JobStatus.RUNNING, JobStatus.BLOCKED)
                    row.status = JobStatus.BLOCKED.value
                    row.next_retry_at = None
                    session.add(
                        JobEventRow(
                            id=str(uuid4()),
                            job_id=row.id,
                            event_type="job.blocked",
                            timestamp=now,
                            metadata_json={
                                "attemptNumber": row.attempt_count,
                                "reason": "recovery_requires_manual_approval",
                                "retrySafety": row.retry_safety,
                            },
                        )
                    )
                else:
                    ensure_transition(JobStatus.RUNNING, JobStatus.FAILED)
                    row.status = JobStatus.FAILED.value
                    session.add(
                        JobEventRow(
                            id=str(uuid4()),
                            job_id=row.id,
                            event_type="job.failed",
                            timestamp=now,
                            metadata_json={
                                "attemptNumber": row.attempt_count,
                                "reason": "max_attempts_exhausted_after_recovery",
                            },
                        )
                    )
                recovered_ids.append(row.id)
            session.commit()
            return [self._reload(session, job_id) for job_id in recovered_ids]

    def renew_lease(
        self,
        job_id: str,
        worker_id: str,
        attempt_number: int,
        now: datetime,
        lease_seconds: int,
    ) -> JobRow:
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session:
            self._begin_immediate(session)
            row = session.scalar(
                select(JobRow).where(
                    JobRow.id == job_id,
                    JobRow.status == JobStatus.RUNNING.value,
                    JobRow.worker_id == worker_id,
                    JobRow.attempt_count == attempt_number,
                    JobRow.lease_expires_at > now,
                )
            )
            if row is None:
                raise JobClaimConflict
            attempt = session.scalar(
                select(JobAttemptRow).where(
                    JobAttemptRow.job_id == job_id,
                    JobAttemptRow.attempt_number == attempt_number,
                    JobAttemptRow.worker_id == worker_id,
                    JobAttemptRow.status == "running",
                    JobAttemptRow.lease_expires_at > now,
                )
            )
            if attempt is None:
                raise JobClaimConflict
            row.lease_expires_at = lease_expires_at
            row.updated_at = now
            attempt.lease_expires_at = lease_expires_at
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=row.id,
                    event_type="job.lease_renewed",
                    timestamp=now,
                    metadata_json={
                        "attemptNumber": row.attempt_count,
                        "leaseExpiresAt": lease_expires_at.isoformat(),
                        "workerId": worker_id,
                    },
                )
            )
            session.commit()
            return self._reload(session, row.id)

    def _reload(self, session: Session, job_id: str) -> JobRow:
        session.expire_all()
        row = session.scalar(
            select(JobRow)
            .where(JobRow.id == job_id)
            .options(selectinload(JobRow.attempts), selectinload(JobRow.events))
        )
        if row is None:  # pragma: no cover - guarded by transaction semantics
            raise RuntimeError("persisted job disappeared")
        return row

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import create_sqlite_engine, sqlite_url
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.jobs.domain import (
    JobExecutionOutcome,
    JobExecutionResult,
    JobSubmit,
    RetrySafety,
)
from auraly_pipeline.jobs.handlers import JobExecutionContext, SimulatedWorkerCrash
from auraly_pipeline.jobs.repository import DuplicateIdempotencyRace, JobRepository
from auraly_pipeline.jobs.service import (
    JobClaimError,
    JobIdempotencyConflictError,
    JobPersistenceError,
    JobReferenceError,
    JobRetrySafetyError,
    JobService,
    JobTransitionError,
)
from tests.test_campaign_domain import valid_campaign_data
from tests.test_jobs import valid_job_data


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _create_campaign(database_path: Path) -> tuple[str, str]:
    service = CampaignService.for_database(database_path)
    campaign = service.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    service.close()
    return campaign.campaign_id, campaign.scene_variants[0].scene_variant_id


def _local_job(
    job_type: str,
    idempotency_key: str,
    *,
    max_attempts: int = 3,
    retry_safety: RetrySafety = RetrySafety.IDEMPOTENT,
) -> JobSubmit:
    return JobSubmit(
        job_type=job_type,
        idempotency_key=idempotency_key,
        input={"operation": "deterministic-local-test"},
        max_attempts=max_attempts,
        retry_safety=retry_safety,
    )


def test_submit_get_list_and_restart_persist_campaign_level_job(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    campaign_id, _ = _create_campaign(database_path)
    request = JobSubmit.model_validate(valid_job_data())
    first = JobService.for_database(database_path, clock=lambda: NOW)

    created = first.submit_job(request)
    first.close()
    restarted = JobService.for_database(database_path, clock=lambda: NOW)
    retrieved = restarted.get_job(created.job_id)
    listed = restarted.list_jobs(campaign_id=campaign_id)
    restarted.close()

    assert retrieved == created
    assert listed == [created]
    assert created.status == "queued"
    assert created.attempt_count == 0
    assert [event.event_type for event in created.events] == ["job.created", "job.queued"]
    assert created.attempts == []


def test_scene_variant_job_requires_existing_matching_campaign(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    campaign_id, scene_variant_id = _create_campaign(database_path)
    service = JobService.for_database(database_path, clock=lambda: NOW)
    data = valid_job_data()
    data["sceneVariantId"] = scene_variant_id

    created = service.submit_job(JobSubmit.model_validate(data))

    assert created.campaign_id == campaign_id
    assert created.scene_variant_id == scene_variant_id

    missing = deepcopy(data)
    missing["idempotencyKey"] = "missing-scene"
    missing["sceneVariantId"] = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(JobReferenceError, match="SceneVariant"):
        service.submit_job(JobSubmit.model_validate(missing))
    service.close()


def test_repository_does_not_misclassify_foreign_key_failure_as_idempotency_race(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "auraly.db"
    initialized = JobService.for_database(database_path, clock=lambda: NOW)
    initialized.close()
    engine = create_sqlite_engine(database_path)
    repository = JobRepository(sessionmaker(engine, expire_on_commit=False, class_=Session))
    request = JobSubmit(
        job_type="fake.success",
        campaign_id="missing-campaign",
        idempotency_key="foreign-key-classification",
        input={},
    )

    with pytest.raises(IntegrityError) as raised:
        repository.create(request, NOW)

    assert not isinstance(raised.value, DuplicateIdempotencyRace)
    engine.dispose()


def test_service_wraps_unexpected_integrity_failure_without_database_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = JobService.for_database(tmp_path / "auraly.db", clock=lambda: NOW)

    def fail_create(request: JobSubmit, now: datetime):
        raise IntegrityError("SENSITIVE SQL", {}, RuntimeError("private database detail"))

    monkeypatch.setattr(service._repository, "create", fail_create)

    with pytest.raises(JobPersistenceError) as raised:
        service.submit_job(_local_job("fake.success", "safe-persistence-error"))
    assert "SENSITIVE" not in raised.value.public_message
    assert "private" not in raised.value.public_message
    service.close()


def test_malformed_event_id_is_rejected_by_storage(tmp_path: Path) -> None:
    database_path = tmp_path / "event-uuid.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "event-uuid-storage"))
    engine = create_sqlite_engine(database_path)
    with pytest.raises(IntegrityError, match="valid UUID"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO job_events (id, job_id, event_type, timestamp, metadata_json) "
                    "VALUES ('not-a-uuid', :job_id, 'job.recovered', :now, '{}')"
                ),
                {"job_id": submitted.job_id, "now": NOW},
            )
    engine.dispose()
    service.close()


def test_claim_rejects_legacy_malformed_event_before_persisting_running(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-event.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "legacy-event-boundary"))
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER enforce_job_event_uuid_insert"))
        connection.execute(
            text(
                "INSERT INTO job_events (id, job_id, event_type, timestamp, metadata_json) "
                "VALUES ('not-a-uuid', :job_id, 'job.recovered', :now, '{}')"
            ),
            {"job_id": submitted.job_id, "now": NOW},
        )
    with pytest.raises(JobPersistenceError):
        service.claim_next_job("worker-safe-boundary")
    with engine.connect() as connection:
        persisted = connection.execute(
            text("SELECT status, attempt_count FROM jobs WHERE id=:job_id"),
            {"job_id": submitted.job_id},
        ).one()
    assert persisted == ("queued", 0)
    engine.dispose()
    service.close()


def test_service_refuses_to_emit_unsafe_metadata_from_tampered_database(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "tampered-event"))
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO job_events (id, job_id, event_type, timestamp, metadata_json) "
                "VALUES ('00000000-0000-4000-8000-000000000001', :job_id, "
                "'job.recovered', :now, :metadata)"
            ),
            {
                "job_id": submitted.job_id,
                "metadata": '{"accessToken":"do-not-emit"}',
                "now": NOW,
            },
        )
    engine.dispose()

    with pytest.raises(ValidationError, match="metadata contains a forbidden sensitive key"):
        service.get_job(submitted.job_id)
    service.close()


@pytest.mark.parametrize(
    ("job_type", "error"),
    [
        ("token.read-boundary-secret", "job_type contains a sensitive marker"),
        ("Uppercase", "String should match pattern"),
    ],
)
def test_service_refuses_to_emit_invalid_tampered_job_type(
    tmp_path: Path, job_type: str, error: str
) -> None:
    database_path = tmp_path / "auraly.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "tampered-job-type"))
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET job_type=:job_type WHERE id=:job_id"),
            {"job_id": submitted.job_id, "job_type": job_type},
        )
    engine.dispose()

    with pytest.raises(ValidationError, match=error):
        service.get_job(submitted.job_id)
    service.close()


@pytest.mark.parametrize(
    ("event_id", "error"),
    [
        ("token=event-id-leak", "String should match pattern"),
        ("not-a-uuid", "String should match pattern"),
    ],
)
def test_service_refuses_to_emit_tampered_invalid_event_id(
    tmp_path: Path, event_id: str, error: str
) -> None:
    database_path = tmp_path / "auraly.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "tampered-event-id"))
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER enforce_job_event_uuid_insert"))
        connection.execute(
            text(
                "INSERT INTO job_events (id, job_id, event_type, timestamp, metadata_json) "
                "VALUES (:event_id, :job_id, 'job.recovered', :now, '{}')"
            ),
            {"event_id": event_id, "job_id": submitted.job_id, "now": NOW},
        )
    engine.dispose()

    with pytest.raises(ValidationError, match=error):
        service.get_job(submitted.job_id)
    service.close()


@pytest.mark.parametrize(
    ("error_code", "error"),
    [
        ("token=read-boundary-leak", "String should match pattern"),
        ("BAD-CODE", "String should match pattern"),
    ],
)
def test_service_refuses_to_emit_tampered_invalid_error_code(
    tmp_path: Path, error_code: str, error: str
) -> None:
    database_path = tmp_path / "auraly.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "tampered-error-code"))
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET last_error_code=:error_code WHERE id=:job_id"),
            {"job_id": submitted.job_id, "error_code": error_code},
        )
    engine.dispose()

    with pytest.raises(ValidationError, match=error):
        service.get_job(submitted.job_id)
    service.close()


def test_service_refuses_to_emit_tampered_raw_error_message(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "tampered-error-message"))
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET last_error_message=:message WHERE id=:job_id"),
            {
                "job_id": submitted.job_id,
                "message": "request failed at C:" + r"\Users\Private\request.json",
            },
        )
    engine.dispose()

    with pytest.raises(ValidationError, match="unsafe diagnostic details"):
        service.get_job(submitted.job_id)
    service.close()


def test_service_refuses_semantically_tampered_request_fingerprint(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "tampered-fingerprint"))
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET request_fingerprint=:fingerprint WHERE id=:job_id"),
            {"job_id": submitted.job_id, "fingerprint": "0" * 64},
        )
    engine.dispose()

    with pytest.raises(ValidationError, match="request_fingerprint does not match"):
        service.get_job(submitted.job_id)
    service.close()


def test_idempotent_submission_reuses_exact_job_and_rejects_conflict(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    _create_campaign(database_path)
    service = JobService.for_database(database_path, clock=lambda: NOW)
    request = JobSubmit.model_validate(valid_job_data())

    first = service.submit_job(request)
    reused = service.submit_job(request)

    assert reused == first
    assert len(service.list_jobs()) == 1
    assert [event.event_type for event in reused.events] == ["job.created", "job.queued"]

    conflicting = valid_job_data()
    conflicting["input"] = {"operation": "different"}
    with pytest.raises(JobIdempotencyConflictError):
        service.submit_job(JobSubmit.model_validate(conflicting))
    assert len(service.list_jobs()) == 1
    service.close()


def test_worker_claim_is_atomic_and_persists_running_attempt(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    first_worker = JobService.for_database(database_path, clock=lambda: NOW)
    second_worker = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = first_worker.submit_job(_local_job("fake.success", "claim-once"))

    claimed = first_worker.claim_next_job("worker-1", lease_seconds=60)
    duplicate_claim = second_worker.claim_next_job("worker-2", lease_seconds=60)

    assert claimed is not None
    assert claimed.job_id == submitted.job_id
    assert claimed.status == "running"
    assert claimed.worker_id == "worker-1"
    assert claimed.attempt_count == 1
    assert len(claimed.attempts) == 1
    assert claimed.attempts[0].status == "running"
    assert [event.event_type for event in claimed.events] == [
        "job.created",
        "job.queued",
        "job.claimed",
        "job.started",
    ]
    assert duplicate_claim is None
    first_worker.close()
    second_worker.close()


@pytest.mark.parametrize("worker_id", ["token=do-not-store", "worker secret", "../private"])
def test_worker_rejects_unsafe_persisted_identifier(tmp_path: Path, worker_id: str) -> None:
    service = JobService.for_database(tmp_path / "auraly.db", clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "safe-worker-id-test"))

    with pytest.raises(ValueError, match="worker_id must be a safe identifier"):
        service.claim_next_job(worker_id)

    assert service.get_job(submitted.job_id).worker_id is None
    service.close()


def test_success_handler_completes_job_and_attempt_with_audit_event(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    service = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.success", "success-once"))

    completed = service.worker_once("worker-1", lease_seconds=60)

    assert completed is not None
    assert completed.job_id == submitted.job_id
    assert completed.status == "completed"
    assert completed.output == {"attempt": 1, "handler": "fake.success"}
    assert completed.worker_id is None
    assert completed.lease_expires_at is None
    assert completed.attempts[0].status == "completed"
    assert completed.attempts[0].finished_at == NOW
    assert completed.events[-1].event_type == "job.completed"
    service.close()


def test_retryable_failure_is_scheduled_then_second_attempt_succeeds(tmp_path: Path) -> None:
    clock = MutableClock()
    service = JobService.for_database(tmp_path / "auraly.db", clock=clock)
    service.submit_job(_local_job("fake.retry-once", "retry-once"))

    scheduled = service.worker_once("worker-1")

    assert scheduled is not None
    assert scheduled.status == "retry_scheduled"
    assert scheduled.next_retry_at == NOW + timedelta(seconds=30)
    assert scheduled.attempts[0].status == "retryable_failure"
    assert service.worker_once("worker-2") is None

    clock.advance(30)
    completed = service.worker_once("worker-2")

    assert completed is not None
    assert completed.status == "completed"
    assert completed.attempt_count == 2
    assert [attempt.attempt_number for attempt in completed.attempts] == [1, 2]
    assert [attempt.status for attempt in completed.attempts] == [
        "retryable_failure",
        "completed",
    ]
    assert "job.retry_scheduled" in [event.event_type for event in completed.events]
    service.close()


def test_non_idempotent_retry_requires_explicit_manual_resume(tmp_path: Path) -> None:
    executions = 0

    class ManualRetryHandler:
        retry_safety = RetrySafety.MANUAL_ONLY

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            nonlocal executions
            executions += 1
            return JobExecutionResult(
                outcome=JobExecutionOutcome.RETRYABLE_FAILURE,
                error_code="manual_retry_required",
                error_message="Explicit operator approval is required before retry.",
            )

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=lambda: NOW,
        handlers={"fake.manual-retry": ManualRetryHandler()},
    )
    submitted = service.submit_job(
        _local_job(
            "fake.manual-retry",
            "manual-retry-safety",
            retry_safety=RetrySafety.MANUAL_ONLY,
        )
    )

    blocked = service.worker_once("worker-1")

    assert blocked is not None and blocked.status == "blocked"
    assert blocked.next_retry_at is None
    assert blocked.attempts[0].status == "retryable_failure"
    assert executions == 1
    assert service.worker_once("worker-2") is None
    resumed = service.resume_job(submitted.job_id)
    assert resumed.status == "queued"
    service.close()


def test_reconcile_before_retry_policy_cannot_use_generic_resume(tmp_path: Path) -> None:
    class ReconcileHandler:
        retry_safety = RetrySafety.RECONCILE_BEFORE_RETRY

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            return JobExecutionResult(
                outcome=JobExecutionOutcome.RETRYABLE_FAILURE,
                error_code="reconciliation_required",
                error_message="Reconciliation is required before another execution.",
            )

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=lambda: NOW,
        handlers={"fake.reconcile": ReconcileHandler()},
    )
    submitted = service.submit_job(
        _local_job(
            "fake.reconcile",
            "reconcile-before-retry",
            retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
        )
    )
    blocked = service.worker_once("worker-1")
    assert blocked is not None and blocked.status == "blocked"

    with pytest.raises(JobTransitionError):
        service.resume_job(submitted.job_id)
    service.close()


def test_handler_retry_capability_must_match_persisted_job_policy(tmp_path: Path) -> None:
    class ManualHandler:
        retry_safety = RetrySafety.MANUAL_ONLY

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            raise AssertionError("mismatched handler must not execute")

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=lambda: NOW,
        handlers={"fake.manual": ManualHandler()},
    )

    with pytest.raises(JobRetrySafetyError):
        service.submit_job(_local_job("fake.manual", "retry-safety-mismatch"))
    assert service.list_jobs() == []
    service.close()


def test_worker_rechecks_persisted_retry_safety_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    initial = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = initial.submit_job(_local_job("fake.success", "restart-policy-drift"))
    initial.close()
    executions = 0

    class DriftedHandler:
        retry_safety = RetrySafety.MANUAL_ONLY

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            nonlocal executions
            executions += 1
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS,
                result={"unsafeExecution": True},
            )

    restarted = JobService.for_database(
        database_path,
        clock=lambda: NOW,
        handlers={"fake.success": DriftedHandler()},
    )

    blocked = restarted.worker_once("worker-1")

    assert blocked is not None and blocked.job_id == submitted.job_id
    assert blocked.status == "blocked"
    assert executions == 0
    assert blocked.attempts[-1].status == "blocked"
    assert blocked.last_error_code == "handler_retry_safety_mismatch"
    assert blocked.events[-1].event_type == "job.blocked"
    restarted.close()


def test_worker_blocks_persisted_job_when_handler_disappears_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "auraly.db"
    initial = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = initial.submit_job(_local_job("fake.success", "restart-handler-missing"))
    initial.close()

    restarted = JobService.for_database(
        database_path,
        clock=lambda: NOW,
        handlers={},
    )

    blocked = restarted.worker_once("worker-1")

    assert blocked is not None and blocked.job_id == submitted.job_id
    assert blocked.status == "blocked"
    assert blocked.attempts[-1].status == "blocked"
    assert blocked.last_error_code == "handler_not_registered"
    assert blocked.events[-1].event_type == "job.blocked"
    restarted.close()


def test_max_attempts_and_permanent_failure_are_terminal(tmp_path: Path) -> None:
    clock = MutableClock()
    service = JobService.for_database(tmp_path / "auraly.db", clock=clock)
    retry_job = service.submit_job(_local_job("fake.retry-always", "max-attempts", max_attempts=2))
    service.worker_once("worker-1")
    clock.advance(30)
    exhausted = service.worker_once("worker-1")

    assert exhausted is not None
    assert exhausted.job_id == retry_job.job_id
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 2
    assert exhausted.attempts[-1].status == "terminal_failure"
    assert exhausted.events[-1].event_type == "job.failed"

    permanent = service.submit_job(_local_job("fake.permanent-failure", "permanent"))
    failed = service.worker_once("worker-2")
    assert failed is not None
    assert failed.job_id == permanent.job_id
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.attempts[0].status == "terminal_failure"
    service.close()


def test_cancel_and_resume_follow_explicit_state_machine(tmp_path: Path) -> None:
    service = JobService.for_database(tmp_path / "auraly.db", clock=lambda: NOW)
    queued = service.submit_job(_local_job("fake.success", "cancel-queued"))

    cancelled = service.cancel_job(queued.job_id)

    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at == NOW
    assert cancelled.events[-1].event_type == "job.cancelled"
    with pytest.raises(JobTransitionError):
        service.cancel_job(cancelled.job_id)

    blocked_job = service.submit_job(_local_job("fake.blocked", "resume-blocked"))
    blocked = service.worker_once("worker-1")
    assert blocked is not None
    assert blocked.job_id == blocked_job.job_id
    assert blocked.status == "blocked"
    resumed = service.resume_job(blocked.job_id)
    assert resumed.status == "queued"
    assert resumed.events[-2].event_type == "job.resumed"
    assert resumed.events[-1].event_type == "job.queued"
    service.close()


def test_blocked_job_cannot_resume_after_attempt_budget_is_exhausted(tmp_path: Path) -> None:
    service = JobService.for_database(tmp_path / "auraly.db", clock=lambda: NOW)
    submitted = service.submit_job(
        _local_job("fake.blocked", "blocked-max-attempt", max_attempts=1)
    )
    blocked = service.worker_once("worker-1")
    assert blocked is not None and blocked.job_id == submitted.job_id
    assert blocked.status == "blocked"

    with pytest.raises(JobTransitionError):
        service.resume_job(submitted.job_id)
    service.close()


def test_stale_running_lease_is_recovered_audited_and_resumed_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "auraly.db"
    first_clock = MutableClock()
    first = JobService.for_database(database_path, clock=first_clock)
    submitted = first.submit_job(_local_job("fake.success", "restart-recovery"))
    claimed = first.claim_next_job("crashed-worker", lease_seconds=10)
    assert claimed is not None
    first.close()

    restarted_clock = MutableClock(NOW + timedelta(seconds=11))
    restarted = JobService.for_database(database_path, clock=restarted_clock)
    recovered = restarted.recover_stale_jobs()

    assert len(recovered) == 1
    assert recovered[0].job_id == submitted.job_id
    assert recovered[0].status == "retry_scheduled"
    assert recovered[0].attempts[0].status == "interrupted"
    assert [event.event_type for event in recovered[0].events][-2:] == [
        "job.recovered",
        "job.retry_scheduled",
    ]

    completed = restarted.worker_once("replacement-worker")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.attempt_count == 2
    restarted.close()


def test_stale_non_idempotent_claim_blocks_instead_of_automatic_retry(tmp_path: Path) -> None:
    clock = MutableClock()

    class ManualHandler:
        retry_safety = RetrySafety.MANUAL_ONLY

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            raise AssertionError("stale recovery must not execute the handler")

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=clock,
        handlers={"fake.manual-stale": ManualHandler()},
    )
    submitted = service.submit_job(
        _local_job(
            "fake.manual-stale",
            "manual-stale-recovery",
            retry_safety=RetrySafety.MANUAL_ONLY,
        )
    )
    service.claim_next_job("crashed-worker", lease_seconds=10)
    clock.advance(11)

    recovered = service.recover_stale_jobs()[0]

    assert recovered.job_id == submitted.job_id
    assert recovered.status == "blocked"
    assert recovered.next_retry_at is None
    assert recovered.attempts[0].status == "interrupted"
    assert recovered.events[-1].event_type == "job.blocked"
    assert recovered.events[-1].metadata["reason"] == "recovery_requires_manual_approval"
    service.close()


def test_crash_handler_leaves_durable_running_claim_for_lease_recovery(tmp_path: Path) -> None:
    service = JobService.for_database(tmp_path / "auraly.db", clock=lambda: NOW)
    submitted = service.submit_job(_local_job("fake.crash", "simulated-crash"))

    with pytest.raises(SimulatedWorkerCrash):
        service.worker_once("worker-1", lease_seconds=10)

    interrupted = service.get_job(submitted.job_id)
    assert interrupted.status == "running"
    assert interrupted.worker_id == "worker-1"
    assert interrupted.attempts[0].status == "running"
    service.close()


def test_stale_recovery_respects_max_attempts_and_becomes_terminal(tmp_path: Path) -> None:
    clock = MutableClock()
    service = JobService.for_database(tmp_path / "auraly.db", clock=clock)
    submitted = service.submit_job(_local_job("fake.success", "stale-max-attempt", max_attempts=1))
    service.claim_next_job("crashed-worker", lease_seconds=10)
    clock.advance(11)

    recovered = service.recover_stale_jobs()

    assert len(recovered) == 1
    assert recovered[0].job_id == submitted.job_id
    assert recovered[0].status == "failed"
    assert recovered[0].attempts[0].status == "interrupted"
    assert recovered[0].events[-2].event_type == "job.recovered"
    assert recovered[0].events[-1].event_type == "job.failed"
    service.close()


def test_stale_recovery_terminally_records_missing_active_attempt(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    initialized = JobService.for_database(database_path, clock=lambda: NOW)
    initialized.close()
    engine = create_engine(sqlite_url(database_path))
    expired = NOW - timedelta(seconds=1)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, job_type, status, priority, idempotency_key, request_fingerprint, "
                "input_json, attempt_count, max_attempts, retry_safety, worker_id, "
                "lease_expires_at, created_at, updated_at, queued_at, started_at) "
                "VALUES ('malformed-running-job', 'fake.success', 'running', 0, "
                "'malformed-running-key', :fingerprint, '{}', 1, 3, 'idempotent', "
                "'missing-attempt-worker', :expired, :now, :now, :now, :now)"
            ),
            {
                "expired": expired,
                "fingerprint": JobSubmit(
                    job_type="fake.success",
                    idempotency_key="malformed-running-key",
                    input={},
                ).request_fingerprint,
                "now": NOW,
            },
        )
    engine.dispose()
    service = JobService.for_database(database_path, clock=lambda: NOW)

    recovered = service.recover_stale_jobs()

    assert len(recovered) == 1
    assert recovered[0].status == "failed"
    assert recovered[0].last_error_code == "orchestration_state_corrupt"
    assert recovered[0].attempts[0].attempt_number == 1
    assert recovered[0].attempts[0].status == "interrupted"
    assert [event.event_type for event in recovered[0].events] == [
        "job.recovered",
        "job.failed",
    ]
    service.close()


def test_stale_recovery_closes_mismatched_running_attempts(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    initialized = JobService.for_database(database_path, clock=lambda: NOW)
    initialized.close()
    engine = create_sqlite_engine(database_path)
    expired = NOW - timedelta(seconds=1)
    request = JobSubmit(
        job_type="fake.success",
        idempotency_key="dangling-running-key",
        input={},
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, job_type, status, priority, idempotency_key, request_fingerprint, "
                "input_json, attempt_count, max_attempts, retry_safety, worker_id, "
                "lease_expires_at, created_at, updated_at, queued_at, started_at) "
                "VALUES ('malformed-dangling-job', 'fake.success', 'running', 0, "
                "'dangling-running-key', :fingerprint, '{}', 1, 3, 'idempotent', "
                "'parent-worker', :expired, :now, :now, :now, :now)"
            ),
            {"expired": expired, "fingerprint": request.request_fingerprint, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO job_attempts "
                "(id, job_id, attempt_number, worker_id, status, started_at, "
                "lease_expires_at, finished_at) VALUES "
                "('00000000-0000-4000-8000-000000000001', 'malformed-dangling-job', "
                "1, 'completed-worker', 'completed', :now, :expired, :now)"
            ),
            {"expired": expired, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO job_attempts "
                "(id, job_id, attempt_number, worker_id, status, started_at, "
                "lease_expires_at) VALUES "
                "('00000000-0000-4000-8000-000000000002', 'malformed-dangling-job', "
                "2, 'rogue-worker', 'running', :now, :expired)"
            ),
            {"expired": expired, "now": NOW},
        )
    engine.dispose()
    service = JobService.for_database(database_path, clock=lambda: NOW)

    recovered = service.recover_stale_jobs()[0]

    assert recovered.status == "failed"
    assert recovered.last_error_code == "orchestration_state_corrupt"
    assert [(item.attempt_number, item.status) for item in recovered.attempts] == [
        (1, "completed"),
        (2, "interrupted"),
    ]
    assert not any(item.status == "running" for item in recovered.attempts)
    service.close()


def test_stale_recovery_closes_multiple_running_attempts(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    clock = MutableClock()
    service = JobService.for_database(database_path, clock=clock)
    submitted = service.submit_job(_local_job("fake.success", "multiple-running-attempts"))
    service.claim_next_job("active-worker", lease_seconds=10)
    service.close()
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO job_attempts "
                "(id, job_id, attempt_number, worker_id, status, started_at, lease_expires_at) "
                "VALUES ('00000000-0000-4000-8000-000000000002', :job_id, 2, "
                "'rogue-worker', 'running', :now, :expired)"
            ),
            {
                "job_id": submitted.job_id,
                "now": NOW,
                "expired": NOW - timedelta(seconds=1),
            },
        )
    engine.dispose()
    clock.advance(11)
    restarted = JobService.for_database(database_path, clock=clock)

    recovered = restarted.recover_stale_jobs()[0]

    assert recovered.status == "failed"
    assert recovered.last_error_code == "orchestration_state_corrupt"
    assert [item.status for item in recovered.attempts] == ["interrupted", "interrupted"]
    restarted.close()


def test_retry_scheduled_job_can_be_cancelled_and_running_job_cannot(tmp_path: Path) -> None:
    service = JobService.for_database(tmp_path / "auraly.db", clock=lambda: NOW)
    retrying = service.submit_job(_local_job("fake.retry-always", "cancel-retry"))
    scheduled = service.worker_once("worker-1")
    assert scheduled is not None and scheduled.job_id == retrying.job_id

    cancelled = service.cancel_job(retrying.job_id)
    assert cancelled.status == "cancelled"
    assert cancelled.next_retry_at is None

    running = service.submit_job(_local_job("fake.success", "running-cannot-cancel"))
    service.claim_next_job("worker-2")
    with pytest.raises(JobTransitionError):
        service.cancel_job(running.job_id)
    service.close()


def test_lease_renewal_requires_owner_and_prevents_old_expiry_recovery(tmp_path: Path) -> None:
    clock = MutableClock()
    service = JobService.for_database(tmp_path / "auraly.db", clock=clock)
    submitted = service.submit_job(_local_job("fake.success", "renew-lease"))
    service.claim_next_job("worker-1", lease_seconds=60)
    clock.advance(30)

    renewed = service.renew_lease(
        submitted.job_id,
        "worker-1",
        attempt_number=1,
        lease_seconds=60,
    )

    assert renewed.lease_expires_at == NOW + timedelta(seconds=90)
    assert renewed.attempts[0].lease_expires_at == NOW + timedelta(seconds=90)
    assert renewed.events[-1].event_type == "job.lease_renewed"
    with pytest.raises(JobClaimError):
        service.renew_lease(
            submitted.job_id,
            "worker-2",
            attempt_number=1,
            lease_seconds=60,
        )
    clock.advance(31)
    assert service.recover_stale_jobs() == []
    service.close()


def test_expired_lease_cannot_be_renewed_or_publish_handler_result(tmp_path: Path) -> None:
    clock = MutableClock()

    class LeaseExpiringHandler:
        retry_safety = RetrySafety.IDEMPOTENT

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            clock.advance(61)
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS,
                result={"source": "expired-claim"},
            )

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=clock,
        handlers={"fake.lease-expiry": LeaseExpiringHandler()},
    )
    submitted = service.submit_job(_local_job("fake.lease-expiry", "expired-claim"))

    with pytest.raises(JobClaimError):
        service.worker_once("worker-1", lease_seconds=60)
    with pytest.raises(JobClaimError):
        service.renew_lease(
            submitted.job_id,
            "worker-1",
            attempt_number=1,
            lease_seconds=60,
        )

    expired = service.get_job(submitted.job_id)
    assert expired.status == "running"
    assert expired.output is None
    assert expired.attempts[0].status == "running"
    recovered = service.recover_stale_jobs()[0]
    assert recovered.status == "retry_scheduled"
    assert recovered.attempts[0].status == "interrupted"
    service.close()


def test_attempt_number_fences_stale_execution_reusing_same_worker_id(tmp_path: Path) -> None:
    clock = MutableClock()
    started = Event()
    release = Event()

    class DelayedHandler:
        retry_safety = RetrySafety.IDEMPOTENT

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            started.set()
            assert release.wait(timeout=5)
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS,
                result={"sourceAttempt": context.attempt_number},
            )

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=clock,
        handlers={"fake.delayed": DelayedHandler()},
    )
    submitted = service.submit_job(_local_job("fake.delayed", "attempt-fencing"))

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_execution = executor.submit(service.worker_once, "reused-worker", lease_seconds=60)
        assert started.wait(timeout=5)
        clock.advance(61)
        assert service.recover_stale_jobs()[0].status == "retry_scheduled"
        clock.advance(30)
        newer_claim = service.claim_next_job("reused-worker", lease_seconds=60)
        assert newer_claim is not None and newer_claim.attempt_count == 2
        release.set()
        with pytest.raises(JobClaimError):
            stale_execution.result(timeout=5)

    persisted = service.get_job(submitted.job_id)
    assert persisted.status == "running"
    assert persisted.output is None
    assert [attempt.status for attempt in persisted.attempts] == ["interrupted", "running"]
    service.close()


def test_unexpected_handler_error_is_sanitized_before_persistence(tmp_path: Path) -> None:
    class ExplodingHandler:
        retry_safety = RetrySafety.IDEMPOTENT

        def execute(self, context: JobExecutionContext):
            raise RuntimeError("SENSITIVE C:\\Users\\Private token=do-not-store")

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=lambda: NOW,
        handlers={"fake.explodes": ExplodingHandler()},
    )
    submitted = service.submit_job(_local_job("fake.explodes", "sanitize-handler"))

    failed = service.worker_once("worker-1")

    assert failed is not None and failed.job_id == submitted.job_id
    assert failed.status == "failed"
    assert failed.last_error_code == "handler_execution_failed"
    serialized = failed.model_dump_json()
    assert "SENSITIVE" not in serialized
    assert "Private" not in serialized
    assert "do-not-store" not in serialized
    service.close()


def test_handler_result_cannot_persist_raw_sensitive_error_details(tmp_path: Path) -> None:
    class UnsafeResultHandler:
        retry_safety = RetrySafety.IDEMPOTENT

        def execute(self, context: JobExecutionContext) -> JobExecutionResult:
            return JobExecutionResult(
                outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                error_code="raw_provider_error",
                error_message="token=do-not-store C:" + r"\Users\Private\request.json",
            )

    service = JobService.for_database(
        tmp_path / "auraly.db",
        clock=lambda: NOW,
        handlers={"fake.unsafe-result": UnsafeResultHandler()},
    )
    submitted = service.submit_job(_local_job("fake.unsafe-result", "unsafe-result"))

    failed = service.worker_once("worker-1")

    assert failed is not None and failed.job_id == submitted.job_id
    assert failed.last_error_code == "handler_execution_failed"
    serialized = failed.model_dump_json()
    assert "do-not-store" not in serialized
    assert "Users" not in serialized
    service.close()

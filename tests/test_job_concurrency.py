from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from threading import Lock

from sqlalchemy import text
from sqlalchemy.orm import Session

from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.domain import Job
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.service import JobService


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _request(key: str) -> JobSubmit:
    return JobSubmit(
        job_type="fake.success",
        idempotency_key=key,
        input={"operation": "concurrency-test"},
    )


def test_concurrent_duplicate_submissions_create_exactly_one_job(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    first = JobService.for_database(database_path, clock=lambda: NOW)
    second = JobService.for_database(database_path, clock=lambda: NOW)
    barrier = Barrier(2)

    def submit(service: JobService) -> str:
        barrier.wait()
        return service.submit_job(_request("concurrent-idempotency")).job_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        identifiers = list(executor.map(submit, [first, second]))

    assert identifiers[0] == identifiers[1]
    assert len(first.list_jobs()) == 1
    first.close()
    second.close()


def test_concurrent_linked_submissions_invoke_creation_callback_once(tmp_path: Path) -> None:
    database_path = tmp_path / "linked-race.db"
    first = JobService.for_database(database_path, clock=lambda: NOW)
    second = JobService.for_database(database_path, clock=lambda: NOW)
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE linked_job_test_rows ("
                "id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, "
                "FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT)"
            )
        )
    engine.dispose()
    barrier = Barrier(2)
    callback_lock = Lock()
    callback_calls = 0

    def create_linked(session: Session, row: JobRow) -> str:
        nonlocal callback_calls
        with callback_lock:
            callback_calls += 1
        session.execute(
            text("INSERT INTO linked_job_test_rows (id,job_id) VALUES ('linked-1',:job_id)"),
            {"job_id": row.id},
        )
        return "linked-1"

    def load_existing(job: Job) -> str:
        load_engine = create_sqlite_engine(database_path)
        with load_engine.connect() as connection:
            linked = connection.execute(
                text("SELECT id FROM linked_job_test_rows WHERE job_id=:job_id"),
                {"job_id": job.job_id},
            ).scalar_one()
        load_engine.dispose()
        return str(linked)

    def submit(service: JobService) -> tuple[str, str]:
        barrier.wait()
        submission = service.submit_linked_job(
            _request("concurrent-linked"), create_linked, load_existing
        )
        return submission.job.job_id, submission.linked

    with ThreadPoolExecutor(max_workers=2) as executor:
        submissions = list(executor.map(submit, [first, second]))

    assert callback_calls == 1
    assert submissions[0] == submissions[1]
    assert len(first.list_jobs()) == 1
    first.close()
    second.close()


def test_two_concurrent_workers_cannot_claim_same_job(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    first = JobService.for_database(database_path, clock=lambda: NOW)
    second = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = first.submit_job(_request("concurrent-claim"))
    barrier = Barrier(2)

    def claim(service_and_worker: tuple[JobService, str]):
        service, worker_id = service_and_worker
        barrier.wait()
        return service.claim_next_job(worker_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, [(first, "worker-1"), (second, "worker-2")]))

    acquired = [job for job in claims if job is not None]
    assert len(acquired) == 1
    assert acquired[0].job_id == submitted.job_id
    persisted = first.get_job(submitted.job_id)
    assert persisted.attempt_count == 1
    assert [attempt.attempt_number for attempt in persisted.attempts] == [1]
    first.close()
    second.close()


def test_two_recovery_processes_record_one_stale_lease_recovery(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    initial = JobService.for_database(database_path, clock=lambda: NOW)
    submitted = initial.submit_job(_request("recover-once"))
    initial.claim_next_job("crashed-worker", lease_seconds=10)
    initial.close()
    expired = NOW + timedelta(seconds=11)
    first = JobService.for_database(database_path, clock=lambda: expired)
    second = JobService.for_database(database_path, clock=lambda: expired)
    barrier = Barrier(2)

    def recover(service: JobService):
        barrier.wait()
        return service.recover_stale_jobs()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(recover, [first, second]))

    assert sorted(len(items) for items in results) == [0, 1]
    persisted = first.get_job(submitted.job_id)
    assert [event.event_type for event in persisted.events].count("job.recovered") == 1
    assert persisted.attempts[0].status == "interrupted"
    first.close()
    second.close()

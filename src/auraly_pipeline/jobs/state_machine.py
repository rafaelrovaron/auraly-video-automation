from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.BLOCKED,
            JobStatus.RETRY_SCHEDULED,
        }
    ),
    JobStatus.RETRY_SCHEDULED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.BLOCKED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class InvalidJobTransition(ValueError):
    """Raised when a persisted job transition is not explicitly allowed."""


def ensure_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidJobTransition(f"invalid job transition: {current.value} -> {target.value}")

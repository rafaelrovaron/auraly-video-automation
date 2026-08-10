from __future__ import annotations

import pytest

from auraly_pipeline.jobs.state_machine import (
    InvalidJobTransition,
    JobStatus,
    ensure_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.QUEUED, JobStatus.RUNNING),
        (JobStatus.QUEUED, JobStatus.CANCELLED),
        (JobStatus.RUNNING, JobStatus.COMPLETED),
        (JobStatus.RUNNING, JobStatus.RETRY_SCHEDULED),
        (JobStatus.RUNNING, JobStatus.FAILED),
        (JobStatus.RUNNING, JobStatus.BLOCKED),
        (JobStatus.RETRY_SCHEDULED, JobStatus.QUEUED),
        (JobStatus.RETRY_SCHEDULED, JobStatus.CANCELLED),
        (JobStatus.BLOCKED, JobStatus.QUEUED),
        (JobStatus.BLOCKED, JobStatus.CANCELLED),
    ],
)
def test_job_state_machine_accepts_explicit_transitions(
    current: JobStatus,
    target: JobStatus,
) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.QUEUED, JobStatus.COMPLETED),
        (JobStatus.RUNNING, JobStatus.QUEUED),
        (JobStatus.COMPLETED, JobStatus.RUNNING),
        (JobStatus.FAILED, JobStatus.QUEUED),
        (JobStatus.CANCELLED, JobStatus.QUEUED),
    ],
)
def test_job_state_machine_rejects_invalid_or_terminal_transitions(
    current: JobStatus,
    target: JobStatus,
) -> None:
    with pytest.raises(InvalidJobTransition, match=f"{current.value} -> {target.value}"):
        ensure_transition(current, target)

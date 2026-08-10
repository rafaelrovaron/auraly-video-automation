from __future__ import annotations

import socket

import pytest

from auraly_pipeline.jobs.handlers import (
    JobExecutionContext,
    SimulatedWorkerCrash,
    default_fake_handlers,
)


EXPECTED_FAKE_HANDLERS = {
    "fake.blocked",
    "fake.crash",
    "fake.permanent-failure",
    "fake.retry-always",
    "fake.retry-once",
    "fake.success",
}


def test_default_handler_registry_contains_only_goal_2_fake_handlers() -> None:
    assert set(default_fake_handlers()) == EXPECTED_FAKE_HANDLERS


@pytest.mark.parametrize(
    "job_type",
    sorted(EXPECTED_FAKE_HANDLERS - {"fake.crash"}),
)
def test_fake_handlers_are_deterministic_and_do_not_use_network(
    job_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Goal 2 fake handlers must not open sockets")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    handler = default_fake_handlers()[job_type]
    context = JobExecutionContext(
        job_id="11111111-1111-4111-8111-111111111111",
        job_type=job_type,
        input={"operation": "deterministic-test"},
        attempt_number=1,
    )

    assert handler.execute(context) == handler.execute(context)


def test_fake_crash_handler_deterministically_simulates_interruption() -> None:
    handler = default_fake_handlers()["fake.crash"]
    context = JobExecutionContext(
        job_id="11111111-1111-4111-8111-111111111111",
        job_type="fake.crash",
        input={},
        attempt_number=1,
    )

    with pytest.raises(SimulatedWorkerCrash):
        handler.execute(context)

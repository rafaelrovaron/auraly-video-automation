from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import cast

import pytest

from auraly_pipeline.flow.config import FlowRuntimeConfig
from auraly_pipeline.flow.diagnostics import FlowDiagnosticWriter
from auraly_pipeline.flow.domain import (
    FlowAuthenticationTimeoutError,
    FlowBrowserLaunchError,
    FlowDiagnosticSanitizationError,
    FlowFailureEvidence,
    FlowPreflightResult,
    FlowRuntimeBusyError,
    FlowRuntimeError,
    FlowRuntimeObservation,
    FlowUiContractError,
    FlowUnexpectedStateError,
)
from auraly_pipeline.flow.service import (
    ConfigResolver,
    DiagnosticWriterFactory,
    FlowPreflightService,
    LockFactory,
    RuntimeFactory,
)


TIMESTAMP = datetime(2026, 8, 16, tzinfo=UTC)


class RecordingLock:
    def __init__(
        self,
        events: list[str],
        *,
        acquire_error: Exception | None = None,
        release_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._acquire_error = acquire_error
        self._release_error = release_error

    def acquire(self) -> None:
        self._events.append("lock.acquire")
        if self._acquire_error is not None:
            raise self._acquire_error

    def release(self) -> None:
        self._events.append("lock.release")
        if self._release_error is not None:
            raise self._release_error


class RecordingRuntime:
    def __init__(self, events: list[str], *, error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    def run(self) -> FlowRuntimeObservation:
        self._events.append("runtime.run")
        try:
            if self._error is not None:
                raise self._error
            return FlowRuntimeObservation()
        finally:
            self._events.append("runtime.close")


class RecordingDiagnosticWriter:
    def __init__(self, events: list[str], *, error: Exception | None = None) -> None:
        self._events = events
        self._error = error
        self.results: list[FlowPreflightResult] = []
        self.evidence: list[FlowFailureEvidence] = []

    def write_failure(
        self,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
    ) -> FlowPreflightResult:
        self._events.append("diagnostics.write")
        self.results.append(result)
        self.evidence.append(evidence)
        if self._error is not None:
            raise self._error
        return result


class SequencedDiagnosticWriter(RecordingDiagnosticWriter):
    def __init__(self, events: list[str], outcomes: list[Exception | FlowPreflightResult]) -> None:
        super().__init__(events)
        self._outcomes = outcomes

    def write_failure(
        self,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
    ) -> FlowPreflightResult:
        super().write_failure(result, evidence=evidence)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class UnknownRuntimeError(RuntimeError):
    def __init__(self, *, trusted_page: bool, message: str) -> None:
        super().__init__(message)
        self.trusted_page = trusted_page


def _config(tmp_path: Path) -> FlowRuntimeConfig:
    return FlowRuntimeConfig(
        profile_dir=tmp_path / "profile",
        diagnostics_dir=tmp_path / "diagnostics",
        lock_path=tmp_path / "locks" / "flow.lock",
        staging_root=tmp_path / "staging",
        login_timeout_seconds=60,
        navigation_timeout_seconds=30,
    )


def _resolver(config: FlowRuntimeConfig, calls: list[dict[str, object]]) -> ConfigResolver:
    def resolve(
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowRuntimeConfig:
        calls.append(
            {
                "profile_dir": profile_dir,
                "diagnostics_dir": diagnostics_dir,
                "login_timeout_seconds": login_timeout_seconds,
                "navigation_timeout_seconds": navigation_timeout_seconds,
            }
        )
        return config

    return resolve


def _runtime_error(error_type: type[FlowRuntimeError]) -> FlowRuntimeError:
    if error_type is FlowRuntimeBusyError:
        return FlowRuntimeBusyError()
    if error_type is FlowBrowserLaunchError:
        return FlowBrowserLaunchError()
    if error_type is FlowAuthenticationTimeoutError:
        return FlowAuthenticationTimeoutError()
    if error_type is FlowUnexpectedStateError:
        return FlowUnexpectedStateError(failed_step="navigate_flow")
    if error_type is FlowUiContractError:
        return FlowUiContractError()
    if error_type is FlowDiagnosticSanitizationError:
        return FlowDiagnosticSanitizationError()
    raise AssertionError(f"unsupported error type: {error_type}")


def _service(
    tmp_path: Path,
    *,
    runtime_error: Exception | None = None,
    lock_acquire_error: Exception | None = None,
    lock_release_error: Exception | None = None,
    writer_error: Exception | None = None,
    writer_outcomes: list[Exception | FlowPreflightResult] | None = None,
    use_real_writer: bool = False,
) -> tuple[FlowPreflightService, list[str], RecordingDiagnosticWriter, list[dict[str, object]]]:
    events: list[str] = []
    resolver_calls: list[dict[str, object]] = []
    config = _config(tmp_path)
    writer: RecordingDiagnosticWriter
    if writer_outcomes is None:
        writer = RecordingDiagnosticWriter(events, error=writer_error)
    else:
        writer = SequencedDiagnosticWriter(events, writer_outcomes)

    def lock_factory(path: Path) -> RecordingLock:
        assert path == config.lock_path
        return RecordingLock(
            events,
            acquire_error=lock_acquire_error,
            release_error=lock_release_error,
        )

    def runtime_factory(received_config: FlowRuntimeConfig) -> RecordingRuntime:
        assert received_config is config
        return RecordingRuntime(events, error=runtime_error)

    def writer_factory(diagnostics_dir: Path, staging_root: Path) -> RecordingDiagnosticWriter:
        assert diagnostics_dir == config.diagnostics_dir
        assert staging_root == config.staging_root
        return writer

    service = FlowPreflightService(
        _config_resolver=_resolver(config, resolver_calls),
        _lock_factory=cast(LockFactory, lock_factory),
        _runtime_factory=cast(RuntimeFactory, runtime_factory),
        _diagnostic_writer_factory=(
            FlowDiagnosticWriter if use_real_writer else cast(DiagnosticWriterFactory, writer_factory)
        ),
        _now=lambda: TIMESTAMP,
    )
    return service, events, writer, resolver_calls


def test_service_holds_lock_until_runtime_has_closed_returns_ready_and_forwards_options(
    tmp_path: Path,
) -> None:
    service, events, writer, resolver_calls = _service(tmp_path)

    result = service.preflight(
        profile_dir=Path("profile-option"),
        diagnostics_dir=Path("diagnostics-option"),
        login_timeout_seconds=12,
        navigation_timeout_seconds=34,
    )

    assert result == FlowPreflightResult.ready(timestamp=TIMESTAMP)
    assert events == ["lock.acquire", "runtime.run", "runtime.close", "lock.release"]
    assert writer.results == []
    assert resolver_calls == [
        {
            "profile_dir": Path("profile-option"),
            "diagnostics_dir": Path("diagnostics-option"),
            "login_timeout_seconds": 12,
            "navigation_timeout_seconds": 34,
        }
    ]


@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        (FlowRuntimeBusyError, "runtime_busy"),
        (FlowBrowserLaunchError, "browser_launch_failed"),
        (FlowAuthenticationTimeoutError, "authentication_required"),
        (FlowUnexpectedStateError, "human_intervention_required"),
        (FlowUiContractError, "ui_contract_failed"),
        (FlowDiagnosticSanitizationError, "human_intervention_required"),
    ],
)
def test_service_maps_typed_errors_to_exact_public_statuses(
    tmp_path: Path,
    error_type: type[FlowRuntimeError],
    status: str,
) -> None:
    service, _, writer, _ = _service(tmp_path, runtime_error=_runtime_error(error_type))

    result = service.preflight()

    assert result.status == status
    assert result.success is False
    assert writer.results == [result]


def test_config_failure_returns_sanitized_result_before_constructing_lock_or_runtime(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    def failing_resolver(
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowRuntimeConfig:
        raise FlowBrowserLaunchError(failed_step="validate_config")

    def unexpected_lock_factory(path: Path) -> RecordingLock:
        events.append("lock.constructed")
        return RecordingLock(events)

    def unexpected_runtime_factory(config: FlowRuntimeConfig) -> RecordingRuntime:
        events.append("runtime.constructed")
        return RecordingRuntime(events)

    service = FlowPreflightService(
        _config_resolver=failing_resolver,
        _lock_factory=cast(LockFactory, unexpected_lock_factory),
        _runtime_factory=cast(RuntimeFactory, unexpected_runtime_factory),
        _now=lambda: TIMESTAMP,
    )

    result = service.preflight()

    assert result == FlowPreflightResult.failure(
        status="browser_launch_failed",
        authenticated=False,
        ui_ready=False,
        failed_step="validate_config",
        timestamp=TIMESTAMP,
    )
    assert events == []


def test_busy_lock_never_constructs_or_runs_the_runtime(tmp_path: Path) -> None:
    service, events, writer, _ = _service(tmp_path, lock_acquire_error=FlowRuntimeBusyError())

    result = service.preflight()

    assert result.status == "runtime_busy"
    assert events == ["lock.acquire", "diagnostics.write"]
    assert writer.results == [result]


@pytest.mark.parametrize(
    "runtime_error",
    [
        FlowAuthenticationTimeoutError(),
        FlowUnexpectedStateError(failed_step="navigate_flow"),
        FlowDiagnosticSanitizationError(),
    ],
)
def test_every_non_ready_runtime_result_publishes_result_json_after_lock_release(
    tmp_path: Path,
    runtime_error: FlowRuntimeError,
) -> None:
    service, events, _, _ = _service(
        tmp_path,
        runtime_error=runtime_error,
        use_real_writer=True,
    )

    result = service.preflight()

    assert events == ["lock.acquire", "runtime.run", "runtime.close", "lock.release"]
    assert result.diagnostic_run_id is not None
    run_dir = _config(tmp_path).diagnostics_dir / result.diagnostic_run_id
    published = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert published["status"] == result.status
    assert published["failedStep"] == result.failed_step


def test_diagnostic_sanitization_failure_returns_fresh_result_only_failure(tmp_path: Path) -> None:
    service, events, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(),
        writer_error=FlowDiagnosticSanitizationError(),
    )

    result = service.preflight()

    assert events == [
        "lock.acquire",
        "runtime.run",
        "runtime.close",
        "lock.release",
        "diagnostics.write",
        "diagnostics.write",
    ]
    assert writer.results[0].status == "ui_contract_failed"
    assert writer.results[1] == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        timestamp=TIMESTAMP,
    )
    assert writer.evidence[1] == FlowFailureEvidence()
    assert result == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        timestamp=TIMESTAMP,
    )


def test_writer_failure_retries_once_with_empty_evidence_and_returns_fallback_publication(
    tmp_path: Path,
) -> None:
    fallback_result = FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        diagnostic_run_id="20260816T000000Z-1234abcd",
        timestamp=TIMESTAMP,
    )
    original_evidence = FlowFailureEvidence(
        screenshot_png=b"private screenshot",
        raw_trace_path=tmp_path / "staging" / "private-trace.zip",
        deny_values=("PRIVATE",),
        trusted_page=True,
    )
    service, events, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(evidence=original_evidence),
        writer_outcomes=[FlowDiagnosticSanitizationError(), fallback_result],
    )

    result = service.preflight()

    assert result == fallback_result
    assert events == [
        "lock.acquire",
        "runtime.run",
        "runtime.close",
        "lock.release",
        "diagnostics.write",
        "diagnostics.write",
    ]
    assert writer.results[0].status == "ui_contract_failed"
    assert writer.results[1] == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        timestamp=TIMESTAMP,
    )
    assert writer.evidence == [original_evidence, FlowFailureEvidence()]


def test_two_writer_failures_return_fresh_result_only_failure_without_recursion(tmp_path: Path) -> None:
    service, events, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(),
        writer_outcomes=[FlowDiagnosticSanitizationError(), FlowDiagnosticSanitizationError()],
    )

    result = service.preflight()

    assert result == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        timestamp=TIMESTAMP,
    )
    assert events == [
        "lock.acquire",
        "runtime.run",
        "runtime.close",
        "lock.release",
        "diagnostics.write",
        "diagnostics.write",
    ]
    assert len(writer.results) == 2
    assert writer.evidence[1] == FlowFailureEvidence()


@pytest.mark.parametrize(
    ("trusted_page", "status", "failed_step", "authenticated"),
    [
        (False, "browser_launch_failed", "launch_browser", False),
        (True, "human_intervention_required", "verify_flow_ui", True),
    ],
)
def test_unknown_runtime_errors_map_conservatively_from_trusted_page_state(
    tmp_path: Path,
    trusted_page: bool,
    status: str,
    failed_step: str,
    authenticated: bool,
) -> None:
    secret_path = r"C:\\Users\\Rovaron\\secret-profile"
    service, _, _, _ = _service(
        tmp_path,
        runtime_error=UnknownRuntimeError(
            trusted_page=trusted_page,
            message=f"secret exception at {secret_path}",
        ),
    )

    result = service.preflight()

    assert result.status == status
    assert result.failed_step == failed_step
    assert result.authenticated is authenticated
    public_payload = result.model_dump_json(by_alias=True)
    assert "secret exception" not in public_payload
    assert secret_path not in public_payload


def test_lock_release_failure_discards_trusted_evidence_and_overrides_runtime_failure(
    tmp_path: Path,
) -> None:
    service, events, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(
            evidence=FlowFailureEvidence(
                screenshot_png=b"private screenshot",
                raw_trace_path=tmp_path / "staging" / "private-trace.zip",
                deny_values=("PRIVATE",),
                trusted_page=True,
            )
        ),
        lock_release_error=RuntimeError("release failed"),
    )

    result = service.preflight()

    assert events == [
        "lock.acquire",
        "runtime.run",
        "runtime.close",
        "lock.release",
        "diagnostics.write",
    ]
    assert result == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=False,
        ui_ready=False,
        failed_step="close_browser",
        timestamp=TIMESTAMP,
    )
    assert writer.evidence == [FlowFailureEvidence()]

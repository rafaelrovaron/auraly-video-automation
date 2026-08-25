from __future__ import annotations

import ast
from datetime import UTC, datetime
import errno
import json
import os
from pathlib import Path
from typing import Callable, cast
from zipfile import ZIP_STORED, ZipFile

import pytest

from auraly_pipeline.flow.config import FlowRuntimeConfig
from auraly_pipeline.flow.diagnostics import FlowDiagnosticWriter
from auraly_pipeline.flow import service as service_module
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
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc```\xf8\x0f\x00\x01\x04\x01\x00_\xe5\xc3K"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


class DistinctDiagnosticWriterFactory:
    def __init__(
        self,
        events: list[str],
        outcome_batches: list[list[Exception | FlowPreflightResult]],
    ) -> None:
        self._events = events
        self._outcome_batches = outcome_batches
        self.calls: list[tuple[Path, Path]] = []
        self.writers: list[SequencedDiagnosticWriter] = []

    def __call__(self, diagnostics_dir: Path, staging_root: Path) -> SequencedDiagnosticWriter:
        self.calls.append((diagnostics_dir, staging_root))
        writer = SequencedDiagnosticWriter(self._events, self._outcome_batches.pop(0))
        self.writers.append(writer)
        return writer


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
    raw_trace_cleanup: Callable[[Path, Path], None] | None = None,
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

    diagnostic_writer_factory = (
        FlowDiagnosticWriter if use_real_writer else cast(DiagnosticWriterFactory, writer_factory)
    )
    if raw_trace_cleanup is None:
        service = FlowPreflightService(
            _config_resolver=_resolver(config, resolver_calls),
            _lock_factory=cast(LockFactory, lock_factory),
            _runtime_factory=cast(RuntimeFactory, runtime_factory),
            _diagnostic_writer_factory=diagnostic_writer_factory,
            _now=lambda: TIMESTAMP,
        )
    else:
        service = FlowPreflightService(
            _config_resolver=_resolver(config, resolver_calls),
            _lock_factory=cast(LockFactory, lock_factory),
            _runtime_factory=cast(RuntimeFactory, runtime_factory),
            _diagnostic_writer_factory=diagnostic_writer_factory,
            _raw_trace_cleanup=raw_trace_cleanup,
            _now=lambda: TIMESTAMP,
        )
    return service, events, writer, resolver_calls


def _service_with_distinct_writers(
    tmp_path: Path,
    *,
    runtime_error: Exception,
    writer_outcome_batches: list[list[Exception | FlowPreflightResult]],
) -> tuple[FlowPreflightService, list[str], DistinctDiagnosticWriterFactory]:
    events: list[str] = []
    config = _config(tmp_path)
    writer_factory = DistinctDiagnosticWriterFactory(events, writer_outcome_batches)

    def lock_factory(path: Path) -> RecordingLock:
        assert path == config.lock_path
        return RecordingLock(events)

    def runtime_factory(received_config: FlowRuntimeConfig) -> RecordingRuntime:
        assert received_config is config
        return RecordingRuntime(events, error=runtime_error)

    service = FlowPreflightService(
        _config_resolver=_resolver(config, []),
        _lock_factory=cast(LockFactory, lock_factory),
        _runtime_factory=cast(RuntimeFactory, runtime_factory),
        _diagnostic_writer_factory=cast(DiagnosticWriterFactory, writer_factory),
        _now=lambda: TIMESTAMP,
    )
    return service, events, writer_factory


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
    assert result.diagnostic_run_id is None
    assert result.screenshot is None
    assert result.trace is None


def test_ui_contract_failure_preserves_allowlisted_locator_and_fixed_timestamp(tmp_path: Path) -> None:
    service, _, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(failed_locator="PROMPT_INPUT"),
    )

    result = service.preflight()

    assert result == FlowPreflightResult.failure(
        status="ui_contract_failed",
        authenticated=True,
        ui_ready=False,
        failed_step="verify_flow_ui",
        failed_locator="PROMPT_INPUT",
        timestamp=TIMESTAMP,
    )
    assert writer.results == [result]


def test_service_constructs_config_lock_and_runtime_in_lifecycle_order(tmp_path: Path) -> None:
    events: list[str] = []
    config = _config(tmp_path)

    def resolver(
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowRuntimeConfig:
        events.append("config")
        return config

    def lock_factory(path: Path) -> RecordingLock:
        assert path == config.lock_path
        events.append("lock.create")
        return RecordingLock(events)

    def runtime_factory(received_config: FlowRuntimeConfig) -> RecordingRuntime:
        assert received_config is config
        events.append("runtime.create")
        return RecordingRuntime(events)

    service = FlowPreflightService(
        _config_resolver=resolver,
        _lock_factory=cast(LockFactory, lock_factory),
        _runtime_factory=cast(RuntimeFactory, runtime_factory),
        _now=lambda: TIMESTAMP,
    )

    result = service.preflight()

    assert result == FlowPreflightResult.ready(timestamp=TIMESTAMP)
    assert events == [
        "config",
        "lock.create",
        "lock.acquire",
        "runtime.create",
        "runtime.run",
        "runtime.close",
        "lock.release",
    ]


@pytest.mark.parametrize("failure_phase", ["runtime_factory", "runtime_run"])
def test_unknown_runtime_or_factory_error_releases_lock_then_writes_sanitized_result(
    tmp_path: Path,
    failure_phase: str,
) -> None:
    events: list[str] = []
    config = _config(tmp_path)
    raw_path = r"C:\\Users\\Rovaron\\secret-profile"
    unknown_error = UnknownRuntimeError(
        trusted_page=True,
        message=f"unexpected failure at {raw_path}",
    )
    writer = RecordingDiagnosticWriter(events)

    def resolver(
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowRuntimeConfig:
        events.append("config")
        return config

    def lock_factory(path: Path) -> RecordingLock:
        assert path == config.lock_path
        events.append("lock.create")
        return RecordingLock(events)

    def runtime_factory(received_config: FlowRuntimeConfig) -> RecordingRuntime:
        assert received_config is config
        events.append("runtime.create")
        if failure_phase == "runtime_factory":
            raise unknown_error
        return RecordingRuntime(events, error=unknown_error)

    def writer_factory(diagnostics_dir: Path, staging_root: Path) -> RecordingDiagnosticWriter:
        assert diagnostics_dir == config.diagnostics_dir
        assert staging_root == config.staging_root
        return writer

    service = FlowPreflightService(
        _config_resolver=resolver,
        _lock_factory=cast(LockFactory, lock_factory),
        _runtime_factory=cast(RuntimeFactory, runtime_factory),
        _diagnostic_writer_factory=cast(DiagnosticWriterFactory, writer_factory),
        _now=lambda: TIMESTAMP,
    )

    result = service.preflight()

    assert result == FlowPreflightResult.failure(
        status="browser_launch_failed",
        authenticated=False,
        ui_ready=False,
        failed_step="launch_browser",
        timestamp=TIMESTAMP,
    )
    assert events.index("lock.release") < events.index("diagnostics.write")
    assert writer.results == [result]
    public_payload = result.model_dump_json(by_alias=True)
    assert "unexpected failure" not in public_payload
    assert raw_path not in public_payload


def _service_tree() -> ast.Module:
    module_path = Path(service_module.__file__)
    return ast.parse(module_path.read_text(encoding="utf-8"))


def _resolved_imports(tree: ast.Module) -> set[str]:
    package = ("auraly_pipeline", "flow")
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = () if node.level == 0 else package[: len(package) - node.level + 1]
            module = () if node.module is None else tuple(node.module.split("."))
            imports.add(".".join((*base, *module)))
    return imports


def _result_constructor_names(tree: ast.Module) -> set[str]:
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "FlowPreflightResult"
    }


def test_service_constructs_public_results_only_through_contract_factories() -> None:
    tree = _service_tree()
    constructor_names = _result_constructor_names(tree)
    direct_result_construction = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in constructor_names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "FlowPreflightResult")
        )
    ]
    result_factory_calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            (isinstance(node.func.value, ast.Name) and node.func.value.id in constructor_names)
            or (
                isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "FlowPreflightResult"
            )
        )
    ]

    assert direct_result_construction == []
    assert constructor_names == {"FlowPreflightResult"}
    assert set(result_factory_calls) == {"ready", "failure"}


def test_service_import_and_preflight_surface_exclude_browser_targets_urls_and_app_domains() -> None:
    tree = _service_tree()
    imported_modules = _resolved_imports(tree)
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    private_target_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"_target", "_FlowRuntimeTarget", "_local_test_target", "PRODUCTION_TARGET"}
    }
    public_option_names = {
        argument.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        for argument in (*node.args.args, *node.args.kwonlyargs)
    }
    public_attribute_names = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and not node.attr.startswith("_")
    }

    forbidden_module_prefixes = (
        "playwright",
        "auraly_pipeline.images",
        "auraly_pipeline.jobs",
        "auraly_pipeline.db",
    )
    assert all(
        not (
            module == forbidden_prefix or module.startswith(f"{forbidden_prefix}.")
        )
        for module in imported_modules
        for forbidden_prefix in forbidden_module_prefixes
    )
    assert {"_FlowRuntimeTarget", "_local_test_target", "PRODUCTION_TARGET"}.isdisjoint(identifiers)
    assert private_target_attributes == set()
    assert all("url" not in name.casefold() for name in public_option_names)
    assert all("url" not in name.casefold() for name in public_attribute_names)


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


def _trusted_ui_evidence(tmp_path: Path) -> FlowFailureEvidence:
    raw_trace = tmp_path / "staging" / "raw" / "trace.zip"
    raw_trace.parent.mkdir(parents=True)
    with ZipFile(raw_trace, "w", compression=ZIP_STORED) as archive:
        archive.writestr("trace.trace", '{"type":"event"}\n')
    return FlowFailureEvidence(
        screenshot_png=PNG_BYTES,
        raw_trace_path=raw_trace,
        trusted_page=True,
    )


@pytest.mark.parametrize(
    ("failure_kind", "status"),
    [
        ("runtime_busy", "runtime_busy"),
        ("browser_launch", "browser_launch_failed"),
        ("authentication", "authentication_required"),
        ("unexpected", "human_intervention_required"),
        ("ui_contract", "ui_contract_failed"),
        ("sanitize", "human_intervention_required"),
    ],
)
def test_valid_config_typed_failures_publish_real_result_json_and_return_run_id(
    tmp_path: Path,
    failure_kind: str,
    status: str,
) -> None:
    runtime_error_by_kind: dict[str, FlowRuntimeError] = {
        "browser_launch": FlowBrowserLaunchError(),
        "authentication": FlowAuthenticationTimeoutError(),
        "unexpected": FlowUnexpectedStateError(failed_step="navigate_flow"),
        "ui_contract": FlowUiContractError(evidence=_trusted_ui_evidence(tmp_path)),
        "sanitize": FlowDiagnosticSanitizationError(),
    }
    service, _, _, _ = _service(
        tmp_path,
        lock_acquire_error=FlowRuntimeBusyError() if failure_kind == "runtime_busy" else None,
        runtime_error=runtime_error_by_kind.get(failure_kind),
        use_real_writer=True,
    )

    result = service.preflight()

    assert result.status == status
    assert result.timestamp == TIMESTAMP
    assert result.diagnostic_run_id is not None
    run_dir = _config(tmp_path).diagnostics_dir / result.diagnostic_run_id
    published = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert published["diagnosticRunId"] == result.diagnostic_run_id
    assert published["status"] == status
    if failure_kind == "ui_contract":
        assert result.screenshot == "screenshot.png"
        assert result.trace == "trace.zip"
        assert (run_dir / "screenshot.png").is_file()
        assert (run_dir / "trace.zip").is_file()
    else:
        assert result.screenshot is None
        assert result.trace is None
    if failure_kind == "runtime_busy":
        assert result.screenshot is None
        assert result.trace is None


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
    service, events, writer_factory = _service_with_distinct_writers(
        tmp_path,
        runtime_error=FlowUiContractError(evidence=original_evidence),
        writer_outcome_batches=[[FlowDiagnosticSanitizationError()], [fallback_result]],
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
    assert writer_factory.calls == [
        (_config(tmp_path).diagnostics_dir, _config(tmp_path).staging_root),
        (_config(tmp_path).diagnostics_dir, _config(tmp_path).staging_root),
    ]
    first_writer, second_writer = writer_factory.writers
    assert first_writer.results[0].status == "ui_contract_failed"
    assert first_writer.evidence == [original_evidence]
    assert second_writer.results == [
        FlowPreflightResult.failure(
            status="human_intervention_required",
            authenticated=True,
            ui_ready=False,
            failed_step="sanitize_diagnostics",
            timestamp=TIMESTAMP,
        )
    ]
    assert second_writer.evidence == [FlowFailureEvidence()]


def test_two_writer_failures_return_fresh_result_only_failure_without_recursion(tmp_path: Path) -> None:
    service, events, writer_factory = _service_with_distinct_writers(
        tmp_path,
        runtime_error=FlowUiContractError(),
        writer_outcome_batches=[[FlowDiagnosticSanitizationError()], [FlowDiagnosticSanitizationError()]],
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
    assert writer_factory.calls == [
        (_config(tmp_path).diagnostics_dir, _config(tmp_path).staging_root),
        (_config(tmp_path).diagnostics_dir, _config(tmp_path).staging_root),
    ]
    first_writer, second_writer = writer_factory.writers
    assert first_writer.results[0].status == "ui_contract_failed"
    assert second_writer.results == [
        FlowPreflightResult.failure(
            status="human_intervention_required",
            authenticated=True,
            ui_ready=False,
            failed_step="sanitize_diagnostics",
            timestamp=TIMESTAMP,
        )
    ]
    assert second_writer.evidence == [FlowFailureEvidence()]


@pytest.mark.parametrize(
    "runtime_error",
    [
        UnknownRuntimeError(
            trusted_page=False,
            message=r"secret exception at C:\\Users\\Rovaron\\secret-profile",
        ),
        UnknownRuntimeError(
            trusted_page=True,
            message=r"secret exception at C:\\Users\\Rovaron\\secret-profile",
        ),
    ],
    ids=["untrusted_attribute", "forged_trusted_attribute"],
)
def test_unknown_runtime_errors_always_map_to_untrusted_launch_failure(
    tmp_path: Path,
    runtime_error: UnknownRuntimeError,
) -> None:
    secret_path = r"C:\\Users\\Rovaron\\secret-profile"
    service, _, _, _ = _service(
        tmp_path,
        runtime_error=runtime_error,
    )

    result = service.preflight()

    assert result.status == "browser_launch_failed"
    assert result.failed_step == "launch_browser"
    assert result.authenticated is False
    public_payload = result.model_dump_json(by_alias=True)
    assert "secret exception" not in public_payload
    assert secret_path not in public_payload


def test_lock_release_failure_discards_trusted_evidence_and_overrides_runtime_failure(
    tmp_path: Path,
) -> None:
    raw_trace = tmp_path / "staging" / "private-trace.zip"
    raw_trace.parent.mkdir(parents=True)
    raw_trace.write_bytes(b"private raw trace")
    service, events, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(
            evidence=FlowFailureEvidence(
                screenshot_png=b"private screenshot",
                raw_trace_path=raw_trace,
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
    assert not raw_trace.exists()
    assert result.screenshot is None
    assert result.trace is None
    serialized = result.model_dump_json(by_alias=True)
    assert "private screenshot" not in serialized
    assert str(raw_trace) not in serialized
    assert "PRIVATE" not in serialized
    assert writer.evidence == [FlowFailureEvidence()]


@pytest.mark.parametrize("path_kind", ["staging_root", "outside_staging"])
def test_lock_release_failure_refuses_unsafe_raw_trace_paths(
    tmp_path: Path,
    path_kind: str,
) -> None:
    config = _config(tmp_path)
    config.staging_root.mkdir()
    if path_kind == "staging_root":
        raw_trace = config.staging_root
    else:
        raw_trace = tmp_path / "outside" / "private-trace.zip"
        raw_trace.parent.mkdir()
        raw_trace.write_bytes(b"private raw trace")

    service, _, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(
            evidence=FlowFailureEvidence(raw_trace_path=raw_trace, trusted_page=True)
        ),
        lock_release_error=RuntimeError("release failed"),
    )

    result = service.preflight()

    assert result == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        timestamp=TIMESTAMP,
    )
    assert raw_trace.exists()
    assert result.screenshot is None
    assert result.trace is None
    assert str(raw_trace) not in result.model_dump_json(by_alias=True)
    assert writer.evidence == [FlowFailureEvidence()]


def test_create_symlink_skips_posix_permission_error_without_winerror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_symlink(source: Path, target: Path, *, target_is_directory: bool) -> None:
        del source, target, target_is_directory
        raise OSError(errno.EPERM, "operation not permitted")

    monkeypatch.setattr(os, "symlink", reject_symlink)

    with pytest.raises(pytest.skip.Exception):
        _create_symlink_or_skip(tmp_path / "outside", tmp_path / "staging-link")


def test_create_symlink_reraises_unrecognized_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_error = OSError(errno.EIO, "input/output error")

    def reject_symlink(source: Path, target: Path, *, target_is_directory: bool) -> None:
        del source, target, target_is_directory
        raise expected_error

    monkeypatch.setattr(os, "symlink", reject_symlink)

    with pytest.raises(OSError) as raised:
        _create_symlink_or_skip(tmp_path / "outside", tmp_path / "staging-link")

    assert raised.value is expected_error


def _create_symlink_or_skip(source: Path, target: Path) -> None:
    try:
        os.symlink(source, target, target_is_directory=False)
    except OSError as error:
        winerror = getattr(error, "winerror", None)
        if winerror == 1314 or error.errno in {errno.EPERM, errno.ENOTSUP}:
            pytest.skip(
                f"symlink creation is unsupported: winerror={winerror!r}, errno={error.errno!r}"
            )
        raise


def test_lock_release_failure_refuses_staging_symlink_to_outside_raw_trace(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.staging_root.mkdir()
    outside_raw_trace = tmp_path / "outside" / "private-trace.zip"
    outside_raw_trace.parent.mkdir()
    outside_raw_trace.write_bytes(b"private raw trace")
    staged_link = config.staging_root / "private-trace.zip"
    _create_symlink_or_skip(outside_raw_trace, staged_link)

    service, _, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(
            evidence=FlowFailureEvidence(raw_trace_path=staged_link, trusted_page=True)
        ),
        lock_release_error=RuntimeError("release failed"),
    )

    result = service.preflight()

    assert result == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        timestamp=TIMESTAMP,
    )
    assert staged_link.is_symlink()
    assert outside_raw_trace.exists()
    assert result.screenshot is None
    assert result.trace is None
    assert str(staged_link) not in result.model_dump_json(by_alias=True)
    assert str(outside_raw_trace) not in result.model_dump_json(by_alias=True)
    assert writer.evidence == [FlowFailureEvidence()]


def test_lock_release_failure_maps_raw_trace_cleanup_error_to_sanitize_diagnostics(
    tmp_path: Path,
) -> None:
    raw_trace = tmp_path / "staging" / "private-trace.zip"
    raw_trace.parent.mkdir(parents=True)
    raw_trace.write_bytes(b"private raw trace")
    cleanup_calls: list[tuple[Path, Path]] = []

    def reject_cleanup(path: Path, staging_root: Path) -> None:
        cleanup_calls.append((path, staging_root))
        raise OSError(r"private cleanup error at C:\\secret")

    service, _, writer, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(
            evidence=FlowFailureEvidence(raw_trace_path=raw_trace, trusted_page=True)
        ),
        lock_release_error=RuntimeError("release failed"),
        raw_trace_cleanup=reject_cleanup,
    )

    result = service.preflight()

    assert cleanup_calls == [(raw_trace, _config(tmp_path).staging_root)]
    assert raw_trace.exists()
    assert result == FlowPreflightResult.failure(
        status="human_intervention_required",
        authenticated=True,
        ui_ready=False,
        failed_step="sanitize_diagnostics",
        timestamp=TIMESTAMP,
    )
    serialized = result.model_dump_json(by_alias=True)
    assert "private cleanup error" not in serialized
    assert r"C:\\secret" not in serialized
    assert result.screenshot is None
    assert result.trace is None
    assert writer.evidence == [FlowFailureEvidence()]


def test_successful_lock_release_leaves_trusted_raw_trace_for_diagnostics_only(
    tmp_path: Path,
) -> None:
    raw_trace = tmp_path / "staging" / "raw" / "trace.zip"
    raw_trace.parent.mkdir(parents=True)
    with ZipFile(raw_trace, "w", compression=ZIP_STORED) as archive:
        archive.writestr("trace.trace", '{"type":"event"}\n')
    cleanup_calls: list[tuple[Path, Path]] = []

    def reject_service_cleanup(path: Path, staging_root: Path) -> None:
        cleanup_calls.append((path, staging_root))
        raise AssertionError("service cleanup must not run after a successful release")

    service, events, _, _ = _service(
        tmp_path,
        runtime_error=FlowUiContractError(
            evidence=FlowFailureEvidence(
                screenshot_png=PNG_BYTES,
                raw_trace_path=raw_trace,
                trusted_page=True,
            )
        ),
        use_real_writer=True,
        raw_trace_cleanup=reject_service_cleanup,
    )

    result = service.preflight()

    assert events == ["lock.acquire", "runtime.run", "runtime.close", "lock.release"]
    assert cleanup_calls == []
    assert result.screenshot == "screenshot.png"
    assert result.trace == "trace.zip"
    assert result.diagnostic_run_id is not None
    run_dir = _config(tmp_path).diagnostics_dir / result.diagnostic_run_id
    assert (run_dir / "screenshot.png").is_file()
    assert (run_dir / "trace.zip").is_file()
    assert not raw_trace.exists()

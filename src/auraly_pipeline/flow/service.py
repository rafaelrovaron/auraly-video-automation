"""Public orchestration boundary for one Google Flow preflight."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeAlias, cast

from .config import FlowRuntimeConfig, resolve_flow_runtime_config
from .diagnostics import FlowDiagnosticWriter
from .domain import (
    FlowBrowserLaunchError,
    FlowDiagnosticSanitizationError,
    FlowFailureEvidence,
    FlowPreflightResult,
    FlowRuntimeError,
    FlowUnexpectedStateError,
    NonReadyFlowPreflightStatus,
)
from .lock import BrowserRuntimeLock
from .runtime import GoogleFlowRuntime


class ConfigResolver(Protocol):
    def __call__(
        self,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowRuntimeConfig: ...


LockFactory: TypeAlias = Callable[[Path], BrowserRuntimeLock]
RuntimeFactory: TypeAlias = Callable[[FlowRuntimeConfig], GoogleFlowRuntime]
DiagnosticWriterFactory: TypeAlias = Callable[[Path, Path], FlowDiagnosticWriter]
RawTraceCleanup: TypeAlias = Callable[[Path, Path], None]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _discard_overridden_raw_trace(raw_trace_path: Path, staging_root: Path) -> None:
    """Discard transient raw evidence only after proving it belongs to this staging root."""
    try:
        resolved_root = staging_root.resolve(strict=False)
        resolved_raw_trace = raw_trace_path.resolve(strict=False)
        if resolved_raw_trace == resolved_root or not resolved_raw_trace.is_relative_to(resolved_root):
            raise ValueError("raw trace is not strictly inside the staging root")
        resolved_raw_trace.unlink(missing_ok=True)
    except (OSError, RuntimeError, ValueError):
        raise FlowDiagnosticSanitizationError() from None


class FlowPreflightService:
    """Resolve configuration, run the exclusive browser preflight, and publish safe results."""

    def __init__(
        self,
        *,
        _config_resolver: ConfigResolver = resolve_flow_runtime_config,
        _lock_factory: LockFactory = BrowserRuntimeLock,
        _runtime_factory: RuntimeFactory = GoogleFlowRuntime,
        _diagnostic_writer_factory: DiagnosticWriterFactory = FlowDiagnosticWriter,
        _raw_trace_cleanup: RawTraceCleanup = _discard_overridden_raw_trace,
        _now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._config_resolver = _config_resolver
        self._lock_factory = _lock_factory
        self._runtime_factory = _runtime_factory
        self._diagnostic_writer_factory = _diagnostic_writer_factory
        self._raw_trace_cleanup = _raw_trace_cleanup
        self._now = _now

    def preflight(
        self,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowPreflightResult:
        """Resolve config, hold the exclusive lock, run preflight, and map sanitized results."""
        try:
            config = self._config_resolver(
                profile_dir=profile_dir,
                diagnostics_dir=diagnostics_dir,
                login_timeout_seconds=login_timeout_seconds,
                navigation_timeout_seconds=navigation_timeout_seconds,
            )
        except FlowRuntimeError as error:
            return self._result_from_error(error)
        except Exception:
            return self._result_from_error(FlowBrowserLaunchError(failed_step="validate_config"))

        runtime_error: FlowRuntimeError | None = None
        runtime_succeeded = False
        lock: BrowserRuntimeLock | None = None
        lock_acquired = False
        lock_release_failed = False
        try:
            lock = self._lock_factory(config.lock_path)
            lock.acquire()
            lock_acquired = True
            runtime = self._runtime_factory(config)
            runtime.run()
            runtime_succeeded = True
        except FlowRuntimeError as error:
            runtime_error = error
        except Exception:
            runtime_error = FlowBrowserLaunchError()
        finally:
            if lock_acquired and lock is not None:
                try:
                    lock.release()
                except Exception:
                    runtime_succeeded = False
                    lock_release_failed = True

        if runtime_error is None and runtime_succeeded:
            return FlowPreflightResult.ready(timestamp=self._now())

        failure = (
            runtime_error
            if runtime_error is not None
            else FlowUnexpectedStateError(failed_step="close_browser")
        )
        evidence = failure.evidence
        if lock_release_failed:
            raw_trace_path = evidence.raw_trace_path
            if raw_trace_path is not None:
                try:
                    self._raw_trace_cleanup(raw_trace_path, config.staging_root)
                except Exception:
                    failure = FlowDiagnosticSanitizationError()
                else:
                    failure = FlowUnexpectedStateError(failed_step="close_browser")
            else:
                failure = FlowUnexpectedStateError(failed_step="close_browser")
            evidence = FlowFailureEvidence()
        result = self._result_from_error(failure)
        return self._publish_failure(config, result, evidence=evidence)

    def _publish_failure(
        self,
        config: FlowRuntimeConfig,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
    ) -> FlowPreflightResult:
        try:
            writer = self._diagnostic_writer_factory(config.diagnostics_dir, config.staging_root)
            return writer.write_failure(result, evidence=evidence)
        except Exception:
            fallback = self._result_from_error(FlowDiagnosticSanitizationError())
            try:
                writer = self._diagnostic_writer_factory(config.diagnostics_dir, config.staging_root)
                return writer.write_failure(fallback, evidence=FlowFailureEvidence())
            except Exception:
                return self._result_from_error(FlowDiagnosticSanitizationError())

    def _result_from_error(self, error: FlowRuntimeError) -> FlowPreflightResult:
        return FlowPreflightResult.failure(
            status=cast(NonReadyFlowPreflightStatus, error.status),
            authenticated=error.authenticated,
            ui_ready=error.ui_ready,
            failed_step=error.failed_step,
            failed_locator=error.failed_locator,
            timestamp=self._now(),
        )

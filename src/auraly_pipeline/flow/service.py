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


def utc_now() -> datetime:
    return datetime.now(UTC)


class FlowPreflightService:
    """Resolve configuration, run the exclusive browser preflight, and publish safe results."""

    def __init__(
        self,
        *,
        _config_resolver: ConfigResolver = resolve_flow_runtime_config,
        _lock_factory: LockFactory = BrowserRuntimeLock,
        _runtime_factory: RuntimeFactory = GoogleFlowRuntime,
        _diagnostic_writer_factory: DiagnosticWriterFactory = FlowDiagnosticWriter,
        _now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._config_resolver = _config_resolver
        self._lock_factory = _lock_factory
        self._runtime_factory = _runtime_factory
        self._diagnostic_writer_factory = _diagnostic_writer_factory
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
                    runtime_error = FlowUnexpectedStateError(failed_step="close_browser")

        if runtime_error is None and runtime_succeeded:
            return FlowPreflightResult.ready(timestamp=self._now())

        failure = (
            runtime_error
            if runtime_error is not None
            else FlowUnexpectedStateError(failed_step="close_browser")
        )
        result = self._result_from_error(failure)
        return self._publish_failure(config, result, evidence=failure.evidence)

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

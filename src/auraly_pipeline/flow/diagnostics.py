"""Sanitized append-only diagnostic publication for Flow preflight failures."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import shutil
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .domain import (
    FlowDiagnosticSanitizationError,
    FlowFailureEvidence,
    FlowPreflightResult,
    NonReadyFlowPreflightStatus,
)


_SCREENSHOT_NAME: Literal["screenshot.png"] = "screenshot.png"
_TRACE_NAME: Literal["trace.zip"] = "trace.zip"
_RESULT_NAME = "result.json"
_TRACE_MEMBER = "trace.trace"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
_HTTP_URL = re.compile(r"https?://[^\s\"'<>]+")
_SENSITIVE_KEY_PARTS = (
    "headers",
    "request",
    "response",
    "body",
    "postdata",
    "snapshot",
    "html",
    "source",
    "cookie",
    "authorization",
    "credential",
    "password",
    "token",
    "secret",
    "storage",
    "localstorage",
    "sessionstorage",
    "resource",
    "prompt",
)
_FIXED_DENY_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(rb"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}"),
    re.compile(rb"(?i)[a-z]:\\users\\"),
    re.compile(rb"(?i)/(?:home|users)/[^\"'\s]+"),
    re.compile(rb"https?://[^\"'\s]*(?:\?|#)"),
    re.compile(rb"(?i)\b(?:authorization|cookie|set-cookie|postdata|storagestate)\b"),
)


def sanitize_trace_archive(
    raw_path: Path,
    output_path: Path,
    *,
    deny_values: Sequence[str] = (),
) -> None:
    """Publish a query-free, resource-free allowlisted trace archive or raise."""
    output_created = False
    try:
        sanitized_trace = _sanitize_trace_member(raw_path)
        _validate_sanitized_bytes(sanitized_trace, deny_values)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("xb") as output_file:
            output_created = True
            with ZipFile(output_file, "w", compression=ZIP_DEFLATED) as output_archive:
                output_archive.writestr(_TRACE_MEMBER, sanitized_trace)
        _validate_sanitized_archive(output_path, deny_values)
    except FlowDiagnosticSanitizationError:
        if output_created:
            _unlink_if_present(output_path)
        raise
    except (BadZipFile, OSError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        if output_created:
            _unlink_if_present(output_path)
        raise FlowDiagnosticSanitizationError() from None


class FlowDiagnosticWriter:
    """Publish one append-only sanitized diagnostic run for a non-ready preflight result."""

    def __init__(self, diagnostics_dir: Path, staging_root: Path) -> None:
        self._diagnostics_dir = diagnostics_dir
        self._staging_root = staging_root

    def write_failure(
        self,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
    ) -> FlowPreflightResult:
        """Sanitize in staging, publish one exclusive run, and return artifact references."""
        if result.success or result.status == "ready" or result.failed_step is None:
            raise FlowDiagnosticSanitizationError(evidence=evidence)

        run_id = _new_run_id()
        staging_dir = self._staging_root / f"{run_id}-{secrets.token_hex(4)}.stage"
        final_dir = self._diagnostics_dir / run_id
        final_created = False

        try:
            self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
            self._staging_root.mkdir(parents=True, exist_ok=True)
            staging_dir.mkdir(parents=False, exist_ok=False)

            publish_rich_evidence = _requires_rich_evidence(result)
            screenshot_stage: Path | None = None
            trace_stage: Path | None = None
            if publish_rich_evidence:
                screenshot_stage = staging_dir / _SCREENSHOT_NAME
                trace_stage = staging_dir / _TRACE_NAME
                _write_sanitized_screenshot(
                    screenshot_stage,
                    evidence.screenshot_png,
                    deny_values=evidence.deny_values,
                )
                if evidence.raw_trace_path is None:
                    raise FlowDiagnosticSanitizationError(evidence=evidence)
                sanitize_trace_archive(
                    evidence.raw_trace_path,
                    trace_stage,
                    deny_values=evidence.deny_values,
                )

            published_result = _with_diagnostic_references(
                result,
                run_id=run_id,
                screenshot=_SCREENSHOT_NAME if publish_rich_evidence else None,
                trace=_TRACE_NAME if publish_rich_evidence else None,
            )

            final_dir.mkdir(parents=False, exist_ok=False)
            final_created = True
            if screenshot_stage is not None:
                self._publish_file_exclusive(screenshot_stage, final_dir / _SCREENSHOT_NAME)
            if trace_stage is not None:
                self._publish_file_exclusive(trace_stage, final_dir / _TRACE_NAME)
            self._publish_result_json_exclusive(final_dir / _RESULT_NAME, published_result)
            return published_result
        except FlowDiagnosticSanitizationError:
            if final_created:
                _remove_incomplete_final_run(final_dir)
            raise
        except OSError:
            if final_created:
                _remove_incomplete_final_run(final_dir)
            raise FlowDiagnosticSanitizationError(evidence=evidence) from None
        finally:
            _remove_tree_if_present(staging_dir)
            _cleanup_raw_trace(evidence.raw_trace_path, self._staging_root)

    def _publish_file_exclusive(self, source: Path, destination: Path) -> None:
        with source.open("rb") as source_file:
            with destination.open("xb") as destination_file:
                shutil.copyfileobj(source_file, destination_file)

    def _publish_result_json_exclusive(
        self, destination: Path, result: FlowPreflightResult
    ) -> None:
        payload = result.model_dump(by_alias=True, mode="json", exclude_none=False)
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        created = False
        try:
            with destination.open("xb") as result_file:
                created = True
                result_file.write(encoded)
        except OSError:
            if created:
                _unlink_if_present(destination)
            raise


def _sanitize_trace_member(raw_path: Path) -> bytes:
    with ZipFile(raw_path) as archive:
        trace_members = [info for info in archive.infolist() if info.filename == _TRACE_MEMBER]
        if len(trace_members) != 1:
            raise FlowDiagnosticSanitizationError()
        for info in archive.infolist():
            _classify_archive_member(info.filename)

        raw_trace = archive.read(trace_members[0]).decode("utf-8")

    sanitized_lines: list[str] = []
    for line in raw_trace.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise FlowDiagnosticSanitizationError()
        sanitized = _sanitize_json_value(value)
        if not isinstance(sanitized, dict):
            raise FlowDiagnosticSanitizationError()
        sanitized_lines.append(
            json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
    if not sanitized_lines:
        raise FlowDiagnosticSanitizationError()
    return ("\n".join(sanitized_lines) + "\n").encode("utf-8")


def _classify_archive_member(name: str) -> None:
    if _is_unsafe_archive_name(name):
        raise FlowDiagnosticSanitizationError()
    if name == _TRACE_MEMBER or _is_known_dropped_member(name):
        return
    raise FlowDiagnosticSanitizationError()


def _is_unsafe_archive_name(name: str) -> bool:
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    return (
        not name
        or "\x00" in name
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or ".." in windows.parts
    )


def _is_known_dropped_member(name: str) -> bool:
    normalized = name.replace("\\", "/").lower()
    if normalized == "trace.network":
        return True
    dropped_prefixes = (
        "resources/",
        "resource/",
        "sources/",
        "source/",
        "src/",
        "dom/",
        "snapshot/",
        "snapshots/",
    )
    if normalized.startswith(dropped_prefixes):
        return True
    basename = PurePosixPath(normalized).name
    return basename.startswith(("source", "snapshot", "dom")) or basename.endswith(
        (".js", ".ts", ".tsx", ".css", ".html", ".map")
    )


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise FlowDiagnosticSanitizationError()
            if _is_sensitive_key(key):
                continue
            sanitized[key] = _sanitize_json_value(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json_value(child) for child in value]
    if isinstance(value, str):
        return _sanitize_urls(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    raise FlowDiagnosticSanitizationError()


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_urls(value: str) -> str:
    return _HTTP_URL.sub(lambda match: _canonical_url(match.group(0)), value)


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FlowDiagnosticSanitizationError()
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _validate_sanitized_archive(path: Path, deny_values: Sequence[str]) -> None:
    with ZipFile(path) as archive:
        names = archive.namelist()
        if names != [_TRACE_MEMBER]:
            raise FlowDiagnosticSanitizationError()
        _validate_sanitized_bytes(archive.read(_TRACE_MEMBER), deny_values)


def _validate_sanitized_bytes(data: bytes, deny_values: Sequence[str]) -> None:
    for pattern in _FIXED_DENY_PATTERNS:
        if pattern.search(data):
            raise FlowDiagnosticSanitizationError()
    for deny_value in deny_values:
        if not deny_value:
            continue
        for encoded in _encoded_deny_values(deny_value):
            if encoded and encoded in data:
                raise FlowDiagnosticSanitizationError()


def _encoded_deny_values(value: str) -> tuple[bytes, ...]:
    raw = value.encode("utf-8")
    escaped = json.dumps(value, ensure_ascii=True)[1:-1].encode("utf-8")
    if escaped == raw:
        return (raw,)
    return (raw, escaped)


def _write_sanitized_screenshot(
    destination: Path,
    screenshot_png: bytes | None,
    *,
    deny_values: Sequence[str],
) -> None:
    if screenshot_png is None or not screenshot_png.startswith(_PNG_SIGNATURE):
        raise FlowDiagnosticSanitizationError()
    _validate_sanitized_bytes(screenshot_png, deny_values)
    with destination.open("xb") as screenshot_file:
        screenshot_file.write(screenshot_png)


def _requires_rich_evidence(result: FlowPreflightResult) -> bool:
    return result.authenticated and result.status in {
        "ui_contract_failed",
        "human_intervention_required",
    }


def _with_diagnostic_references(
    result: FlowPreflightResult,
    *,
    run_id: str,
    screenshot: Literal["screenshot.png"] | None,
    trace: Literal["trace.zip"] | None,
) -> FlowPreflightResult:
    failed_step = result.failed_step
    if failed_step is None:
        raise FlowDiagnosticSanitizationError()
    return FlowPreflightResult.failure(
        status=cast(NonReadyFlowPreflightStatus, result.status),
        authenticated=result.authenticated,
        ui_ready=result.ui_ready,
        failed_step=failed_step,
        failed_locator=result.failed_locator,
        diagnostic_run_id=run_id,
        screenshot=screenshot,
        trace=trace,
        timestamp=result.timestamp,
    )


def _new_run_id() -> str:
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise FlowDiagnosticSanitizationError()
    return run_id


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _remove_tree_if_present(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path)
    except OSError:
        pass


def _remove_incomplete_final_run(path: Path) -> None:
    _remove_tree_if_present(path)


def _cleanup_raw_trace(raw_trace_path: Path | None, staging_root: Path) -> None:
    if raw_trace_path is None:
        return
    try:
        resolved_root = staging_root.resolve(strict=False)
        resolved_raw = raw_trace_path.resolve(strict=False)
        if not resolved_raw.is_relative_to(resolved_root):
            return
        raw_trace_path.unlink(missing_ok=True)
        parent = raw_trace_path.parent
        while parent != resolved_root and parent.resolve(strict=False).is_relative_to(resolved_root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    except OSError:
        pass

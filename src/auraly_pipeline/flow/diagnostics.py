"""Sanitized append-only diagnostic publication for Flow preflight failures."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import shutil
import struct
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile
import zlib

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
_HTTP_URL = re.compile(r"https?://[^\s\"'<>}\\]+", re.IGNORECASE)
_SAFE_TRACE_TEXT = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_SAFE_TRACE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@/-]{1,256}$")
_MAX_ZIP_MEMBERS = 20
_MAX_RAW_TRACE_ARCHIVE_BYTES = 512 * 1024
_MAX_ZIP_MEMBER_COMPRESSED_BYTES = 128 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 40
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 262_144
_MAX_TRACE_MEMBER_BYTES = 16_384
_MAX_TRACE_LINE_BYTES = 8_192
_MAX_TRACE_STRING_LENGTH = 2_048
_MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
_MAX_PNG_CHUNK_BYTES = 4 * 1024 * 1024
_MAX_PNG_PIXELS = 16_777_216
_MAX_PNG_INFLATED_BYTES = 64 * 1024 * 1024
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
    "payload",
)
_SAFE_TRACE_SCALAR_KEYS = frozenset(
    {
        "apiName",
        "browserName",
        "callId",
        "class",
        "column",
        "contextId",
        "duration",
        "endTime",
        "frameId",
        "guid",
        "height",
        "line",
        "method",
        "monotonicTime",
        "pageId",
        "parentId",
        "platform",
        "sdkLanguage",
        "startTime",
        "testIdAttributeName",
        "timestamp",
        "type",
        "url",
        "version",
        "wallTime",
        "width",
    }
)
_TRACE_EVENT_PROJECTIONS: dict[str, frozenset[str]] = {
    "context-options": frozenset(
        {
            "browserName",
            "contextId",
            "monotonicTime",
            "platform",
            "sdkLanguage",
            "testIdAttributeName",
            "type",
            "version",
            "wallTime",
        }
    ),
    "before": frozenset(
        {
            "apiName",
            "callId",
            "class",
            "frameId",
            "guid",
            "method",
            "pageId",
            "startTime",
            "type",
            "url",
            "wallTime",
        }
    ),
    "after": frozenset(
        {
            "callId",
            "duration",
            "endTime",
            "type",
            "wallTime",
        }
    ),
    "event": frozenset(
        {
            "apiName",
            "callId",
            "class",
            "frameId",
            "guid",
            "method",
            "pageId",
            "timestamp",
            "type",
            "url",
            "wallTime",
        }
    ),
}
_TRACE_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "context-options": frozenset({"type", "version", "browserName"}),
    "before": frozenset({"type", "callId", "startTime"}),
    "after": frozenset({"type", "callId", "endTime"}),
    "event": frozenset({"type"}),
}
_FIXED_DENY_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(rb"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}"),
    re.compile(rb"(?i)[a-z]:(?:\\\\|/)+users(?:\\\\|/)+"),
    re.compile(rb'"//[^/"\s]+/[^"\s]+'),
    re.compile(rb"(?:\\\\){2,}[^\\\"'\s]+(?:\\\\)+[^\\\"'\s]+"),
    re.compile(rb"(?i)/(?:home|users)/[^\"'\s]+"),
    re.compile(rb"https?://[^\"'\s]*(?:\?|#)"),
    re.compile(rb"(?i)\b(?:authorization|cookie|set-cookie|postdata|storagestate)\b"),
)
_SENSITIVE_VALUE_PARTS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "privateprompt",
    "prompt",
    "secret",
    "storage",
    "token",
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
    except (
        BadZipFile,
        NotImplementedError,
        OSError,
        RecursionError,
        RuntimeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        zlib.error,
        json.JSONDecodeError,
    ):
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
        published: FlowPreflightResult | None = None
        cleanup_failed = False
        operation_error: FlowDiagnosticSanitizationError | None = None
        try:
            if result.success or result.status == "ready" or result.failed_step is None:
                raise FlowDiagnosticSanitizationError(evidence=evidence)
            _validate_result_diagnostic_policy(result, evidence=evidence)
            self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
            self._staging_root.mkdir(parents=True, exist_ok=True)
            published = self._write_failure_with_retries(result, evidence=evidence)
        except FlowDiagnosticSanitizationError as exc:
            operation_error = exc
        except OSError:
            operation_error = FlowDiagnosticSanitizationError(evidence=evidence)
        try:
            _cleanup_raw_trace(evidence.raw_trace_path, self._staging_root)
        except (FlowDiagnosticSanitizationError, OSError):
            cleanup_failed = True
        if cleanup_failed and published is not None and published.diagnostic_run_id is not None:
            _remove_incomplete_final_run(self._diagnostics_dir / published.diagnostic_run_id)
        if operation_error is not None:
            raise operation_error
        if cleanup_failed:
            raise FlowDiagnosticSanitizationError(evidence=evidence)
        if published is None:
            raise FlowDiagnosticSanitizationError(evidence=evidence)
        return published

    def _write_failure_with_retries(
        self,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
    ) -> FlowPreflightResult:
        for _ in range(10):
            run_id = _new_run_id()
            final_dir = self._diagnostics_dir / run_id
            if final_dir.exists():
                continue
            try:
                return self._write_single_failure_run(
                    result,
                    evidence=evidence,
                    run_id=run_id,
                    final_dir=final_dir,
                )
            except FileExistsError:
                continue
        raise FlowDiagnosticSanitizationError(evidence=evidence)

    def _write_single_failure_run(
        self,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
        run_id: str,
        final_dir: Path,
    ) -> FlowPreflightResult:
        staging_dir = self._staging_root / f"{run_id}-{secrets.token_hex(4)}.stage"
        final_created = False
        published_result: FlowPreflightResult | None = None
        try:
            staging_dir.mkdir(parents=False, exist_ok=False)

            publish_rich_evidence = _requires_rich_evidence(result, evidence=evidence)
            screenshot_stage: Path | None = None
            trace_stage: Path | None = None
            result_stage = staging_dir / _RESULT_NAME
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
                raw_trace_path = _require_staged_raw_trace(
                    evidence.raw_trace_path, self._staging_root, evidence=evidence
                )
                sanitize_trace_archive(
                    raw_trace_path,
                    trace_stage,
                    deny_values=evidence.deny_values,
                )

            published_result = _with_diagnostic_references(
                result,
                run_id=run_id,
                screenshot=_SCREENSHOT_NAME if publish_rich_evidence else None,
                trace=_TRACE_NAME if publish_rich_evidence else None,
            )
            self._stage_result_json(result_stage, published_result)

            final_dir.mkdir(parents=False, exist_ok=False)
            final_created = True
            if screenshot_stage is not None:
                self._publish_file_exclusive(screenshot_stage, final_dir / _SCREENSHOT_NAME)
            if trace_stage is not None:
                self._publish_file_exclusive(trace_stage, final_dir / _TRACE_NAME)
            self._publish_result_json_exclusive(result_stage, final_dir / _RESULT_NAME)
            _remove_tree_if_present(staging_dir)
            return published_result
        except FlowDiagnosticSanitizationError:
            if final_created:
                _remove_incomplete_final_run(final_dir)
            _remove_tree_for_failure(staging_dir)
            raise
        except FileExistsError:
            if final_created:
                _remove_incomplete_final_run(final_dir)
            _remove_tree_for_failure(staging_dir)
            raise
        except OSError:
            if final_created:
                _remove_incomplete_final_run(final_dir)
            _remove_tree_for_failure(staging_dir)
            raise FlowDiagnosticSanitizationError(evidence=evidence) from None

    def _publish_file_exclusive(self, source: Path, destination: Path) -> None:
        with source.open("rb") as source_file:
            with destination.open("xb") as destination_file:
                shutil.copyfileobj(source_file, destination_file)

    def _stage_result_json(self, destination: Path, result: FlowPreflightResult) -> None:
        payload = result.model_dump(by_alias=True, mode="json", exclude_none=False)
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        _validate_sanitized_bytes(encoded, ())
        with destination.open("xb") as result_file:
            result_file.write(encoded)

    def _publish_result_json_exclusive(self, source: Path, destination: Path) -> None:
        temp_destination = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temp_destination.open("xb") as result_file:
                with source.open("rb") as source_file:
                    shutil.copyfileobj(source_file, result_file)
                result_file.flush()
                os.fsync(result_file.fileno())
            if destination.exists():
                raise FileExistsError(destination)
            temp_destination.replace(destination)
        except OSError:
            _unlink_if_present(temp_destination)
            raise


def _sanitize_trace_member(raw_path: Path) -> bytes:
    try:
        if raw_path.stat().st_size > _MAX_RAW_TRACE_ARCHIVE_BYTES:
            raise FlowDiagnosticSanitizationError()
    except OSError:
        raise FlowDiagnosticSanitizationError() from None
    with ZipFile(raw_path) as archive:
        members = archive.infolist()
        if len(members) > _MAX_ZIP_MEMBERS:
            raise FlowDiagnosticSanitizationError()
        total_size = 0
        for info in members:
            _validate_zip_info(
                info.filename,
                info.flag_bits,
                info.compress_type,
                info.file_size,
                info.compress_size,
            )
            total_size += info.file_size
        if total_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise FlowDiagnosticSanitizationError()

        trace_members = [info for info in archive.infolist() if info.filename == _TRACE_MEMBER]
        if len(trace_members) != 1:
            raise FlowDiagnosticSanitizationError()
        for info in archive.infolist():
            _classify_archive_member(info.filename)

        raw_trace = archive.read(trace_members[0]).decode("utf-8")
        if len(raw_trace.encode("utf-8")) > _MAX_TRACE_MEMBER_BYTES:
            raise FlowDiagnosticSanitizationError()

    sanitized_lines: list[str] = []
    for line in raw_trace.splitlines():
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > _MAX_TRACE_LINE_BYTES:
            raise FlowDiagnosticSanitizationError()
        value = json.loads(line)
        if not isinstance(value, dict):
            raise FlowDiagnosticSanitizationError()
        sanitized = _project_trace_event(value)
        if not isinstance(sanitized, dict):
            raise FlowDiagnosticSanitizationError()
        sanitized_lines.append(
            json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
    if not sanitized_lines:
        raise FlowDiagnosticSanitizationError()
    return ("\n".join(sanitized_lines) + "\n").encode("utf-8")


def _validate_zip_info(
    name: str,
    flag_bits: int,
    compress_type: int,
    file_size: int,
    compress_size: int,
) -> None:
    if flag_bits & 0x1:
        raise FlowDiagnosticSanitizationError()
    if compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
        raise FlowDiagnosticSanitizationError()
    if compress_size < 0 or compress_size > _MAX_ZIP_MEMBER_COMPRESSED_BYTES:
        raise FlowDiagnosticSanitizationError()
    if compress_size > 0 and file_size // compress_size > _MAX_ZIP_COMPRESSION_RATIO:
        raise FlowDiagnosticSanitizationError()
    if name == _TRACE_MEMBER and file_size > _MAX_TRACE_MEMBER_BYTES:
        raise FlowDiagnosticSanitizationError()
    if file_size < 0 or file_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise FlowDiagnosticSanitizationError()


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
    return basename.startswith(("source.", "source-", "snapshot.", "snapshot-", "dom.", "dom-")) or basename.endswith(
        (".js", ".ts", ".tsx", ".css", ".html", ".map")
    )


def _project_trace_event(value: dict[str, Any]) -> dict[str, Any]:
    if any(_HTTP_URL.search(key) for key in value):
        raise FlowDiagnosticSanitizationError()
    event_type = value.get("type")
    if not isinstance(event_type, str) or event_type not in _TRACE_EVENT_PROJECTIONS:
        raise FlowDiagnosticSanitizationError()
    required_keys = _TRACE_REQUIRED_KEYS[event_type]
    if any(key not in value for key in required_keys):
        raise FlowDiagnosticSanitizationError()

    projection = _TRACE_EVENT_PROJECTIONS[event_type]
    for key in value:
        if key in projection:
            continue
        if event_type == "context-options" and key == "options":
            continue
        if event_type == "before" and key == "params":
            continue
        if _is_sensitive_key(key):
            continue
        raise FlowDiagnosticSanitizationError()

    sanitized: dict[str, Any] = {}
    for key in sorted(projection):
        if key not in value:
            continue
        if key not in _SAFE_TRACE_SCALAR_KEYS:
            raise FlowDiagnosticSanitizationError()
        sanitized[key] = _sanitize_trace_scalar(key, value[key])

    if event_type == "before":
        params = value.get("params")
        if isinstance(params, dict) and "url" in params:
            sanitized["url"] = _sanitize_trace_scalar("url", params["url"])
    return sanitized


def _sanitize_trace_scalar(key: str, value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_trace_string(key, value)
    if value is None or isinstance(value, bool | int | float):
        if isinstance(value, float) and not math.isfinite(value):
            raise FlowDiagnosticSanitizationError()
        return value
    raise FlowDiagnosticSanitizationError()


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_urls(value: str) -> str:
    return _HTTP_URL.sub(lambda match: _canonical_url(match.group(0)), value)


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise FlowDiagnosticSanitizationError()
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((scheme, host, parsed.path, "", ""))


def _is_canonical_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _sanitize_trace_string(key: str, value: str) -> str:
    if len(value) > _MAX_TRACE_STRING_LENGTH:
        raise FlowDiagnosticSanitizationError()
    sanitized = _sanitize_urls(value)
    if key == "url" or _is_canonical_http_url(sanitized):
        if not _is_canonical_http_url(sanitized):
            raise FlowDiagnosticSanitizationError()
        return sanitized
    normalized = re.sub(r"[^a-z0-9]", "", sanitized.lower())
    if any(part in normalized for part in _SENSITIVE_VALUE_PARTS):
        raise FlowDiagnosticSanitizationError()
    if key in {"callId", "contextId", "frameId", "guid", "pageId", "parentId"}:
        if not _SAFE_TRACE_IDENTIFIER.fullmatch(sanitized):
            raise FlowDiagnosticSanitizationError()
        return sanitized
    if not _SAFE_TRACE_TEXT.fullmatch(sanitized):
        raise FlowDiagnosticSanitizationError()
    return sanitized


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
    if screenshot_png is None:
        raise FlowDiagnosticSanitizationError()
    _validate_png_screenshot(screenshot_png, deny_values=deny_values)
    with destination.open("xb") as screenshot_file:
        screenshot_file.write(screenshot_png)


def _validate_png_screenshot(data: bytes, *, deny_values: Sequence[str]) -> None:
    if (
        not data.startswith(_PNG_SIGNATURE)
        or len(data) <= len(_PNG_SIGNATURE)
        or len(data) > _MAX_SCREENSHOT_BYTES
    ):
        raise FlowDiagnosticSanitizationError()
    offset = len(_PNG_SIGNATURE)
    seen_ihdr = False
    seen_idat = False
    seen_iend = False
    width = 0
    height = 0
    color_type = 0
    idat_chunks: list[bytes] = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise FlowDiagnosticSanitizationError()
        chunk_length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_length
        crc_end = chunk_end + 4
        if chunk_length > _MAX_PNG_CHUNK_BYTES or crc_end > len(data):
            raise FlowDiagnosticSanitizationError()
        chunk_data = data[chunk_start:chunk_end]
        expected_crc = struct.unpack(">I", data[chunk_end:crc_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise FlowDiagnosticSanitizationError()

        if chunk_type == b"IHDR":
            if seen_ihdr or offset != len(_PNG_SIGNATURE) or chunk_length != 13:
                raise FlowDiagnosticSanitizationError()
            width, height, color_type = _validate_ihdr_chunk(chunk_data)
            seen_ihdr = True
        elif chunk_type == b"IDAT":
            if not seen_ihdr or seen_iend:
                raise FlowDiagnosticSanitizationError()
            seen_idat = True
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            if chunk_length != 0 or not seen_ihdr or not seen_idat:
                raise FlowDiagnosticSanitizationError()
            seen_iend = True
            offset = crc_end
            break
        else:
            raise FlowDiagnosticSanitizationError()
        offset = crc_end
    if not seen_iend or offset != len(data):
        raise FlowDiagnosticSanitizationError()
    _validate_png_idat_stream(b"".join(idat_chunks), width=width, height=height, color_type=color_type)
    _validate_sanitized_bytes(data, deny_values)


def _validate_ihdr_chunk(data: bytes) -> tuple[int, int, int]:
    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", data
    )
    if width <= 0 or height <= 0:
        raise FlowDiagnosticSanitizationError()
    if width * height > _MAX_PNG_PIXELS:
        raise FlowDiagnosticSanitizationError()
    if bit_depth != 8 or color_type not in {2, 6}:
        raise FlowDiagnosticSanitizationError()
    if compression != 0 or filter_method != 0 or interlace not in {0, 1}:
        raise FlowDiagnosticSanitizationError()
    return width, height, color_type


def _validate_png_idat_stream(data: bytes, *, width: int, height: int, color_type: int) -> None:
    channels = 4 if color_type == 6 else 3
    expected_scanline_bytes = height * (1 + width * channels)
    if expected_scanline_bytes > _MAX_PNG_INFLATED_BYTES:
        raise FlowDiagnosticSanitizationError()
    decompressor = zlib.decompressobj()
    try:
        inflated = decompressor.decompress(data, _MAX_PNG_INFLATED_BYTES + 1)
        inflated += decompressor.flush(_MAX_PNG_INFLATED_BYTES + 1 - len(inflated))
    except zlib.error:
        raise FlowDiagnosticSanitizationError() from None
    if decompressor.unused_data or decompressor.unconsumed_tail:
        raise FlowDiagnosticSanitizationError()
    if len(inflated) > _MAX_PNG_INFLATED_BYTES or len(inflated) != expected_scanline_bytes:
        raise FlowDiagnosticSanitizationError()


def _requires_rich_evidence(
    result: FlowPreflightResult,
    *,
    evidence: FlowFailureEvidence,
) -> bool:
    if not result.authenticated:
        return False
    if result.status == "ui_contract_failed":
        return result.failed_step == "verify_flow_ui" and evidence.trusted_page
    return (
        result.status == "human_intervention_required"
        and result.failed_step == "verify_flow_ui"
        and evidence.trusted_page
    )


def _validate_result_diagnostic_policy(
    result: FlowPreflightResult, *, evidence: FlowFailureEvidence
) -> None:
    if result.status == "ui_contract_failed" and (
        not result.authenticated or result.failed_step != "verify_flow_ui" or not evidence.trusted_page
    ):
        raise FlowDiagnosticSanitizationError(evidence=evidence)
    if result.screenshot is not None or result.trace is not None:
        raise FlowDiagnosticSanitizationError(evidence=evidence)


def _require_staged_raw_trace(
    raw_trace_path: Path,
    staging_root: Path,
    *,
    evidence: FlowFailureEvidence,
) -> Path:
    try:
        resolved_root = staging_root.resolve(strict=False)
        resolved_raw = raw_trace_path.resolve(strict=False)
    except OSError:
        raise FlowDiagnosticSanitizationError(evidence=evidence) from None
    if resolved_raw == resolved_root or not resolved_raw.is_relative_to(resolved_root):
        raise FlowDiagnosticSanitizationError(evidence=evidence)
    return raw_trace_path


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
    path.unlink(missing_ok=True)


def _remove_tree_if_present(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _remove_incomplete_final_run(path: Path) -> None:
    try:
        _remove_tree_if_present(path)
    except OSError:
        raise FlowDiagnosticSanitizationError() from None


def _remove_tree_for_failure(path: Path) -> None:
    try:
        _remove_tree_if_present(path)
    except OSError:
        raise FlowDiagnosticSanitizationError() from None


def _cleanup_raw_trace(raw_trace_path: Path | None, staging_root: Path) -> None:
    if raw_trace_path is None:
        return
    try:
        resolved_root = staging_root.resolve(strict=False)
        resolved_raw = raw_trace_path.resolve(strict=False)
        if resolved_raw == resolved_root or not resolved_raw.is_relative_to(resolved_root):
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
        raise FlowDiagnosticSanitizationError() from None

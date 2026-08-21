from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from auraly_pipeline.flow.diagnostics import FlowDiagnosticWriter, sanitize_trace_archive
from auraly_pipeline.flow.domain import (
    FLOW_URL,
    FlowDiagnosticSanitizationError,
    FlowFailureEvidence,
    FlowFailedStep,
    FlowLocatorName,
    FlowPreflightResult,
    NonReadyFlowPreflightStatus,
)


TIMESTAMP = datetime(2026, 8, 16, tzinfo=UTC)
PNG_BYTES = b"\x89PNG\r\n\x1a\nsanitized-png-bytes"
ALLOWED_RESULT_KEYS = {
    "schemaVersion",
    "success",
    "status",
    "flowUrl",
    "authenticated",
    "uiReady",
    "failedStep",
    "failedLocator",
    "diagnosticRunId",
    "screenshot",
    "trace",
    "timestamp",
}


def _failure_result(
    status: NonReadyFlowPreflightStatus,
    *,
    authenticated: bool = False,
    ui_ready: bool = False,
    failed_step: FlowFailedStep | None = None,
    failed_locator: FlowLocatorName | None = None,
) -> FlowPreflightResult:
    failed_step_by_status: dict[NonReadyFlowPreflightStatus, FlowFailedStep] = {
        "authentication_required": "await_manual_authentication",
        "human_intervention_required": "navigate_flow",
        "runtime_busy": "acquire_runtime_lock",
        "browser_launch_failed": "launch_browser",
        "ui_contract_failed": "verify_flow_ui",
    }
    return FlowPreflightResult.failure(
        status=status,
        authenticated=authenticated,
        ui_ready=ui_ready,
        failed_step=failed_step if failed_step is not None else failed_step_by_status[status],
        failed_locator=failed_locator,
        timestamp=TIMESTAMP,
    )


def _write_trace_archive(
    path: Path,
    events: list[dict[str, Any]],
    *,
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    trace_body = "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n"
    with ZipFile(path, "w") as archive:
        archive.writestr("trace.trace", trace_body)
        for name, body in (extra_members or {}).items():
            archive.writestr(name, body)
    return path


def _expanded_zip_bytes(path: Path) -> bytes:
    expanded: list[bytes] = []
    with ZipFile(path) as archive:
        for name in archive.namelist():
            expanded.append(name.encode("utf-8"))
            expanded.append(archive.read(name))
    return b"\n".join(expanded)


def _zip_members(path: Path) -> set[str]:
    with ZipFile(path) as archive:
        return set(archive.namelist())


def _writer(tmp_path: Path) -> FlowDiagnosticWriter:
    diagnostics_dir = tmp_path / "diagnostics"
    staging_root = tmp_path / "staging"
    diagnostics_dir.mkdir()
    staging_root.mkdir()
    return FlowDiagnosticWriter(diagnostics_dir, staging_root)


def _trusted_evidence(staging_root: Path) -> FlowFailureEvidence:
    raw_trace = _write_trace_archive(
        staging_root / "raw" / "trace.zip",
        [
            {
                "type": "event",
                "url": "https://labs.google/fx/tools/flow?token=SECRET#fragment",
                "request": {
                    "headers": {"cookie": "session=COOKIE_SECRET"},
                    "postData": "PRIVATE_PROMPT",
                },
                "metadata": {"safe": "workspace"},
            }
        ],
        extra_members={
            "trace.network": b"COOKIE_SECRET",
            "resources/body.txt": b"PRIVATE_PROMPT",
            "source.js": b"SECRET",
        },
    )
    return FlowFailureEvidence(
        screenshot_png=PNG_BYTES,
        raw_trace_path=raw_trace,
        deny_values=("SECRET", "COOKIE_SECRET", "PRIVATE_PROMPT"),
    )


def _run_dir(tmp_path: Path, result: FlowPreflightResult) -> Path:
    assert result.diagnostic_run_id is not None
    return tmp_path / "diagnostics" / result.diagnostic_run_id


def test_trace_sanitizer_drops_network_resources_sources_and_url_secrets(
    tmp_path: Path,
) -> None:
    raw = _write_trace_archive(
        tmp_path / "raw.zip",
        [
            {
                "type": "before",
                "url": "https://labs.google/fx/tools/flow?token=SECRET#fragment",
                "request": {"headers": {"cookie": "session=SECRET"}},
                "snapshot": {"html": "<main>PRIVATE_PROMPT</main>"},
            }
        ],
        extra_members={
            "trace.network": b"SECRET",
            "resources/blob": b"PRIVATE_PROMPT",
            "source.js": b"SECRET",
        },
    )
    safe = tmp_path / "safe.zip"

    sanitize_trace_archive(raw, safe, deny_values=("SECRET", "PRIVATE_PROMPT"))

    expanded = _expanded_zip_bytes(safe)
    assert _zip_members(safe) == {"trace.trace"}
    assert b"https://labs.google/fx/tools/flow" in expanded
    assert b"trace.network" not in expanded
    assert b"resources/" not in expanded
    assert b"source.js" not in expanded
    assert b"?token=" not in expanded
    assert b"#fragment" not in expanded
    assert b"SECRET" not in expanded
    assert b"PRIVATE_PROMPT" not in expanded


def test_trace_sanitizer_removes_headers_bodies_cookies_auth_and_prompt_content(
    tmp_path: Path,
) -> None:
    raw = _write_trace_archive(
        tmp_path / "raw.zip",
        [
            {
                "type": "event",
                "headers": {"authorization": "Bearer AUTH_TOKEN"},
                "payload": [
                    {"request": {"body": "PRIVATE_PROMPT"}},
                    {"response": {"body": "RESPONSE_SECRET"}},
                    {
                        "metadata": {
                            "cookies": "COOKIE_SECRET",
                            "postData": "PRIVATE_PROMPT",
                            "source": "SOURCE_SECRET",
                            "storageState": "STORAGE_SECRET",
                        }
                    },
                ],
                "url": "https://example.test/path?token=URL_SECRET#fragment",
            }
        ],
    )
    safe = tmp_path / "safe.zip"

    sanitize_trace_archive(
        raw,
        safe,
        deny_values=(
            "AUTH_TOKEN",
            "COOKIE_SECRET",
            "PRIVATE_PROMPT",
            "RESPONSE_SECRET",
            "SOURCE_SECRET",
            "STORAGE_SECRET",
            "URL_SECRET",
        ),
    )

    expanded = _expanded_zip_bytes(safe)
    assert b"https://example.test/path" in expanded
    for forbidden in (
        b"headers",
        b"authorization",
        b"body",
        b"postData",
        b"AUTH_TOKEN",
        b"COOKIE_SECRET",
        b"PRIVATE_PROMPT",
        b"RESPONSE_SECRET",
        b"SOURCE_SECRET",
        b"STORAGE_SECRET",
        b"URL_SECRET",
    ):
        assert forbidden not in expanded


def test_trace_sanitizer_rejects_surviving_deny_values_including_private_paths(
    tmp_path: Path,
) -> None:
    raw = _write_trace_archive(
        tmp_path / "raw.zip",
        [
            {
                "type": "event",
                "note": (
                    "person@example.com "
                    "C:\\Users\\PrivateUser\\ "
                    "/home/private-user/ "
                    "SECRET"
                ),
            }
        ],
    )
    safe = tmp_path / "safe.zip"

    with pytest.raises(FlowDiagnosticSanitizationError):
        sanitize_trace_archive(
            raw,
            safe,
            deny_values=(
                "person@example.com",
                "C:\\Users\\PrivateUser\\",
                "/home/private-user/",
                "SECRET",
            ),
        )

    assert not safe.exists()


def test_trace_sanitizer_rejects_unknown_archive_members(tmp_path: Path) -> None:
    raw = _write_trace_archive(
        tmp_path / "raw.zip",
        [{"type": "event", "url": FLOW_URL}],
        extra_members={"mystery.bin": b"unknown"},
    )
    safe = tmp_path / "safe.zip"

    with pytest.raises(FlowDiagnosticSanitizationError):
        sanitize_trace_archive(raw, safe)

    assert not safe.exists()


def test_trace_sanitizer_preserves_existing_output_on_exclusive_create_failure(
    tmp_path: Path,
) -> None:
    raw = _write_trace_archive(tmp_path / "raw.zip", [{"type": "event", "url": FLOW_URL}])
    existing = tmp_path / "safe.zip"
    existing.write_bytes(b"existing diagnostics must remain append-only")

    with pytest.raises(FlowDiagnosticSanitizationError):
        sanitize_trace_archive(raw, existing)

    assert existing.read_bytes() == b"existing diagnostics must remain append-only"


def test_writer_creates_unique_runs_without_overwrite_or_cleanup(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    result = _failure_result("runtime_busy")

    first = writer.write_failure(result, evidence=FlowFailureEvidence())
    second = writer.write_failure(result, evidence=FlowFailureEvidence())

    assert first.diagnostic_run_id != second.diagnostic_run_id
    assert {path.name for path in (tmp_path / "diagnostics").iterdir()} == {
        first.diagnostic_run_id,
        second.diagnostic_run_id,
    }
    assert (_run_dir(tmp_path, first) / "result.json").is_file()
    assert (_run_dir(tmp_path, second) / "result.json").is_file()


@pytest.mark.parametrize(
    "result",
    (
        _failure_result("authentication_required"),
        _failure_result("runtime_busy"),
        _failure_result("browser_launch_failed"),
    ),
)
def test_result_only_statuses_ignore_supplied_evidence(
    tmp_path: Path, result: FlowPreflightResult
) -> None:
    writer = _writer(tmp_path)
    raw_trace = _write_trace_archive(
        tmp_path / "staging" / "raw" / "trace.zip",
        [{"type": "event", "note": "SECRET"}],
        extra_members={"mystery.bin": b"raw secret"},
    )
    evidence = FlowFailureEvidence(
        screenshot_png=PNG_BYTES + b"SECRET",
        raw_trace_path=raw_trace,
        deny_values=("SECRET",),
    )

    published = writer.write_failure(result, evidence=evidence)

    run_dir = _run_dir(tmp_path, published)
    assert published.screenshot is None
    assert published.trace is None
    assert {path.name for path in run_dir.iterdir()} == {"result.json"}
    assert not raw_trace.exists()
    assert b"SECRET" not in (run_dir / "result.json").read_bytes()


def test_trusted_ui_failure_publishes_sanitized_artifacts_and_relative_references(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    result = _failure_result(
        "ui_contract_failed",
        authenticated=True,
        failed_step="verify_flow_ui",
        failed_locator="PROMPT_INPUT",
    )
    evidence = _trusted_evidence(tmp_path / "staging")

    published = writer.write_failure(result, evidence=evidence)

    run_dir = _run_dir(tmp_path, published)
    assert published.screenshot == "screenshot.png"
    assert published.trace == "trace.zip"
    assert {path.name for path in run_dir.iterdir()} == {
        "screenshot.png",
        "trace.zip",
        "result.json",
    }
    assert (run_dir / "screenshot.png").read_bytes() == PNG_BYTES
    expanded = _expanded_zip_bytes(run_dir / "trace.zip")
    assert b"https://labs.google/fx/tools/flow" in expanded
    for forbidden in (b"SECRET", b"COOKIE_SECRET", b"PRIVATE_PROMPT", b"?token=", b"#fragment"):
        assert forbidden not in expanded
    assert evidence.raw_trace_path is not None
    assert not evidence.raw_trace_path.exists()


def test_writer_rejects_ready_result_without_creating_a_run(tmp_path: Path) -> None:
    writer = _writer(tmp_path)

    with pytest.raises(FlowDiagnosticSanitizationError):
        writer.write_failure(
            FlowPreflightResult.ready(timestamp=TIMESTAMP), evidence=FlowFailureEvidence()
        )

    assert not any((tmp_path / "diagnostics").iterdir())


def test_writer_writes_result_json_after_final_artifacts(tmp_path: Path) -> None:
    class RecordingWriter(FlowDiagnosticWriter):
        def __init__(self, diagnostics_dir: Path, staging_root: Path) -> None:
            super().__init__(diagnostics_dir, staging_root)
            self.final_write_order: list[str] = []

        def _publish_file_exclusive(self, source: Path, destination: Path) -> None:
            self.final_write_order.append(destination.name)
            super()._publish_file_exclusive(source, destination)

        def _publish_result_json_exclusive(
            self, destination: Path, result: FlowPreflightResult
        ) -> None:
            self.final_write_order.append(destination.name)
            super()._publish_result_json_exclusive(destination, result)

    diagnostics_dir = tmp_path / "diagnostics"
    staging_root = tmp_path / "staging"
    diagnostics_dir.mkdir()
    staging_root.mkdir()
    writer = RecordingWriter(diagnostics_dir, staging_root)
    result = _failure_result(
        "human_intervention_required",
        authenticated=True,
        failed_step="navigate_flow",
    )

    writer.write_failure(result, evidence=_trusted_evidence(staging_root))

    assert writer.final_write_order == ["screenshot.png", "trace.zip", "result.json"]


def test_writer_cleans_staging_raw_and_incomplete_runs_on_sanitization_failure(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    raw_trace = _write_trace_archive(
        tmp_path / "staging" / "raw" / "trace.zip",
        [{"type": "event", "url": FLOW_URL}],
    )
    evidence = FlowFailureEvidence(screenshot_png=b"not a png", raw_trace_path=raw_trace)
    result = _failure_result(
        "ui_contract_failed",
        authenticated=True,
        failed_step="verify_flow_ui",
    )

    with pytest.raises(FlowDiagnosticSanitizationError):
        writer.write_failure(result, evidence=evidence)

    assert not raw_trace.exists()
    assert not list((tmp_path / "staging").rglob("*"))
    assert not list((tmp_path / "diagnostics").rglob("*"))


def test_writer_removes_incomplete_final_run_if_result_json_write_fails(
    tmp_path: Path,
) -> None:
    class FailingResultWriter(FlowDiagnosticWriter):
        def _publish_result_json_exclusive(
            self, destination: Path, result: FlowPreflightResult
        ) -> None:
            destination.write_text("{", encoding="utf-8")
            raise OSError("simulated result marker failure")

    diagnostics_dir = tmp_path / "diagnostics"
    staging_root = tmp_path / "staging"
    diagnostics_dir.mkdir()
    staging_root.mkdir()
    writer = FailingResultWriter(diagnostics_dir, staging_root)
    result = _failure_result(
        "ui_contract_failed",
        authenticated=True,
        failed_step="verify_flow_ui",
    )
    evidence = _trusted_evidence(staging_root)

    with pytest.raises(FlowDiagnosticSanitizationError):
        writer.write_failure(result, evidence=evidence)

    assert evidence.raw_trace_path is not None
    assert not evidence.raw_trace_path.exists()
    assert not list(staging_root.rglob("*"))
    assert not list(diagnostics_dir.rglob("*"))


def test_result_json_contains_only_allowlisted_public_fields_and_no_private_values(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    raw_trace = _write_trace_archive(
        tmp_path / "staging" / "raw" / "trace.zip",
        [{"type": "event", "url": "https://labs.google/fx/tools/flow?token=SECRET"}],
    )
    result = _failure_result(
        "human_intervention_required",
        authenticated=True,
        failed_step="navigate_flow",
    )
    evidence = FlowFailureEvidence(
        screenshot_png=PNG_BYTES,
        raw_trace_path=raw_trace,
        deny_values=(
            "SECRET",
            "person@example.com",
            "C:\\Users\\PrivateUser\\",
            "/home/private-user/",
        ),
    )

    published = writer.write_failure(result, evidence=evidence)

    payload = json.loads((_run_dir(tmp_path, published) / "result.json").read_text("utf-8"))
    serialized = json.dumps(payload, sort_keys=True).encode("utf-8")
    assert set(payload) == ALLOWED_RESULT_KEYS
    assert payload["diagnosticRunId"] == published.diagnostic_run_id
    assert payload["screenshot"] == "screenshot.png"
    assert payload["trace"] == "trace.zip"
    for forbidden in (
        b"raw_trace_path",
        b"exception",
        b"profile",
        b"staging",
        b"?token=",
        b"SECRET",
        b"person@example.com",
        b"C:\\Users\\PrivateUser\\",
        b"/home/private-user/",
    ):
        assert forbidden not in serialized

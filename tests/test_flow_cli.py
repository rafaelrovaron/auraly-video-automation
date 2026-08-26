from __future__ import annotations

import ast
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal, Protocol, TypeAlias, cast

import pytest
import typer.main
from typer.core import TyperGroup
from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.flow import (
    FlowFailedStep,
    FlowLocatorName,
    FlowPreflightResult,
    FlowPreflightService,
    FlowPreflightStatus,
)


runner = CliRunner()

_TIMESTAMP = datetime(2026, 8, 25, tzinfo=UTC)
_PUBLIC_RESULT_KEYS = {
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
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _run_auraly(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the installed entrypoint; CliRunner does not capture Rich help on Linux."""

    entrypoint_name = "auraly.exe" if sys.platform == "win32" else "auraly"
    return subprocess.run(
        [str(Path(sys.executable).with_name(entrypoint_name)), *arguments],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=15,
    )


def _help_output(invocation: subprocess.CompletedProcess[str]) -> str:
    return _ANSI_ESCAPE.sub("", invocation.stdout + invocation.stderr)


NonReadyStatus: TypeAlias = Literal[
    "authentication_required",
    "human_intervention_required",
    "runtime_busy",
    "browser_launch_failed",
    "ui_contract_failed",
]


class PreflightFake(Protocol):
    def __call__(
        self,
        _self: FlowPreflightService,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowPreflightResult: ...


def result_for_status(status: FlowPreflightStatus) -> FlowPreflightResult:
    if status == "ready":
        return FlowPreflightResult.ready(timestamp=_TIMESTAMP)

    failure_values: dict[
        NonReadyStatus, tuple[bool, bool, FlowFailedStep, FlowLocatorName | None]
    ] = {
        "authentication_required": (False, False, "await_manual_authentication", None),
        "human_intervention_required": (True, False, "verify_flow_ui", None),
        "runtime_busy": (False, False, "acquire_runtime_lock", None),
        "browser_launch_failed": (False, False, "launch_browser", None),
        "ui_contract_failed": (True, False, "verify_flow_ui", "FLOW_WORKSPACE"),
    }
    non_ready_status = cast(NonReadyStatus, status)
    authenticated, ui_ready, failed_step, failed_locator = failure_values[non_ready_status]
    return FlowPreflightResult.failure(
        status=non_ready_status,
        authenticated=authenticated,
        ui_ready=ui_ready,
        failed_step=failed_step,
        failed_locator=failed_locator,
        timestamp=_TIMESTAMP,
    )


def preflight_returning(value: FlowPreflightResult) -> PreflightFake:
    def fake_preflight(
        _self: FlowPreflightService,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowPreflightResult:
        del profile_dir, diagnostics_dir, login_timeout_seconds, navigation_timeout_seconds
        return value

    return fake_preflight


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("ready", 0),
        ("authentication_required", 1),
        ("human_intervention_required", 1),
        ("runtime_busy", 1),
        ("browser_launch_failed", 1),
        ("ui_contract_failed", 1),
    ],
)
def test_flow_preflight_emits_one_json_object_and_exact_exit_code(
    status: FlowPreflightStatus,
    exit_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        FlowPreflightService,
        "preflight",
        preflight_returning(result_for_status(status)),
    )

    invocation = runner.invoke(app, ["flow", "preflight"])

    assert invocation.exit_code == exit_code
    assert json.loads(invocation.stdout)["status"] == status
    assert invocation.stdout.rstrip().count("{") == 1


def test_flow_preflight_serializes_aliases_and_explicit_ready_nulls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        FlowPreflightService,
        "preflight",
        preflight_returning(result_for_status("ready")),
    )

    invocation = runner.invoke(app, ["flow", "preflight"])
    payload = json.loads(invocation.stdout)

    assert payload["success"] is True
    assert set(payload) == _PUBLIC_RESULT_KEYS
    assert {"schema_version", "flow_url", "ui_ready"}.isdisjoint(payload)
    assert {key: payload[key] for key in ("failedStep", "failedLocator", "diagnosticRunId", "screenshot", "trace")} == {
        "failedStep": None,
        "failedLocator": None,
        "diagnosticRunId": None,
        "screenshot": None,
        "trace": None,
    }


def test_flow_preflight_preserves_relative_diagnostic_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = FlowPreflightResult.failure(
        status="ui_contract_failed",
        authenticated=True,
        ui_ready=False,
        failed_step="verify_flow_ui",
        failed_locator="FLOW_WORKSPACE",
        diagnostic_run_id="20260825T000000Z-deadbeef",
        screenshot="screenshot.png",
        trace="trace.zip",
        timestamp=_TIMESTAMP,
    )
    monkeypatch.setattr(FlowPreflightService, "preflight", preflight_returning(result))

    payload = json.loads(runner.invoke(app, ["flow", "preflight"]).stdout)

    assert payload["diagnosticRunId"] == "20260825T000000Z-deadbeef"
    assert payload["screenshot"] == "screenshot.png"
    assert payload["trace"] == "trace.zip"


def test_flow_preflight_keeps_result_only_failure_nulls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        FlowPreflightService,
        "preflight",
        preflight_returning(result_for_status("authentication_required")),
    )

    payload = json.loads(runner.invoke(app, ["flow", "preflight"]).stdout)

    assert set(payload) == _PUBLIC_RESULT_KEYS
    assert payload["failedStep"] == "await_manual_authentication"
    assert {
        key: payload[key]
        for key in ("failedLocator", "diagnosticRunId", "screenshot", "trace")
    } == {
        "failedLocator": None,
        "diagnosticRunId": None,
        "screenshot": None,
        "trace": None,
    }


def test_flow_preflight_forwards_all_options_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path | None, Path | None, int | None, int | None]] = []

    def fake_preflight(
        _self: FlowPreflightService,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowPreflightResult:
        calls.append((profile_dir, diagnostics_dir, login_timeout_seconds, navigation_timeout_seconds))
        return result_for_status("ready")

    monkeypatch.setattr(FlowPreflightService, "preflight", fake_preflight)
    invocation = runner.invoke(
        app,
        [
            "flow",
            "preflight",
            "--profile-dir",
            "profile",
            "--diagnostics-dir",
            "diagnostics",
            "--login-timeout",
            "123",
            "--navigation-timeout",
            "45",
        ],
    )

    assert invocation.exit_code == 0
    assert calls == [(Path("profile"), Path("diagnostics"), 123, 45)]


def test_flow_preflight_forwards_none_defaults_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path | None, Path | None, int | None, int | None]] = []

    def fake_preflight(
        _self: FlowPreflightService,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowPreflightResult:
        calls.append((profile_dir, diagnostics_dir, login_timeout_seconds, navigation_timeout_seconds))
        return result_for_status("ready")

    monkeypatch.setattr(FlowPreflightService, "preflight", fake_preflight)

    invocation = runner.invoke(app, ["flow", "preflight"])

    assert invocation.exit_code == 0
    assert calls == [(None, None, None, None)]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--login-timeout", "0"),
        ("--login-timeout", "-1"),
        ("--navigation-timeout", "0"),
        ("--navigation-timeout", "-1"),
    ],
)
def test_flow_preflight_rejects_non_positive_timeouts_without_calling_service(
    option: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_preflight(
        _self: FlowPreflightService,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowPreflightResult:
        nonlocal calls
        del profile_dir, diagnostics_dir, login_timeout_seconds, navigation_timeout_seconds
        calls += 1
        return result_for_status("ready")

    monkeypatch.setattr(FlowPreflightService, "preflight", fake_preflight)

    invocation = runner.invoke(app, ["flow", "preflight", option, value])

    assert invocation.exit_code != 0
    assert calls == 0


def test_flow_preflight_registers_only_approved_options() -> None:
    root_command = cast(TyperGroup, typer.main.get_command(app))
    flow_command = cast(TyperGroup, root_command.commands["flow"])
    command = flow_command.commands["preflight"]

    assert {option for parameter in command.params for option in parameter.opts} == {
        "--profile-dir",
        "--diagnostics-dir",
        "--login-timeout",
        "--navigation-timeout",
    }


def test_flow_preflight_help_uses_only_approved_public_options() -> None:
    invocation = _run_auraly("flow", "preflight", "--help")

    help_output = _help_output(invocation)
    assert invocation.returncode == 0
    assert help_output
    assert set(re.findall(r"--[a-z][a-z-]*", help_output)) == {
        "--help",
        "--profile-dir",
        "--diagnostics-dir",
        "--login-timeout",
        "--navigation-timeout",
    }
    for forbidden in (
        "--url",
        "--headless",
        "--browser",
        "--channel",
        "--executable-path",
        "--generate",
        "--download",
        "--storage-state",
        "--cookie",
        "--token",
        "generate",
        "download",
    ):
        assert forbidden not in help_output.casefold()


def test_flow_help_exposes_preflight_without_generation_commands() -> None:
    invocation = _run_auraly("flow", "--help")

    help_output = _help_output(invocation)
    assert invocation.returncode == 0
    assert help_output
    assert "preflight" in help_output
    assert "generate" not in help_output.casefold()


def test_root_help_retains_existing_public_command_groups() -> None:
    invocation = _run_auraly("--help")

    help_output = _help_output(invocation)
    assert invocation.returncode == 0
    assert help_output
    for command_name in ("campaign", "job", "voice", "image", "flow", "ingest", "validate"):
        assert command_name in help_output


def test_flow_group_registers_only_preflight() -> None:
    root_command = cast(TyperGroup, typer.main.get_command(app))
    command = cast(TyperGroup, root_command.commands["flow"])

    assert set(command.commands) == {"preflight"}


def test_flow_preflight_does_not_leak_private_paths_or_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        FlowPreflightService,
        "preflight",
        preflight_returning(result_for_status("browser_launch_failed")),
    )

    invocation = runner.invoke(
        app,
        [
            "flow",
            "preflight",
            "--profile-dir",
            r"C:\\Users\\PrivateUser\\profile",
            "--diagnostics-dir",
            "/home/private-user/diagnostics",
        ],
    )

    output = invocation.stdout + invocation.stderr
    for unsafe in ("C:\\Users\\PrivateUser", "/home/private-user", "Traceback", "RuntimeError", "Playwright", "SECRET"):
        assert unsafe not in output


@pytest.mark.parametrize("failure_point", ["service", "serialization"])
def test_flow_preflight_unexpected_exception_emits_one_sanitized_boundary_failure(
    failure_point: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_error = RuntimeError(r"SECRET from C:\Users\PrivateUser\profile")

    def unexpected_preflight(*_args: object, **_kwargs: object) -> FlowPreflightResult:
        if failure_point == "service":
            raise private_error

        class UnserializableResult:
            success = False

            @staticmethod
            def model_dump(**_kwargs: object) -> dict[str, object]:
                raise private_error

        return cast(FlowPreflightResult, UnserializableResult())

    monkeypatch.setattr(FlowPreflightService, "preflight", unexpected_preflight)

    invocation = runner.invoke(
        app,
        [
            "flow",
            "preflight",
            "--profile-dir",
            r"C:\Users\PrivateUser\profile",
            "--diagnostics-dir",
            "/home/private-user/diagnostics",
        ],
    )

    assert invocation.exit_code == 1
    assert invocation.stdout.rstrip().count("{") == 1
    payload = json.loads(invocation.stdout)
    timestamp = datetime.fromisoformat(payload.pop("timestamp").replace("Z", "+00:00"))
    assert timestamp.tzinfo is not None
    assert payload == {
        "schemaVersion": "1.0",
        "success": False,
        "status": "browser_launch_failed",
        "flowUrl": "https://labs.google/fx/tools/flow",
        "authenticated": False,
        "uiReady": False,
        "failedStep": "validate_config",
        "failedLocator": None,
        "diagnosticRunId": None,
        "screenshot": None,
        "trace": None,
    }
    output = invocation.stdout + invocation.stderr
    for unsafe in (
        r"C:\Users\PrivateUser",
        "/home/private-user",
        "Traceback",
        "RuntimeError",
        "SECRET",
    ):
        assert unsafe not in output


def test_flow_preflight_preserves_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted_preflight(*_args: object, **_kwargs: object) -> FlowPreflightResult:
        raise KeyboardInterrupt()

    monkeypatch.setattr(FlowPreflightService, "preflight", interrupted_preflight)

    invocation = runner.invoke(app, ["flow", "preflight"])

    assert invocation.exit_code == 130
    assert isinstance(invocation.exception, SystemExit)
    assert invocation.stdout == ""


def test_flow_cli_depends_only_on_public_preflight_boundary() -> None:
    cli_path = Path(__file__).parents[1] / "src" / "auraly_pipeline" / "cli.py"
    tree = ast.parse(cli_path.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    preflight_service_imports = [
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "FlowPreflightService"
    ]
    flow_command = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "flow_preflight_command"
    )
    lower_level_names = {
        "GoogleFlowRuntime",
        "BrowserRuntimeLock",
        "FlowDiagnosticWriter",
        "FlowRuntimeConfig",
        "resolve_flow_runtime_config",
    }

    assert "FlowPreflightService" in names
    assert preflight_service_imports == [("auraly_pipeline.flow", "FlowPreflightService")]
    assert lower_level_names.isdisjoint(names)
    assert lower_level_names.isdisjoint(imported_names)
    try_nodes = [node for node in ast.walk(flow_command) if isinstance(node, ast.Try)]
    assert len(try_nodes) == 1
    assert len(try_nodes[0].handlers) == 1
    handler_type = try_nodes[0].handlers[0].type
    assert isinstance(handler_type, ast.Name)
    assert handler_type.id == "Exception"


def test_root_command_retains_existing_command_groups() -> None:
    root_command = cast(TyperGroup, typer.main.get_command(app))

    for command_name in ("campaign", "job", "voice", "image", "flow", "ingest", "validate"):
        assert command_name in root_command.commands

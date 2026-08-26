"""Integrated local security and lifecycle regressions for Google Flow preflight."""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import inspect
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from zipfile import ZipFile

import pytest
from playwright.sync_api import Locator, Page

from auraly_pipeline import flow as flow_package
from auraly_pipeline import cli as cli_module
from auraly_pipeline.flow import (
    BrowserRuntimeLock,
    FlowDiagnosticWriter,
    FlowFailureEvidence,
    FlowPreflightResult,
    FlowPreflightService,
    FlowRuntimeConfig,
    FlowRuntimeError,
    FlowRuntimeObservation,
    GoogleFlowRuntime,
    resolve_flow_runtime_config,
)
from auraly_pipeline.flow import config as config_module
from auraly_pipeline.flow import locators as locator_module
from auraly_pipeline.flow import runtime as runtime_module
from auraly_pipeline.flow import service as service_module
from auraly_pipeline.flow.runtime import _FlowRuntimeTarget, _local_test_target
from tests.flow_browser_support import FAKE_FLOW_ROOT


REPOSITORY_ROOT = Path(__file__).parents[1].resolve()
FLOW_SOURCE_ROOT = REPOSITORY_ROOT / "src" / "auraly_pipeline" / "flow"
FIXED_NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
APPROVED_FIXTURES = frozenset(path.name for path in FAKE_FLOW_ROOT.glob("*.html"))


@dataclass(frozen=True)
class _LocalPaths:
    profile: Path
    diagnostics: Path
    lock: Path
    staging: Path
    fixtures: Path
    state_root: Path


@dataclass
class _LifecycleObservation:
    raw_trace_path: Path | None = None
    raw_trace_existed_before_publication: bool = False
    lock_acquired: bool = False
    lock_released: bool = False
    runtime_entered_while_locked: bool = False


@dataclass
class _MaskObservation:
    semantic_masks: tuple[Locator, ...] = ()
    screenshot_masks: tuple[object, ...] = ()
    mask_color: object | None = None


def _local_paths(tmp_path: Path) -> _LocalPaths:
    state_root = tmp_path / "state"
    return _LocalPaths(
        profile=tmp_path / "profile",
        diagnostics=tmp_path / "diagnostics",
        lock=state_root / "locks" / "google-flow-browser.lock",
        staging=state_root / "staging" / "google-flow",
        fixtures=tmp_path / "fixtures",
        state_root=state_root,
    )


def _copy_local_fixtures(paths: _LocalPaths) -> None:
    paths.fixtures.mkdir(parents=True, exist_ok=True)
    for source in FAKE_FLOW_ROOT.glob("*.html"):
        shutil.copyfile(source, paths.fixtures / source.name)


def _fixture_url(paths: _LocalPaths, fixture: str) -> str:
    parsed = urlsplit(fixture)
    fixture_path = PurePosixPath(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or fixture_path.name != parsed.path
        or parsed.path not in APPROVED_FIXTURES
    ):
        raise ValueError("security tests require one approved local fixture")
    base = urlsplit((paths.fixtures / parsed.path).resolve(strict=True).as_uri())
    url = urlunsplit((base.scheme, base.netloc, base.path, parsed.query, parsed.fragment))
    if urlsplit(url).scheme != "file":
        raise ValueError("security tests cannot navigate outside local file fixtures")
    return url


def _local_target(paths: _LocalPaths, fixture: str) -> _FlowRuntimeTarget:
    parsed = urlsplit(fixture)
    flow_fixture = "ready.html" if parsed.path.startswith("login-") else parsed.path
    navigation_url = _fixture_url(paths, fixture)
    target = _local_test_target(
        navigation_url=navigation_url,
        flow_url=_fixture_url(paths, flow_fixture),
        login_urls=(
            _fixture_url(paths, "login-required.html"),
            _fixture_url(paths, "login-completes.html"),
        ),
    )
    assert urlsplit(target.navigation_url).scheme == "file"
    return target


def _pretrust_failure_target(paths: _LocalPaths) -> _FlowRuntimeTarget:
    missing_local_page = (paths.fixtures / "route-does-not-exist.html").resolve().as_uri()
    return _local_test_target(
        navigation_url=missing_local_page,
        flow_url=_fixture_url(paths, "ready.html"),
        login_urls=(_fixture_url(paths, "login-required.html"),),
    )


class _DenyValueRuntime(GoogleFlowRuntime):
    """Attach only synthetic deny values while retaining the complete real runtime."""

    def __init__(
        self,
        config: FlowRuntimeConfig,
        *,
        target: _FlowRuntimeTarget,
        deny_values: tuple[str, ...],
    ) -> None:
        super().__init__(config, _target=target)
        self._deny_values = deny_values

    def run(self) -> FlowRuntimeObservation:
        try:
            return super().run()
        except FlowRuntimeError as error:
            error.evidence = replace(error.evidence, deny_values=self._deny_values)
            raise


class _ObservingDiagnosticWriter(FlowDiagnosticWriter):
    def __init__(
        self,
        diagnostics_dir: Path,
        staging_root: Path,
        observation: _LifecycleObservation,
    ) -> None:
        super().__init__(diagnostics_dir, staging_root)
        self._observation = observation

    def write_failure(
        self,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
    ) -> FlowPreflightResult:
        self._observation.raw_trace_path = evidence.raw_trace_path
        self._observation.raw_trace_existed_before_publication = bool(
            evidence.raw_trace_path is not None and evidence.raw_trace_path.is_file()
        )
        return super().write_failure(result, evidence=evidence)


class _ObservedBrowserRuntimeLock(BrowserRuntimeLock):
    def __init__(self, path: Path, observation: _LifecycleObservation) -> None:
        super().__init__(path)
        self._observation = observation

    def acquire(self) -> None:
        super().acquire()
        self._observation.lock_acquired = True

    def release(self) -> None:
        try:
            super().release()
        finally:
            self._observation.lock_released = True


class _InjectedExceptionRuntime(GoogleFlowRuntime):
    def __init__(
        self,
        config: FlowRuntimeConfig,
        *,
        target: _FlowRuntimeTarget,
        observation: _LifecycleObservation,
    ) -> None:
        super().__init__(config, _target=target)
        self._observation = observation

    def run(self) -> FlowRuntimeObservation:
        self._observation.runtime_entered_while_locked = (
            self._observation.lock_acquired and not self._observation.lock_released
        )
        raise RuntimeError("SECRET person@example.com " r"C:\Users\PrivateUser\trace.zip")


def local_preflight(
    *,
    fixture: str,
    tmp_path: Path,
    deny_values: tuple[str, ...] = (),
    login_timeout_seconds: int = 2,
    target: _FlowRuntimeTarget | None = None,
    lifecycle: _LifecycleObservation | None = None,
    inject_runtime_exception: bool = False,
) -> FlowPreflightResult:
    """Run the real local config/lock/browser/diagnostics/service preflight path."""
    paths = _local_paths(tmp_path)
    _copy_local_fixtures(paths)
    selected_target = target if target is not None else _local_target(paths, fixture)

    def local_config_resolver(
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowRuntimeConfig:
        return resolve_flow_runtime_config(
            profile_dir=profile_dir,
            diagnostics_dir=diagnostics_dir,
            login_timeout_seconds=login_timeout_seconds,
            navigation_timeout_seconds=navigation_timeout_seconds,
            environment={},
            repository_root=REPOSITORY_ROOT,
            _local_state_root=paths.state_root,
        )

    def runtime_factory(config: FlowRuntimeConfig) -> GoogleFlowRuntime:
        if inject_runtime_exception:
            if lifecycle is None:
                raise AssertionError("injected failures require lifecycle observation")
            return _InjectedExceptionRuntime(
                config,
                target=selected_target,
                observation=lifecycle,
            )
        return _DenyValueRuntime(config, target=selected_target, deny_values=deny_values)

    def lock_factory(path: Path) -> BrowserRuntimeLock:
        if lifecycle is None:
            return BrowserRuntimeLock(path)
        return _ObservedBrowserRuntimeLock(path, lifecycle)

    def writer_factory(diagnostics_dir: Path, staging_root: Path) -> FlowDiagnosticWriter:
        if lifecycle is None:
            return FlowDiagnosticWriter(diagnostics_dir, staging_root)
        return _ObservingDiagnosticWriter(diagnostics_dir, staging_root, lifecycle)

    return FlowPreflightService(
        _config_resolver=local_config_resolver,
        _lock_factory=lock_factory,
        _runtime_factory=runtime_factory,
        _diagnostic_writer_factory=writer_factory,
        _now=lambda: FIXED_NOW,
    ).preflight(
        profile_dir=paths.profile,
        diagnostics_dir=paths.diagnostics,
        login_timeout_seconds=login_timeout_seconds,
        navigation_timeout_seconds=2,
    )


def _run_dir(paths: _LocalPaths, result: FlowPreflightResult) -> Path:
    assert result.diagnostic_run_id is not None
    return paths.diagnostics / result.diagnostic_run_id


def _published_bytes(run_dir: Path) -> bytes:
    return b"\n".join(
        path.name.encode("utf-8") + b"\n" + path.read_bytes()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    )


def _expanded_zip_bytes(path: Path) -> bytes:
    with ZipFile(path) as archive:
        return b"\n".join(
            name.encode("utf-8") + b"\n" + archive.read(name) for name in sorted(archive.namelist())
        )


def _serialized_result(result: FlowPreflightResult) -> bytes:
    payload = result.model_dump(by_alias=True, mode="json", exclude_none=False)
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _assert_lock_reacquirable(path: Path) -> None:
    lock = BrowserRuntimeLock(path)
    lock.acquire()
    lock.release()


def _result_only_files(paths: _LocalPaths, result: FlowPreflightResult) -> set[str]:
    return {path.name for path in _run_dir(paths, result).iterdir() if path.is_file()}


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _resolved_imports(tree: ast.Module) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _called_attributes(tree: ast.Module) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_authenticated_ui_failure_publishes_only_sanitized_evidence(
    tmp_path: Path,
) -> None:
    """A trusted failure cannot publish secrets, profile state, suffixes, or raw staging data."""
    paths = _local_paths(tmp_path)
    profile_marker = "PROFILE_PRIVATE_MARKER_7F2A"
    paths.profile.mkdir(parents=True)
    marker_path = paths.profile / "private-marker.txt"
    marker_path.write_text(profile_marker, encoding="utf-8")
    secret_values = (
        "person@example.com",
        "COOKIE_SECRET",
        "AUTHORIZATION_SECRET",
        "STORAGE_STATE_SECRET",
        "PRIVATE_PROMPT",
        r"C:\Users\PrivateUser\profile",
        r"\\server\private\share",
        "/home/private-user/profile",
        "/Users/private-user/profile",
        "QUERY_SECRET",
        "PRIVATE_FRAGMENT",
        profile_marker,
        str(paths.profile),
        paths.profile.resolve().as_uri(),
        str(paths.diagnostics),
        paths.diagnostics.resolve().as_uri(),
        str(paths.staging),
        paths.staging.resolve().as_uri(),
    )
    query = urlencode(
        {
            "token": "QUERY_SECRET",
            "email": "person@example.com",
            "cookie": "COOKIE_SECRET",
            "authorization": "AUTHORIZATION_SECRET",
            "storage": "STORAGE_STATE_SECRET",
            "prompt": "PRIVATE_PROMPT",
            "windows": r"C:\Users\PrivateUser\profile",
            "unc": r"\\server\private\share",
            "posix": "/home/private-user/profile",
            "mac": "/Users/private-user/profile",
        }
    )
    lifecycle = _LifecycleObservation()

    result = local_preflight(
        fixture=f"missing-prompt.html?{query}#PRIVATE_FRAGMENT",
        deny_values=secret_values,
        tmp_path=tmp_path,
        lifecycle=lifecycle,
    )

    assert result.status == "ui_contract_failed"
    assert result.success is False
    assert result.screenshot == "screenshot.png"
    assert result.trace == "trace.zip"
    assert not Path(result.screenshot).is_absolute()
    assert not Path(result.trace).is_absolute()
    run_dir = _run_dir(paths, result)
    assert {path.name for path in run_dir.iterdir()} == {
        "result.json",
        "screenshot.png",
        "trace.zip",
    }
    published = _published_bytes(run_dir)
    expanded_trace = _expanded_zip_bytes(run_dir / "trace.zip")
    serialized = _serialized_result(result)
    for forbidden in (
        *secret_values,
        "?token=",
        "#PRIVATE_FRAGMENT",
        *tuple(quote(value, safe="") for value in secret_values if value),
    ):
        assert forbidden.encode("utf-8") not in published + expanded_trace + serialized
    assert lifecycle.raw_trace_existed_before_publication is True
    assert lifecycle.raw_trace_path is not None
    assert not lifecycle.raw_trace_path.exists()
    assert not any(paths.staging.rglob("*"))
    assert marker_path.read_text(encoding="utf-8") == profile_marker
    assert not paths.profile.resolve().is_relative_to(REPOSITORY_ROOT)


def test_flow_package_has_no_forbidden_integrations_or_provider_mutations() -> None:
    """A Goal 4B source change cannot add app coupling or provider-mutating browser methods."""
    forbidden_import_roots = {
        "auraly_pipeline.jobs",
        "auraly_pipeline.images",
        "auraly_pipeline.campaigns",
        "sqlalchemy",
    }
    forbidden_browser_calls = {
        "click",
        "dblclick",
        "tap",
        "fill",
        "type",
        "press",
        "check",
        "uncheck",
        "select_option",
        "set_input_files",
        "drag_and_drop",
        "drag_to",
        "expect_download",
        "expect_file_chooser",
        "dispatch_event",
        "insert_text",
        "set_checked",
        "clear",
        "focus",
        "blur",
        "hover",
        "select_text",
        "set_content",
        "add_init_script",
        "add_script_tag",
        "add_style_tag",
        "evaluate",
        "evaluate_handle",
    }
    playwright_importers: set[str] = set()
    all_calls: set[str] = set()

    for path in sorted(FLOW_SOURCE_ROOT.glob("*.py")):
        tree = _module_tree(path)
        imports = _resolved_imports(tree)
        assert not any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for imported in imports
            for forbidden in forbidden_import_roots
        ), path.name
        if any(
            imported == "playwright" or imported.startswith("playwright.") for imported in imports
        ):
            playwright_importers.add(path.name)
        all_calls.update(_called_attributes(tree))

    assert playwright_importers == {"runtime.py"}
    assert forbidden_browser_calls.isdisjoint(all_calls)

    runtime_tree = _module_tree(FLOW_SOURCE_ROOT / "runtime.py")
    launches = [
        node
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "launch_persistent_context"
    ]
    assert len(launches) == 1
    launch_keywords = {keyword.arg: keyword.value for keyword in launches[0].keywords}
    assert isinstance(launch_keywords["headless"], ast.Constant)
    assert launch_keywords["headless"].value is False
    assert "user_data_dir" in launch_keywords
    assert {"channel", "executable_path", "storage_state"}.isdisjoint(launch_keywords)


def test_flow_cli_and_service_expose_no_job_database_or_arbitrary_target_boundary() -> None:
    """Only the four approved local options may reach the independent Flow service."""
    cli_tree = _module_tree(Path(cli_module.__file__))
    command = _function_node(cli_tree, "flow_preflight_command")
    command_source = ast.unparse(command)
    forbidden = {
        "JobService",
        "ImageService",
        "CampaignService",
        "sqlalchemy",
        "repository",
        "database",
        "work_root",
        "job_id",
        "campaign_id",
        "image_generation_id",
        "url",
        "target",
    }
    assert not any(value in command_source for value in forbidden)

    assert set(inspect.signature(FlowPreflightService.preflight).parameters) == {
        "self",
        "profile_dir",
        "diagnostics_dir",
        "login_timeout_seconds",
        "navigation_timeout_seconds",
    }
    service_imports = _resolved_imports(_module_tree(Path(service_module.__file__)))
    assert not any(
        value in imported
        for imported in service_imports
        for value in ("jobs", "images", "campaigns", "sqlalchemy")
    )
    assert "_local_test_target" not in flow_package.__all__
    assert "_FlowRuntimeTarget" not in flow_package.__all__
    assert FlowRuntimeConfig.__dataclass_fields__["flow_url"].init is False
    assert "AURALY_FLOW_URL" not in Path(config_module.__file__).read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        _local_test_target(
            navigation_url="https://labs.google/fx/tools/flow",
            flow_url="https://labs.google/fx/tools/flow",
            login_urls=(),
        )


def test_ready_releases_lock_closes_browser_and_allows_immediate_second_preflight(
    tmp_path: Path,
) -> None:
    """A ready service return cannot retain its context, profile lock, or diagnostics."""
    paths = _local_paths(tmp_path)
    marker = paths.profile / "persistent-marker.txt"
    paths.profile.mkdir(parents=True)
    marker.write_text("PROFILE_SURVIVES_BROWSER_CLOSE", encoding="utf-8")

    first = local_preflight(fixture="ready.html", tmp_path=tmp_path)

    assert first.status == "ready"
    assert first.success is True
    assert first.diagnostic_run_id is None
    assert first.screenshot is None
    assert first.trace is None
    assert marker.is_file()
    assert not paths.profile.resolve().is_relative_to(REPOSITORY_ROOT)
    assert not any(paths.diagnostics.iterdir())
    _assert_lock_reacquirable(paths.lock)

    second = local_preflight(fixture="ready.html", tmp_path=tmp_path)

    assert second.status == "ready"
    assert second.status != "runtime_busy"
    assert second.success is True
    assert marker.is_file()
    assert not any(paths.diagnostics.iterdir())


def test_ui_failure_releases_lock_consumes_raw_trace_and_allows_ready_retry(
    tmp_path: Path,
) -> None:
    """Diagnostic publication must finish before lock release and an immediate retry."""
    paths = _local_paths(tmp_path)
    lifecycle = _LifecycleObservation()

    failure = local_preflight(
        fixture="missing-prompt.html",
        tmp_path=tmp_path,
        lifecycle=lifecycle,
    )

    assert failure.status == "ui_contract_failed"
    assert lifecycle.raw_trace_existed_before_publication is True
    assert lifecycle.raw_trace_path is not None and not lifecycle.raw_trace_path.exists()
    assert not any(paths.staging.rglob("*"))
    _assert_lock_reacquirable(paths.lock)

    retry = local_preflight(fixture="ready.html", tmp_path=tmp_path)

    assert retry.status == "ready"
    assert retry.status != "runtime_busy"


def test_authentication_timeout_releases_lock_and_publishes_result_only(
    tmp_path: Path,
) -> None:
    """Recognized login timeout cannot retain auth-page evidence or the runtime lock."""
    paths = _local_paths(tmp_path)
    profile_marker = "AUTH_PROFILE_PRIVATE_MARKER"
    paths.profile.mkdir(parents=True)
    (paths.profile / "auth-private-marker.txt").write_text(profile_marker, encoding="utf-8")

    result = local_preflight(
        fixture="login-required.html",
        tmp_path=tmp_path,
        login_timeout_seconds=1,
    )

    assert result.status == "authentication_required"
    assert result.failed_step == "await_manual_authentication"
    assert result.screenshot is None
    assert result.trace is None
    assert _result_only_files(paths, result) == {"result.json"}
    published = _published_bytes(_run_dir(paths, result))
    for forbidden in (profile_marker, "Google sign in", "Sign in to continue"):
        assert forbidden.encode("utf-8") not in published
    assert not any(paths.staging.rglob("*"))
    _assert_lock_reacquirable(paths.lock)


def test_pretrust_navigation_failure_releases_lock_and_publishes_result_only(
    tmp_path: Path,
) -> None:
    """A local navigation failure before trust cannot persist page evidence."""
    paths = _local_paths(tmp_path)
    _copy_local_fixtures(paths)

    result = local_preflight(
        fixture="ready.html",
        tmp_path=tmp_path,
        target=_pretrust_failure_target(paths),
    )

    assert result.status == "browser_launch_failed"
    assert result.failed_step == "navigate_flow"
    assert result.screenshot is None
    assert result.trace is None
    assert _result_only_files(paths, result) == {"result.json"}
    assert not any(paths.staging.rglob("*"))
    _assert_lock_reacquirable(paths.lock)


def test_injected_runtime_exception_is_sanitized_and_releases_real_lock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown failure after lock acquisition cannot expose its message or retain the lock."""
    paths = _local_paths(tmp_path)
    lifecycle = _LifecycleObservation()
    forbidden = (
        "SECRET",
        "person@example.com",
        r"C:\Users\PrivateUser\trace.zip",
    )

    result = local_preflight(
        fixture="ready.html",
        tmp_path=tmp_path,
        lifecycle=lifecycle,
        inject_runtime_exception=True,
    )
    captured = capsys.readouterr()

    assert lifecycle.runtime_entered_while_locked is True
    assert lifecycle.lock_released is True
    assert result.status == "browser_launch_failed"
    assert result.success is False
    assert result.screenshot is None
    assert result.trace is None
    public_data = _serialized_result(result) + _published_bytes(_run_dir(paths, result))
    boundary_output = (captured.out + captured.err).encode("utf-8")
    for value in forbidden:
        assert value.encode("utf-8") not in public_data + boundary_output
    _assert_lock_reacquirable(paths.lock)


def test_account_masking_uses_semantic_identity_locators_and_opaque_black(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trusted screenshots must use the locator contract, never coordinates or pixel inference."""
    observation = _MaskObservation()
    real_resolver = locator_module.resolve_account_identity_masks
    real_screenshot = Page.screenshot

    def resolve_semantic_masks(page: Page) -> tuple[Locator, ...]:
        masks = real_resolver(page)
        observation.semantic_masks = masks
        return masks

    def record_screenshot(page: Page, **kwargs: Any) -> bytes:
        observation.screenshot_masks = tuple(kwargs.get("mask", ()))
        observation.mask_color = kwargs.get("mask_color")
        return real_screenshot(page, **kwargs)

    monkeypatch.setattr(runtime_module, "resolve_account_identity_masks", resolve_semantic_masks)
    monkeypatch.setattr(Page, "screenshot", record_screenshot)

    result = local_preflight(fixture="missing-prompt.html", tmp_path=tmp_path)

    expected_masks = tuple(
        getattr(locator, "_impl_obj", locator) for locator in observation.semantic_masks
    )
    assert result.status == "ui_contract_failed"
    assert observation.semantic_masks
    assert observation.screenshot_masks == expected_masks
    assert observation.mask_color == "#000000"


def test_failure_diagnostics_are_unique_append_only_and_do_not_copy_profile(
    tmp_path: Path,
) -> None:
    """A later failure cannot overwrite an earlier run or copy durable profile contents."""
    paths = _local_paths(tmp_path)
    profile_marker = "PROFILE_PRIVATE_MARKER_7F2A"
    paths.profile.mkdir(parents=True)
    (paths.profile / "profile-private-marker.txt").write_text(profile_marker, encoding="utf-8")

    first = local_preflight(
        fixture="missing-prompt.html",
        tmp_path=tmp_path,
        deny_values=(profile_marker, str(paths.profile), paths.profile.resolve().as_uri()),
    )
    first_dir = _run_dir(paths, first)
    first_snapshot = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in first_dir.iterdir()
        if path.is_file()
    }

    second = local_preflight(
        fixture="missing-prompt.html",
        tmp_path=tmp_path,
        deny_values=(profile_marker, str(paths.profile), paths.profile.resolve().as_uri()),
    )

    second_dir = _run_dir(paths, second)
    assert first.diagnostic_run_id != second.diagnostic_run_id
    assert first_dir != second_dir
    assert first_dir.is_dir() and second_dir.is_dir()
    assert first_snapshot == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in first_dir.iterdir()
        if path.is_file()
    }
    assert {path.name for path in paths.diagnostics.iterdir() if path.is_dir()} == {
        first.diagnostic_run_id,
        second.diagnostic_run_id,
    }
    all_published = _published_bytes(first_dir) + _published_bytes(second_dir)
    all_published += _expanded_zip_bytes(first_dir / "trace.zip")
    all_published += _expanded_zip_bytes(second_dir / "trace.zip")
    for forbidden in (profile_marker, str(paths.profile), paths.profile.resolve().as_uri()):
        assert forbidden.encode("utf-8") not in all_published
    assert (paths.profile / "profile-private-marker.txt").is_file()

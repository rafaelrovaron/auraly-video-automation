"""Local headed-browser tests for the observation-only Flow runtime."""

from __future__ import annotations

import ast
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Iterator, cast

import pytest
from playwright.sync_api import Playwright

from auraly_pipeline.flow import (
    FlowAuthenticationTimeoutError,
    FlowBrowserLaunchError,
    FlowFailureEvidence,
    FlowRuntimeConfig,
    FlowUiContractError,
    FlowUnexpectedStateError,
    GoogleFlowRuntime,
)
from auraly_pipeline.flow.runtime import _FlowRuntimeTarget, _local_test_target
from auraly_pipeline.flow import runtime as runtime_module
from tests.flow_browser_support import fake_flow_url


def config(
    tmp_path: Path,
    *,
    login_timeout_seconds: int = 2,
) -> FlowRuntimeConfig:
    """Build an isolated already-validated configuration for a local browser test."""
    profile_dir = tmp_path / "profile"
    diagnostics_dir = tmp_path / "diagnostics"
    staging_root = tmp_path / "staging"
    for directory in (profile_dir, diagnostics_dir, staging_root):
        directory.mkdir()
    return FlowRuntimeConfig(
        profile_dir=profile_dir,
        diagnostics_dir=diagnostics_dir,
        lock_path=tmp_path / "flow.lock",
        staging_root=staging_root,
        login_timeout_seconds=login_timeout_seconds,
        navigation_timeout_seconds=2,
    )


def local_target(fixture: str) -> _FlowRuntimeTarget:
    """Treat ready.html as Flow and the two explicit login pages as authentication routes."""
    flow_fixture = "ready.html" if fixture.startswith("login-") else fixture
    return _local_test_target(
        navigation_url=fake_flow_url(fixture),
        flow_url=fake_flow_url(flow_fixture),
        login_urls=(fake_flow_url("login-required.html"), fake_flow_url("login-completes.html")),
    )


def test_ready_uses_managed_persistent_headed_context_without_actions(tmp_path: Path) -> None:
    """A valid local Flow page is only observed and returns the immutable ready observation."""
    runtime = GoogleFlowRuntime(config(tmp_path), _target=local_target("ready.html"))

    observation = runtime.run()

    assert observation.authenticated is True
    assert observation.ui_ready is True
    assert observation.status == "ready"


def test_login_timeout_has_no_screenshot_or_trace(tmp_path: Path) -> None:
    """An unfinished recognized login never produces credential-bearing browser evidence."""
    runtime = GoogleFlowRuntime(
        config(tmp_path, login_timeout_seconds=1),
        _target=local_target("login-required.html"),
    )

    with pytest.raises(FlowAuthenticationTimeoutError) as caught:
        runtime.run()

    assert caught.value.evidence == FlowFailureEvidence()


def test_login_page_can_transition_to_ready_page_without_runtime_action(tmp_path: Path) -> None:
    """The runtime re-observes a manually-completed local login transition before its deadline."""
    runtime = GoogleFlowRuntime(config(tmp_path), _target=local_target("login-completes.html"))

    assert runtime.run().status == "ready"


@pytest.mark.parametrize("fixture", ["missing-prompt.html", "ambiguous-ui.html"])
def test_ui_failure_captures_masked_screenshot_and_raw_trace_before_close(
    tmp_path: Path, fixture: str
) -> None:
    """Trusted authenticated UI-contract failures retain only transient pre-sanitization evidence."""
    runtime = GoogleFlowRuntime(config(tmp_path), _target=local_target(fixture))

    with pytest.raises(FlowUiContractError) as caught:
        runtime.run()

    evidence = caught.value.evidence
    assert evidence.screenshot_png is not None
    assert evidence.raw_trace_path is not None and evidence.raw_trace_path.is_file()


def test_blocking_modal_requires_human_intervention_with_trusted_evidence(tmp_path: Path) -> None:
    """A visible overlay is uncertain state, rather than a UI locator failure or automated action."""
    runtime = GoogleFlowRuntime(config(tmp_path), _target=local_target("blocking-modal.html"))

    with pytest.raises(FlowUnexpectedStateError) as caught:
        runtime.run()

    assert caught.value.status == "human_intervention_required"
    assert caught.value.failed_step == "verify_flow_ui"
    assert caught.value.trusted_page is True
    assert caught.value.evidence.screenshot_png is not None
    assert caught.value.evidence.raw_trace_path is not None


def test_unexpected_route_stops_before_trusted_evidence(tmp_path: Path) -> None:
    """A route outside the explicit local Flow/login allowlist cannot be treated as authenticated."""
    target = _local_test_target(
        navigation_url=fake_flow_url("ready.html"),
        flow_url=fake_flow_url("missing-prompt.html"),
        login_urls=(fake_flow_url("login-required.html"),),
    )

    with pytest.raises(FlowUnexpectedStateError) as caught:
        GoogleFlowRuntime(config(tmp_path), _target=target).run()

    assert caught.value.status == "human_intervention_required"
    assert caught.value.failed_step == "navigate_flow"
    assert caught.value.trusted_page is False
    assert caught.value.evidence == FlowFailureEvidence()


def test_query_and_fragment_are_ignored_for_route_classification(tmp_path: Path) -> None:
    """Secret-bearing URL suffixes do not alter the fixed route decision."""
    target = _local_test_target(
        navigation_url=f"{fake_flow_url('ready.html')}?token=NOT_A_RESULT#fragment",
        flow_url=fake_flow_url("ready.html"),
        login_urls=(fake_flow_url("login-required.html"),),
    )

    assert GoogleFlowRuntime(config(tmp_path), _target=target).run().status == "ready"


def test_launch_exception_before_trust_is_sanitized_browser_launch_failure(tmp_path: Path) -> None:
    """Raw Playwright errors before a trusted page map to the pre-trust launch status."""
    with pytest.raises(FlowBrowserLaunchError) as caught:
        GoogleFlowRuntime(
            config(tmp_path),
            _target=local_target("ready.html"),
            _playwright_factory=_raising_playwright_factory,
        ).run()

    assert caught.value.failed_step == "launch_browser"
    assert caught.value.evidence == FlowFailureEvidence()


def test_exception_after_trust_requires_human_intervention(tmp_path: Path) -> None:
    """A raw error after route trust cannot be reported as a browser-launch failure."""
    target = local_target("ready.html")
    page = _FakePage(url=target.flow_url)
    context = _FakeContext(page=page, trace_start_error=RuntimeError("private browser failure"))

    with pytest.raises(FlowUnexpectedStateError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert caught.value.status == "human_intervention_required"
    assert caught.value.failed_step == "verify_flow_ui"
    assert caught.value.trusted_page is True


def test_original_login_deadline_is_not_reset_by_page_activity(tmp_path: Path) -> None:
    """Repeated observed login activity cannot extend the original manual-auth deadline."""
    target = local_target("login-required.html")
    clock = _Clock()
    page = _FakePage(url=target.navigation_url, clock=clock)
    context = _FakeContext(page=page)

    with pytest.raises(FlowAuthenticationTimeoutError):
        GoogleFlowRuntime(
            config(tmp_path, login_timeout_seconds=1),
            _target=target,
            _playwright_factory=_playwright_factory(context),
            _monotonic=clock,
        ).run()

    assert page.waits == [500, 500]


def test_launches_only_persistent_headed_context_with_the_validated_profile(tmp_path: Path) -> None:
    """The runtime never substitutes a system browser, a transient context, or headless mode."""
    runtime_config = config(tmp_path)
    target = local_target("ready.html")
    context = _FakeContext(page=_FakePage(url=target.flow_url, ready=True))
    factory = _playwright_factory(context)

    observation = GoogleFlowRuntime(
        runtime_config, _target=target, _playwright_factory=factory
    ).run()

    assert observation.status == "ready"
    assert context.launch_arguments == {
        "user_data_dir": runtime_config.profile_dir,
        "headless": False,
    }
    assert context.navigation_timeout == runtime_config.navigation_timeout_seconds * 1000
    assert context.tracing.start_arguments == [
        {"screenshots": False, "snapshots": False, "sources": False}
    ]
    assert context.tracing.stop_paths == [None]
    assert context.close_calls == 1
    assert context.manager_exit_calls == 1


def test_close_failure_turns_would_be_ready_result_into_close_browser_intervention(
    tmp_path: Path,
) -> None:
    """Returning ready is forbidden unless persistent-context closure completes."""
    target = local_target("ready.html")
    page = _FakePage(url=target.flow_url, ready=True)
    context = _FakeContext(page=page, close_error=RuntimeError("private close failure"))

    with pytest.raises(FlowUnexpectedStateError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert caught.value.status == "human_intervention_required"
    assert caught.value.failed_step == "close_browser"
    assert caught.value.trusted_page is True


def test_login_redirect_before_ui_observation_never_returns_ready_or_captures_evidence(
    tmp_path: Path,
) -> None:
    """A post-auth redirect invalidates the prior trust decision before overlay/UI observation."""
    target = local_target("ready.html")
    page = _FakePage(
        url=target.flow_url,
        redirect_on_role="dialog",
        redirect_url=fake_flow_url("login-required.html"),
    )
    context = _FakeContext(page=page)

    with pytest.raises(FlowAuthenticationTimeoutError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert caught.value.trusted_page is False
    assert caught.value.evidence == FlowFailureEvidence()
    assert page.screenshot_calls == []
    assert context.tracing.stop_paths == [None]


def test_login_redirect_during_locator_failure_never_captures_trusted_evidence(tmp_path: Path) -> None:
    """A locator error cannot retain trust when the page changed to login during observation."""
    target = local_target("ready.html")
    page = _FakePage(
        url=target.flow_url,
        redirect_on_role="main",
        redirect_url=fake_flow_url("login-required.html"),
        empty_roles={"main"},
    )
    context = _FakeContext(page=page)

    with pytest.raises(FlowAuthenticationTimeoutError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert caught.value.trusted_page is False
    assert caught.value.evidence == FlowFailureEvidence()
    assert page.screenshot_calls == []
    assert context.tracing.stop_paths == [None]


def test_keyboard_interrupt_during_tracing_closes_context_and_manager_then_reraises(
    tmp_path: Path,
) -> None:
    """Cleanup is in a finally path and must not turn an interrupt into a typed runtime result."""
    target = local_target("ready.html")
    context = _FakeContext(
        page=_FakePage(url=target.flow_url),
        trace_start_error=KeyboardInterrupt(),
    )

    with pytest.raises(KeyboardInterrupt):
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert context.close_calls == 1
    assert context.manager_exit_calls == 1


def test_close_failure_after_trusted_ui_failure_preserves_managed_evidence_and_trust_consistency(
    tmp_path: Path,
) -> None:
    """A close failure may override status, but must retain the eligible trace as managed evidence."""
    target = local_target("ready.html")
    page = _FakePage(url=target.flow_url, empty_roles={"main"})
    context = _FakeContext(page=page, close_error=RuntimeError("private close failure"))

    with pytest.raises(FlowUnexpectedStateError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    evidence = caught.value.evidence
    assert caught.value.failed_step == "close_browser"
    assert caught.value.trusted_page is True
    assert evidence.trusted_page is True
    assert evidence.raw_trace_path is not None and evidence.raw_trace_path.is_file()
    assert list(configured_trace_paths(tmp_path)) == [evidence.raw_trace_path]
    assert context.close_calls == 1
    assert context.manager_exit_calls == 1


def test_runtime_uses_only_observation_methods_and_exact_evidence_calls(tmp_path: Path) -> None:
    """The source and fake boundary prevent interaction drift and pin evidence call semantics."""
    forbidden = {
        "click",
        "dblclick",
        "fill",
        "type",
        "press",
        "check",
        "uncheck",
        "select_option",
        "set_input_files",
        "expect_download",
    }
    tree = ast.parse(Path(runtime_module.__file__).read_text(encoding="utf-8"))
    called_attributes = {
        call.func.attr
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }
    assert forbidden.isdisjoint(called_attributes)

    target = local_target("ready.html")
    page = _FakePage(url=target.flow_url, empty_roles={"main"})
    context = _FakeContext(page=page)
    runtime_config = config(tmp_path)

    with pytest.raises(FlowUiContractError):
        GoogleFlowRuntime(
            runtime_config, _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert context.tracing.start_arguments == [
        {"screenshots": False, "snapshots": False, "sources": False}
    ]
    assert len(context.tracing.stop_paths) == 1
    trace_path = context.tracing.stop_paths[0]
    assert trace_path is not None and trace_path.parent == runtime_config.staging_root
    assert trace_path.is_file()
    assert page.screenshot_calls == [
        {"mask": [page.account_locator], "mask_color": "#000000"}
    ]
    assert context.close_calls == 1
    assert context.manager_exit_calls == 1


def test_trusted_failure_trace_staging_paths_are_unique(tmp_path: Path) -> None:
    """Every raw trace has a unique staging name, so concurrent diagnostic publication cannot collide."""
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_config = config(first_root)
    second_config = config(second_root)
    target = local_target("ready.html")
    first_context = _FakeContext(page=_FakePage(url=target.flow_url, empty_roles={"main"}))
    second_context = _FakeContext(page=_FakePage(url=target.flow_url, empty_roles={"main"}))

    with pytest.raises(FlowUiContractError) as first:
        GoogleFlowRuntime(
            first_config, _target=target, _playwright_factory=_playwright_factory(first_context)
        ).run()
    with pytest.raises(FlowUiContractError) as second:
        GoogleFlowRuntime(
            second_config, _target=target, _playwright_factory=_playwright_factory(second_context)
        ).run()

    first_trace = first.value.evidence.raw_trace_path
    second_trace = second.value.evidence.raw_trace_path
    assert first_trace is not None and first_trace.parent == first_config.staging_root
    assert second_trace is not None and second_trace.parent == second_config.staging_root
    assert first_trace.name != second_trace.name


@pytest.mark.parametrize("redirect_stage", ["mask", "screenshot", "trace"])
def test_evidence_time_login_redirect_never_retains_screenshot_or_trace(
    tmp_path: Path,
    redirect_stage: str,
) -> None:
    """Every evidence observation is bracketed by route trust checks before data can be retained."""
    target = local_target("ready.html")
    page = _FakePage(
        url=target.flow_url,
        empty_roles={"main"},
        evidence_redirect_stage=redirect_stage,
        redirect_url=fake_flow_url("login-required.html"),
    )
    context = _FakeContext(page=page)

    with pytest.raises(FlowAuthenticationTimeoutError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert caught.value.trusted_page is False
    assert caught.value.evidence == FlowFailureEvidence()
    assert not list(configured_trace_paths(tmp_path))


def test_close_failure_cannot_establish_trust_from_a_navigation_failed_page_url(tmp_path: Path) -> None:
    """The fixed Flow URL alone never proves authentication after navigation has failed."""
    target = local_target("ready.html")
    page = _FakePage(url=target.flow_url, goto_error=RuntimeError("private navigation failure"))
    context = _FakeContext(page=page, close_error=RuntimeError("private close failure"))

    with pytest.raises(FlowUnexpectedStateError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    assert caught.value.failed_step == "close_browser"
    assert caught.value.authenticated is False
    assert caught.value.trusted_page is False
    assert caught.value.evidence == FlowFailureEvidence()


def test_unlink_failure_after_evidence_redirect_is_sanitized_untrusted_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remaining raw staged trace is never hidden behind an ordinary auth-timeout outcome."""
    target = local_target("ready.html")
    page = _FakePage(
        url=target.flow_url,
        empty_roles={"main"},
        evidence_redirect_stage="trace",
        redirect_url=fake_flow_url("login-required.html"),
    )
    context = _FakeContext(page=page)
    cleanup_attempts: list[Path] = []

    def fail_unlink(path: Path) -> bool:
        cleanup_attempts.append(path)
        return False

    monkeypatch.setattr(runtime_module, "_remove_raw_trace", fail_unlink)

    with pytest.raises(FlowUnexpectedStateError) as caught:
        GoogleFlowRuntime(
            config(tmp_path), _target=target, _playwright_factory=_playwright_factory(context)
        ).run()

    error = caught.value
    staged_traces = list(configured_trace_paths(tmp_path))
    assert error.status == "human_intervention_required"
    assert error.failed_step == "sanitize_diagnostics"
    assert error.authenticated is False
    assert error.trusted_page is False
    assert error.evidence.trusted_page is False
    assert error.evidence.screenshot_png is None
    assert error.evidence.raw_trace_path is None
    assert str(error) == ""
    assert len(staged_traces) == 1
    assert cleanup_attempts == staged_traces


def configured_trace_paths(tmp_path: Path) -> Iterator[Path]:
    """Yield the only Task 6 raw-trace location without reading profile or diagnostics data."""
    return (tmp_path / "staging").glob("flow-trace-*.zip")


@contextmanager
def _raising_playwright_factory() -> Iterator[Playwright]:
    raise RuntimeError("private launch failure")
    yield cast(Playwright, object())


def _playwright_factory(
    context: "_FakeContext",
) -> Callable[[], AbstractContextManager[Playwright]]:
    @contextmanager
    def factory() -> Iterator[Playwright]:
        try:
            yield cast(Playwright, _FakePlaywright(context))
        finally:
            context.manager_exit_calls += 1

    return factory


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds / 1000


class _FakeLocator:
    def __init__(
        self,
        *,
        visible: bool = True,
        enabled: bool = True,
        candidates: list["_FakeLocator"] | None = None,
    ) -> None:
        self._visible = visible
        self._enabled = enabled
        self._candidates = candidates

    def all(self) -> list["_FakeLocator"]:
        return [self] if self._candidates is None else self._candidates

    def is_visible(self) -> bool:
        return self._visible

    def is_enabled(self) -> bool:
        return self._enabled


class _FakePage:
    def __init__(
        self,
        *,
        url: str,
        ready: bool = False,
        clock: _Clock | None = None,
        redirect_on_role: str | None = None,
        redirect_url: str | None = None,
        empty_roles: set[str] | None = None,
        evidence_redirect_stage: str | None = None,
        goto_error: BaseException | None = None,
    ) -> None:
        self.url = url
        self._ready = ready
        self._clock = clock
        self._redirect_on_role = redirect_on_role
        self._redirect_url = redirect_url
        self._empty_roles = empty_roles or set()
        self._evidence_redirect_stage = evidence_redirect_stage
        self._goto_error = goto_error
        self.waits: list[int] = []
        self.account_locator = _FakeLocator()
        self.screenshot_calls: list[dict[str, object]] = []

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.url = url
        if self._goto_error is not None:
            raise self._goto_error

    def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)
        if self._clock is not None:
            self._clock.advance(timeout)

    def get_by_role(self, role: str, *, name: str | None = None, exact: bool | None = None) -> _FakeLocator:
        if role == self._redirect_on_role and self._redirect_url is not None:
            self.url = self._redirect_url
        if role in {"dialog", "alertdialog"}:
            return _FakeLocator(candidates=[])
        if role in self._empty_roles:
            return _FakeLocator(candidates=[])
        if role == "button" and name == "Google Account":
            if self._evidence_redirect_stage == "mask" and self._redirect_url is not None:
                self.url = self._redirect_url
            return self.account_locator
        return _FakeLocator()

    def get_by_label(self, text: str, *, exact: bool | None = None) -> _FakeLocator:
        return _FakeLocator()

    def get_by_placeholder(self, text: str, *, exact: bool | None = None) -> _FakeLocator:
        return _FakeLocator()

    def get_by_text(self, text: str, *, exact: bool | None = None) -> _FakeLocator:
        return _FakeLocator()

    def screenshot(self, **kwargs: object) -> bytes:
        self.screenshot_calls.append(kwargs)
        if self._evidence_redirect_stage == "screenshot" and self._redirect_url is not None:
            self.url = self._redirect_url
        return b"masked-screenshot"


class _FakeTracing:
    def __init__(
        self,
        *,
        page: _FakePage,
        start_error: BaseException | None = None,
    ) -> None:
        self._page = page
        self._start_error = start_error
        self.start_arguments: list[dict[str, object]] = []
        self.stop_paths: list[Path | None] = []

    def start(self, **kwargs: object) -> None:
        self.start_arguments.append(kwargs)
        if self._start_error is not None:
            raise self._start_error

    def stop(self, *, path: Path | None = None) -> None:
        self.stop_paths.append(path)
        if path is not None:
            path.write_bytes(b"trace")
        if self._page._evidence_redirect_stage == "trace" and self._page._redirect_url is not None:
            self._page.url = self._page._redirect_url


class _FakeContext:
    def __init__(
        self,
        *,
        page: _FakePage,
        trace_start_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.pages = [page]
        self.tracing = _FakeTracing(page=page, start_error=trace_start_error)
        self._close_error = close_error
        self.launch_arguments: dict[str, object] | None = None
        self.navigation_timeout: int | None = None
        self.close_calls = 0
        self.manager_exit_calls = 0

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.navigation_timeout = timeout

    def close(self) -> None:
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error


class _FakeChromium:
    def __init__(self, context: _FakeContext) -> None:
        self._context = context

    def launch_persistent_context(self, **kwargs: object) -> _FakeContext:
        self._context.launch_arguments = kwargs
        return self._context


class _FakePlaywright:
    def __init__(self, context: _FakeContext) -> None:
        self.chromium = _FakeChromium(context)

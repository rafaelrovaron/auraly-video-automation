"""Local headed-browser tests for the observation-only Flow runtime."""

from __future__ import annotations

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


@contextmanager
def _raising_playwright_factory() -> Iterator[Playwright]:
    raise RuntimeError("private launch failure")
    yield cast(Playwright, object())


def _playwright_factory(
    context: "_FakeContext",
) -> Callable[[], AbstractContextManager[Playwright]]:
    @contextmanager
    def factory() -> Iterator[Playwright]:
        yield cast(Playwright, _FakePlaywright(context))

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
    def __init__(self, *, url: str, ready: bool = False, clock: _Clock | None = None) -> None:
        self.url = url
        self._ready = ready
        self._clock = clock
        self.waits: list[int] = []

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.url = url

    def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)
        if self._clock is not None:
            self._clock.advance(timeout)

    def get_by_role(self, role: str, *, name: str | None = None, exact: bool | None = None) -> _FakeLocator:
        if role in {"dialog", "alertdialog"}:
            return _FakeLocator(candidates=[])
        return _FakeLocator()

    def get_by_label(self, text: str, *, exact: bool | None = None) -> _FakeLocator:
        return _FakeLocator()

    def get_by_placeholder(self, text: str, *, exact: bool | None = None) -> _FakeLocator:
        return _FakeLocator()

    def get_by_text(self, text: str, *, exact: bool | None = None) -> _FakeLocator:
        return _FakeLocator()

    def screenshot(self, **_: object) -> bytes:
        return b"masked-screenshot"


class _FakeTracing:
    def __init__(self, *, start_error: BaseException | None = None) -> None:
        self._start_error = start_error

    def start(self, **_: object) -> None:
        if self._start_error is not None:
            raise self._start_error

    def stop(self, *, path: Path | None = None) -> None:
        if path is not None:
            path.write_bytes(b"trace")


class _FakeContext:
    def __init__(
        self,
        *,
        page: _FakePage,
        trace_start_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.pages = [page]
        self.tracing = _FakeTracing(start_error=trace_start_error)
        self._close_error = close_error
        self.launch_arguments: dict[str, object] | None = None
        self.navigation_timeout: int | None = None

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.navigation_timeout = timeout

    def close(self) -> None:
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

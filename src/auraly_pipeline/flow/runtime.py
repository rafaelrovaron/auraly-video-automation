"""Headed, observation-only Playwright runtime for the fixed Google Flow route."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import time
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import BrowserContext, Locator, Page, Playwright, sync_playwright

from .config import FlowRuntimeConfig
from .generation_domain import FlowWorkspaceIdentity
from .domain import (
    FLOW_URL,
    FlowAuthenticationTimeoutError,
    FlowBrowserLaunchError,
    FlowFailureEvidence,
    FlowRuntimeError,
    FlowRuntimeObservation,
    FlowUnexpectedStateError,
)
from .locators import REQUIRED_FLOW_LOCATORS, blocking_overlay_present, resolve_account_identity_masks
from .locators import resolve_required_locator


@dataclass(frozen=True)
class _FlowRuntimeTarget:
    """Fixed routing policy; local variants are a private browser-test seam only."""

    navigation_url: str
    flow_url: str
    flow_origin: str
    flow_path: str
    authentication_origin: str
    authentication_paths: frozenset[str] | None


@dataclass
class _RawTraceState:
    """One allocated raw trace remains owned until it is attached safely or removed once."""

    path: Path | None = None
    attached: bool = False
    cleanup_attempted: bool = False
    stop_attempted: bool = False


PRODUCTION_TARGET = _FlowRuntimeTarget(
    navigation_url=FLOW_URL,
    flow_url=FLOW_URL,
    flow_origin="https://labs.google",
    flow_path="/fx/tools/flow",
    authentication_origin="https://accounts.google.com",
    authentication_paths=frozenset(
        {
            "/signin/v2/identifier",
            "/signin/v2/challenge/pwd",
            "/signin/v2/challenge/selection",
            "/signin/v2/challenge/totp",
            "/signin/v2/challenge/ipp",
            "/signin/v2/challenge/dp",
            "/signin/v2/challenge/sk",
            "/signin/v2/challenge/wa",
            "/signin/v2/challenge/az",
        }
    ),
)

_SAFE_WORKSPACE_PATH = re.compile(r"^fx/tools/flow(?:/[a-z0-9][a-z0-9_-]*)+$")


class FlowBrowserSession:
    """Package-internal headed authenticated-page lifecycle shared by Flow workers."""

    def __init__(
        self,
        config: FlowRuntimeConfig,
        *,
        _target: _FlowRuntimeTarget = PRODUCTION_TARGET,
        _playwright_factory: Callable[[], AbstractContextManager[Playwright]] = sync_playwright,
        _monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._target = _target
        self._playwright_factory = _playwright_factory
        self._monotonic = _monotonic
        self._manager: AbstractContextManager[Playwright] | None = None
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._closed = False
        self._navigation_started = False

    @property
    def page(self) -> Page:
        if self._page is None:
            raise FlowBrowserLaunchError()
        return self._page

    @property
    def _context_for_runtime(self) -> BrowserContext | None:
        return self._context

    @property
    def navigation_started(self) -> bool:
        return self._navigation_started

    def __enter__(self) -> "FlowBrowserSession":
        try:
            self.open()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> Literal[False]:
        if self.close():
            raise FlowUnexpectedStateError(failed_step="close_browser")
        return False

    def open(self) -> Page:
        """Launch only Playwright's headed persistent context and await manual authentication."""
        self._manager = self._playwright_factory()
        self._playwright = self._manager.__enter__()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._config.profile_dir,
            headless=False,
        )
        self._context.set_default_navigation_timeout(self._config.navigation_timeout_seconds * 1000)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._navigation_started = True
        self._page.goto(
            self._target.navigation_url,
            wait_until="domcontentloaded",
            timeout=self._config.navigation_timeout_seconds * 1000,
        )
        self._await_authenticated_flow_page()
        return self._page

    def close(self) -> bool:
        """Close context and Playwright manager once; callers map the boolean to their contract."""
        if self._closed:
            return False
        self._closed = True
        close_failed = False
        if self._context is not None:
            try:
                self._context.close()
            except BaseException:
                close_failed = True
        if self._playwright is not None and self._manager is not None:
            try:
                self._manager.__exit__(None, None, None)
            except BaseException:
                close_failed = True
        return close_failed

    def require_current_flow_page(self) -> None:
        """Fail closed when an authenticated session redirects away from the fixed Flow route."""
        classification = _classify_url(self.page.url, self._target)
        if classification == "flow":
            return
        if classification == "login":
            raise FlowAuthenticationTimeoutError()
        raise FlowUnexpectedStateError(failed_step="navigate_flow")

    def workspace_identity(self) -> FlowWorkspaceIdentity:
        """Derive the only persistable workspace identity from the trusted current provider route."""
        return _workspace_identity_for_url(self.page.url)

    def _await_authenticated_flow_page(self) -> None:
        deadline = self._monotonic() + self._config.login_timeout_seconds
        while True:
            classification = _classify_url(self.page.url, self._target)
            if classification == "flow":
                return
            if classification == "unexpected":
                raise FlowUnexpectedStateError(failed_step="navigate_flow")
            if self._monotonic() >= deadline:
                raise FlowAuthenticationTimeoutError()
            self.page.wait_for_timeout(500)

    def capture_trusted_evidence(
        self,
        *,
        context: BrowserContext | None,
        tracing_started: bool,
        raw_trace: _RawTraceState,
    ) -> FlowFailureEvidence | None:
        """Capture masked evidence only while this session remains on the trusted Flow route."""
        if raw_trace.stop_attempted:
            return None
        screenshot_png: bytes | None = None
        try:
            for attempt in range(2):
                self.require_current_flow_page()
                masks = _screenshot_masks(self.page)
                self.require_current_flow_page()
                if not masks:
                    raise FlowUnexpectedStateError(
                        failed_step="sanitize_diagnostics",
                        authenticated=True,
                        trusted_page=True,
                        evidence=FlowFailureEvidence(trusted_page=True),
                    )
                try:
                    screenshot_png = self.page.screenshot(mask=masks, mask_color="#000000")
                except Exception:
                    if attempt == 1:
                        raise
                    self.require_current_flow_page()
                else:
                    break
            if not self.current_page_is_flow():
                return None
            if tracing_started and context is not None:
                raw_trace.path = self._config.staging_root / f"flow-trace-{uuid4().hex}.zip"
                raw_trace.stop_attempted = True
                context.tracing.stop(path=raw_trace.path)
                if not self.current_page_is_flow():
                    return None
        except FlowRuntimeError:
            raise
        except Exception:
            if not self.current_page_is_flow():
                return None
            cleanup_failure = self.discard_raw_trace(raw_trace, trusted_page=True)
            if cleanup_failure is not None:
                raise cleanup_failure from None
            raise FlowUnexpectedStateError(
                failed_step="sanitize_diagnostics",
                authenticated=True,
                trusted_page=True,
                evidence=FlowFailureEvidence(trusted_page=True),
            ) from None
        finally:
            if not self.current_page_is_flow():
                cleanup_failure = self.discard_raw_trace(raw_trace, trusted_page=False)
                if tracing_started and not raw_trace.stop_attempted:
                    raw_trace.stop_attempted = True
                    self.stop_trace_without_artifact(context)
                if cleanup_failure is not None:
                    raise cleanup_failure
        return FlowFailureEvidence(
            screenshot_png=screenshot_png,
            raw_trace_path=raw_trace.path,
            trusted_page=True,
        )

    @staticmethod
    def discard_raw_trace(
        raw_trace: _RawTraceState,
        *,
        trusted_page: bool,
    ) -> FlowRuntimeError | None:
        """Remove one untrusted raw trace without exposing its staging path."""
        if raw_trace.path is None or raw_trace.cleanup_attempted:
            return None
        raw_trace.cleanup_attempted = True
        raw_trace.attached = False
        if _remove_raw_trace(raw_trace.path):
            raw_trace.path = None
            return None
        return FlowUnexpectedStateError(
            failed_step="sanitize_diagnostics",
            authenticated=trusted_page,
            trusted_page=trusted_page,
            evidence=FlowFailureEvidence(trusted_page=trusted_page),
        )

    def current_page_is_flow(self) -> bool:
        return _classify_url(self.page.url, self._target) == "flow"

    @staticmethod
    def stop_trace_without_artifact(context: BrowserContext | None) -> None:
        if context is None:
            return
        try:
            context.tracing.stop()
        except BaseException:
            return


def _local_test_target(
    *,
    navigation_url: str,
    flow_url: str,
    login_urls: tuple[str, ...],
) -> _FlowRuntimeTarget:
    """Build a local-file routing policy for deterministic tests inside this module's package."""
    flow = urlsplit(flow_url)
    if flow.scheme != "file" or any(urlsplit(url).scheme != "file" for url in login_urls):
        raise ValueError("local Flow targets require file URLs")
    return _FlowRuntimeTarget(
        navigation_url=navigation_url,
        flow_url=flow_url,
        flow_origin=_origin(flow_url),
        flow_path=flow.path,
        authentication_origin="file://",
        authentication_paths=frozenset(urlsplit(url).path for url in login_urls),
    )


class GoogleFlowRuntime:
    """Run one headed persistent-context preflight without changing Flow or login state."""

    def __init__(
        self,
        config: FlowRuntimeConfig,
        *,
        _target: _FlowRuntimeTarget | None = None,
        _playwright_factory: Callable[[], AbstractContextManager[Playwright]] = sync_playwright,
        _monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._target = PRODUCTION_TARGET if _target is None else _target
        self._playwright_factory = _playwright_factory
        self._monotonic = _monotonic

    def run(self) -> FlowRuntimeObservation:
        """Run observation-only preflight, close all browser resources, or raise a typed failure."""
        context: BrowserContext | None = None
        page: Page | None = None
        trusted_page = False
        tracing_started = False
        failure: FlowRuntimeError | None = None
        observation: FlowRuntimeObservation | None = None
        phase: Literal["launch_browser", "navigate_flow", "verify_flow_ui"] = "launch_browser"
        close_failed = False
        raw_trace = _RawTraceState()
        raw_trace_cleanup_failure: FlowRuntimeError | None = None
        base_exception_escaping = False
        session = FlowBrowserSession(
            self._config,
            _target=self._target,
            _playwright_factory=self._playwright_factory,
            _monotonic=self._monotonic,
        )

        try:
            try:
                page = session.open()
                context = session._context_for_runtime
                if context is None:
                    raise FlowBrowserLaunchError()
                phase = "navigate_flow"
                trusted_page = True

                context.tracing.start(screenshots=False, snapshots=False, sources=False)
                tracing_started = True
                phase = "verify_flow_ui"
                session.require_current_flow_page()
                if blocking_overlay_present(page):
                    raise FlowUnexpectedStateError(
                        failed_step="verify_flow_ui",
                        authenticated=True,
                        trusted_page=True,
                    )
                for locator_name in REQUIRED_FLOW_LOCATORS:
                    session.require_current_flow_page()
                    resolve_required_locator(page, locator_name)
                    session.require_current_flow_page()
                session.require_current_flow_page()

                raw_trace.stop_attempted = True
                context.tracing.stop()
                tracing_started = False
                session.require_current_flow_page()
                observation = FlowRuntimeObservation()
            except FlowRuntimeError as caught:
                failure = self._route_safe_failure(caught, page)
            except Exception:
                if trusted_page:
                    failure = self._route_safe_failure(
                        FlowUnexpectedStateError(
                            failed_step=(
                                "sanitize_diagnostics"
                                if raw_trace.stop_attempted
                                else "verify_flow_ui"
                            ),
                            authenticated=True,
                            trusted_page=True,
                        ),
                        page,
                    )
                elif phase == "navigate_flow" or session.navigation_started:
                    failure = FlowBrowserLaunchError(failed_step="navigate_flow")
                else:
                    failure = FlowBrowserLaunchError()

            if failure is not None:
                failure = self._route_safe_failure(failure, page)
                if failure.trusted_page and page is not None:
                    evidence = session.capture_trusted_evidence(
                        context=context,
                        tracing_started=tracing_started,
                        raw_trace=raw_trace,
                    )
                    tracing_started = False
                    if evidence is None:
                        failure = self._route_safe_failure(failure, page)
                    else:
                        failure.evidence = evidence
                        raw_trace.attached = evidence.raw_trace_path is not None
                elif tracing_started:
                    session.stop_trace_without_artifact(context)
                    tracing_started = False
        except BaseException:
            base_exception_escaping = True
            raise
        finally:
            if tracing_started and not raw_trace.stop_attempted:
                session.stop_trace_without_artifact(context)
            if raw_trace.path is not None and (
                not raw_trace.attached or base_exception_escaping
            ):
                raw_trace_cleanup_failure = session.discard_raw_trace(
                    raw_trace,
                    trusted_page=trusted_page and page is not None and session.current_page_is_flow(),
                )
                if raw_trace_cleanup_failure is not None and not base_exception_escaping:
                    failure = raw_trace_cleanup_failure
                    observation = None
            close_failed = session.close()

        if close_failed:
            close_failure = self._close_failure(failure, page, trusted_page)
            if close_failure.trusted_page:
                failure = close_failure
            else:
                cleanup_failure = session.discard_raw_trace(raw_trace, trusted_page=False)
                failure = cleanup_failure if cleanup_failure is not None else close_failure
            observation = None

        if failure is not None:
            raise failure
        if observation is None:
            raise FlowUnexpectedStateError(failed_step="close_browser", trusted_page=trusted_page)
        return observation

    def _route_safe_failure(
        self,
        failure: FlowRuntimeError,
        page: Page | None,
    ) -> FlowRuntimeError:
        """Remove trusted status when the current page no longer matches the Flow route."""
        if page is None or not failure.trusted_page:
            return failure
        classification = _classify_url(page.url, self._target)
        if classification == "flow":
            return failure
        if classification == "login":
            return FlowAuthenticationTimeoutError()
        return FlowUnexpectedStateError(failed_step="navigate_flow")

    def _close_failure(
        self,
        previous_failure: FlowRuntimeError | None,
        page: Page | None,
        trusted_flow_established: bool,
    ) -> FlowUnexpectedStateError:
        """Override a result only after preserving evidence whose route remains trusted."""
        trusted_page = (
            trusted_flow_established
            and page is not None
            and _classify_url(page.url, self._target) == "flow"
        )
        previous_evidence = (
            previous_failure.evidence if previous_failure is not None else FlowFailureEvidence()
        )
        evidence = FlowFailureEvidence(
            screenshot_png=previous_evidence.screenshot_png if trusted_page else None,
            raw_trace_path=previous_evidence.raw_trace_path if trusted_page else None,
            deny_values=previous_evidence.deny_values,
            trusted_page=trusted_page,
        )
        return FlowUnexpectedStateError(
            failed_step="close_browser",
            authenticated=trusted_page,
            trusted_page=trusted_page,
            evidence=evidence,
        )

def _classify_url(url: str, target: _FlowRuntimeTarget) -> Literal["flow", "login", "unexpected"]:
    """Classify only exact allowlisted origin/path pairs, deliberately ignoring suffixes."""
    parsed = urlsplit(url)
    origin = _origin(url)
    if origin == target.flow_origin and parsed.path == target.flow_path:
        return "flow"
    if origin != target.authentication_origin:
        return "unexpected"
    if target.authentication_paths is None or parsed.path in target.authentication_paths:
        return "login"
    return "unexpected"


def _workspace_identity_for_url(url: str) -> FlowWorkspaceIdentity:
    """Convert only a fixed-origin Flow workspace subpath into safe durable facts."""
    parsed = urlsplit(url)
    workspace_path = parsed.path.removeprefix("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "labs.google"
        or parsed.query
        or parsed.fragment
        or _SAFE_WORKSPACE_PATH.fullmatch(workspace_path) is None
    ):
        raise FlowUnexpectedStateError(failed_step="navigate_flow")
    return FlowWorkspaceIdentity(
        workspace_path=workspace_path,
        fingerprint=hashlib.sha256(workspace_path.encode("utf-8")).hexdigest(),
    )


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _remove_raw_trace(path: Path) -> bool:
    """Remove a redirect-time trace and report whether staging no longer contains it."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _screenshot_masks(page: Page) -> list[Locator]:
    """Adapt the semantic masks to the installed synchronous Playwright implementation."""
    return [cast(Locator, getattr(mask, "_impl_obj", mask)) for mask in resolve_account_identity_masks(page)]

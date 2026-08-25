"""Headed, observation-only Playwright runtime for the fixed Google Flow route."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import BrowserContext, Locator, Page, Playwright, sync_playwright

from .config import FlowRuntimeConfig
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


PRODUCTION_TARGET = _FlowRuntimeTarget(
    navigation_url=FLOW_URL,
    flow_url=FLOW_URL,
    flow_origin="https://labs.google",
    flow_path="/fx/tools/flow",
    authentication_origin="https://accounts.google.com",
    authentication_paths=None,
)


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
        manager: AbstractContextManager[Playwright] | None = None
        playwright: Playwright | None = None
        context: BrowserContext | None = None
        page: Page | None = None
        trusted_page = False
        tracing_started = False
        failure: FlowRuntimeError | None = None
        observation: FlowRuntimeObservation | None = None
        phase: Literal["launch_browser", "navigate_flow", "verify_flow_ui"] = "launch_browser"
        close_failed = False

        try:
            try:
                manager = self._playwright_factory()
                playwright = manager.__enter__()
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=self._config.profile_dir,
                    headless=False,
                )
                context.set_default_navigation_timeout(self._config.navigation_timeout_seconds * 1000)
                page = context.pages[0] if context.pages else context.new_page()
                phase = "navigate_flow"
                page.goto(
                    self._target.navigation_url,
                    wait_until="domcontentloaded",
                    timeout=self._config.navigation_timeout_seconds * 1000,
                )
                self._await_authenticated_flow_page(page)
                trusted_page = True

                context.tracing.start(screenshots=False, snapshots=False, sources=False)
                tracing_started = True
                phase = "verify_flow_ui"
                self._require_current_flow_page(page)
                if blocking_overlay_present(page):
                    raise FlowUnexpectedStateError(
                        failed_step="verify_flow_ui",
                        authenticated=True,
                        trusted_page=True,
                    )
                for locator_name in REQUIRED_FLOW_LOCATORS:
                    self._require_current_flow_page(page)
                    resolve_required_locator(page, locator_name)
                    self._require_current_flow_page(page)
                self._require_current_flow_page(page)

                context.tracing.stop()
                tracing_started = False
                self._require_current_flow_page(page)
                observation = FlowRuntimeObservation()
            except FlowRuntimeError as caught:
                failure = self._route_safe_failure(caught, page)
            except Exception:
                if trusted_page:
                    failure = self._route_safe_failure(
                        FlowUnexpectedStateError(
                            failed_step="verify_flow_ui",
                            authenticated=True,
                            trusted_page=True,
                        ),
                        page,
                    )
                elif phase == "navigate_flow":
                    failure = FlowBrowserLaunchError(failed_step="navigate_flow")
                else:
                    failure = FlowBrowserLaunchError()

            if failure is not None:
                failure = self._route_safe_failure(failure, page)
                if failure.trusted_page and page is not None:
                    failure.evidence = self._capture_trusted_evidence(
                        page=page,
                        context=context,
                        tracing_started=tracing_started,
                    )
                    tracing_started = False
                elif tracing_started:
                    self._stop_trace_without_artifact(context)
                    tracing_started = False
        finally:
            if tracing_started:
                self._stop_trace_without_artifact(context)
            close_failed = self._close_resources(manager, context, playwright is not None)

        if close_failed:
            failure = self._close_failure(failure, page)
            observation = None

        if failure is not None:
            raise failure
        if observation is None:
            raise FlowUnexpectedStateError(failed_step="close_browser", trusted_page=trusted_page)
        return observation

    def _require_current_flow_page(self, page: Page) -> None:
        """Fail closed when a once-authenticated page redirects during observation."""
        classification = _classify_url(page.url, self._target)
        if classification == "flow":
            return
        if classification == "login":
            raise FlowAuthenticationTimeoutError()
        raise FlowUnexpectedStateError(failed_step="navigate_flow")

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
    ) -> FlowUnexpectedStateError:
        """Override a result only after preserving evidence whose route remains trusted."""
        trusted_page = page is not None and _classify_url(page.url, self._target) == "flow"
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

    def _await_authenticated_flow_page(self, page: Page) -> None:
        deadline = self._monotonic() + self._config.login_timeout_seconds
        while True:
            classification = _classify_url(page.url, self._target)
            if classification == "flow":
                return
            if classification == "unexpected":
                raise FlowUnexpectedStateError(failed_step="navigate_flow")
            if self._monotonic() >= deadline:
                raise FlowAuthenticationTimeoutError()
            page.wait_for_timeout(500)

    def _capture_trusted_evidence(
        self,
        *,
        page: Page,
        context: BrowserContext | None,
        tracing_started: bool,
    ) -> FlowFailureEvidence:
        screenshot_png: bytes | None = None
        raw_trace_path: Path | None = None
        try:
            screenshot_png = page.screenshot(
                mask=_screenshot_masks(page),
                mask_color="#000000",
            )
            if tracing_started and context is not None:
                raw_trace_path = self._config.staging_root / f"flow-trace-{uuid4().hex}.zip"
                context.tracing.stop(path=raw_trace_path)
        except Exception:
            return FlowFailureEvidence(trusted_page=True)
        return FlowFailureEvidence(
            screenshot_png=screenshot_png,
            raw_trace_path=raw_trace_path,
            trusted_page=True,
        )

    @staticmethod
    def _stop_trace_without_artifact(context: BrowserContext | None) -> None:
        """Terminate tracing after an untrusted state without retaining a credential-bearing archive."""
        if context is None:
            return
        try:
            context.tracing.stop()
        except BaseException:
            return

    @staticmethod
    def _close_resources(
        manager: AbstractContextManager[Playwright] | None,
        context: BrowserContext | None,
        entered: bool,
    ) -> bool:
        close_failed = False
        if context is not None:
            try:
                context.close()
            except BaseException:
                close_failed = True
        if entered and manager is not None:
            try:
                manager.__exit__(None, None, None)
            except BaseException:
                close_failed = True
        return close_failed


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


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _screenshot_masks(page: Page) -> list[Locator]:
    """Adapt the semantic masks to the installed synchronous Playwright implementation."""
    return [cast(Locator, getattr(mask, "_impl_obj", mask)) for mask in resolve_account_identity_masks(page)]

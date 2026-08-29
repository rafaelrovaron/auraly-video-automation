"""Deterministic local-browser coverage for authenticated Flow generation dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
import hashlib
import inspect
import json
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright
import pytest

from auraly_pipeline.flow.config import FlowGenerationConfig
from auraly_pipeline.flow.config import FlowRuntimeConfig
from auraly_pipeline.flow import generation as generation_module
from auraly_pipeline.flow.generation import (
    FlowGenerationCheckpointSink,
    FlowGenerationRequest,
    FlowGenerationRuntime,
)
from auraly_pipeline.flow.generation_domain import (
    FlowDispatchAmbiguousError,
    FlowGenerationObservation,
    FlowGenerationRuntimeError,
    FlowWorkspaceIdentity,
)
from auraly_pipeline.flow.generation_locators import (
    _GenerationLocatorTarget,
    _local_test_target,
)


FLOW_GENERATION_ROOT = Path(__file__).parent / "fakes" / "flow-generation"


def _fixture_url(name: str) -> str:
    return (FLOW_GENERATION_ROOT / name).resolve(strict=True).as_uri()


LOCAL_TARGET = _local_test_target(
    *(_fixture_url(path.name) for path in FLOW_GENERATION_ROOT.glob("*.html"))
)


class _LocalAuthenticatedSession:
    def __init__(self, page: Page, target: _GenerationLocatorTarget) -> None:
        self.page = page
        self._target = target

    def require_current_flow_page(self) -> None:
        if not self._target.allows_url(self.page.url):
            raise FlowGenerationRuntimeError(failed_step="open_workspace")

    def workspace_identity(self) -> FlowWorkspaceIdentity:
        self.require_current_flow_page()
        return _workspace()


@contextmanager
def _session(
    page: Page,
    *,
    close_error: BaseException | None = None,
) -> Iterator[_LocalAuthenticatedSession]:
    yield _LocalAuthenticatedSession(page, LOCAL_TARGET)
    if close_error is not None:
        raise close_error


def _runtime_for_fixture(
    fixture: str,
    page: Page,
    *,
    upload_completes: bool = True,
    set_input_files: Callable[[Locator, Path], None] | None = None,
    close_error: BaseException | None = None,
    generation_timeout_seconds: int = 1,
) -> FlowGenerationRuntime:
    page.goto(_fixture_url(fixture))
    if upload_completes:
        page.locator('input[type="file"]').evaluate(
            """element => element.addEventListener('change', () => {
                const status = document.createElement('output');
                status.setAttribute('role', 'status');
                status.setAttribute('aria-label', 'Reference upload complete');
                status.textContent = 'Complete';
                document.querySelector('main').appendChild(status);
            })"""
        )
    def session_factory() -> AbstractContextManager[_LocalAuthenticatedSession]:
        return _session(page, close_error=close_error)

    return FlowGenerationRuntime(
        FlowGenerationConfig(
            generation_timeout_seconds=generation_timeout_seconds,
            download_timeout_seconds=1,
        ),
        _session_factory=session_factory,
        _locator_target=LOCAL_TARGET,
        _set_input_files=set_input_files,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _workspace() -> FlowWorkspaceIdentity:
    return FlowWorkspaceIdentity(
        workspace_path="fx/tools/flow/local-workspace",
        fingerprint=_sha256_text("local-workspace"),
    )


def _prepared_request(reference_png: Path) -> FlowGenerationRequest:
    prompt = "private prompt"
    return FlowGenerationRequest(
        reference_path=reference_png,
        reference_sha256=_sha256(reference_png),
        prompt_snapshot=prompt,
        prompt_sha256=_sha256_text(prompt),
        workspace=_workspace(),
    )


class _CheckpointSink(FlowGenerationCheckpointSink):
    def __init__(self, page: Page) -> None:
        self.events: list[str] = []
        self.click_counts: list[int] = []
        self._page = page

    def _record(self, event: str) -> None:
        self.events.append(event)
        self.click_counts.append(int(self._page.evaluate("window.generateClicks || 0")))

    def record_inputs_verified(self, observation: FlowGenerationObservation) -> None:
        assert observation.reference_verified and observation.prompt_verified
        self._record("inputs_verified")

    def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None:
        assert workspace == _workspace()
        self._record("dispatch_intent_recorded")

    def record_dispatch_confirmed(self, observation: FlowGenerationObservation) -> None:
        assert observation.reference_verified and observation.prompt_verified
        self._record("dispatch_confirmed")


@pytest.fixture(scope="module", name="flow_generation_page")
def provide_flow_generation_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            yield browser.new_page()
        finally:
            browser.close()


@pytest.fixture(name="reference_png")
def provide_reference_png(tmp_path: Path) -> Path:
    path = tmp_path / "private-reference-name.png"
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cff0000004010100f51edb560000000049454e44"
            "ae426082"
        )
    )
    return path


def test_prepare_uploads_reference_and_verifies_prompt_hash(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)

    observed = runtime.prepare_inputs(
        reference_path=reference_png,
        reference_sha256=_sha256(reference_png),
        prompt_snapshot="private prompt",
        prompt_sha256=_sha256_text("private prompt"),
    )

    assert observed.reference_verified is True
    assert observed.prompt_verified is True
    assert observed.model_dump() == {
        "reference_verified": True,
        "prompt_verified": True,
    }


def test_prepare_rejects_wrong_reference_hash_before_browser_upload(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256="0" * 64,
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )

    assert raised.value.failed_step == "verify_reference"
    assert flow_generation_page.locator('input[type="file"]').input_value() == ""


@pytest.mark.parametrize(
    "mutation",
    (
        "document.querySelector('input[type=file]').remove()",
        "document.querySelector('main').insertAdjacentHTML('beforeend', '<input type=file aria-label=\"Reference image\">')",
    ),
)
def test_prepare_rejects_missing_or_ambiguous_reference_input(
    flow_generation_page: Page,
    reference_png: Path,
    mutation: str,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    flow_generation_page.evaluate(mutation)

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )

    assert raised.value.failed_step == "upload_reference"


def test_prepare_requires_positive_upload_complete_state(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture(
        "ready.html",
        flow_generation_page,
        upload_completes=False,
    )

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )

    assert raised.value.failed_step == "verify_reference"


def test_prepare_rejects_prompt_readback_hash_mismatch(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    flow_generation_page.locator("textarea").evaluate(
        "element => element.addEventListener('input', event => event.target.value = 'mismatch')"
    )

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )

    assert raised.value.failed_step == "verify_prompt"


def test_prepare_rejects_route_change_after_upload(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    def upload_then_redirect(locator: Locator, path: Path) -> None:
        locator.set_input_files(path)
        flow_generation_page.goto("data:text/html,redirected")

    runtime = _runtime_for_fixture(
        "ready.html",
        flow_generation_page,
        set_input_files=upload_then_redirect,
    )

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )

    assert raised.value.failed_step == "open_workspace"


def test_prepare_rejects_blocking_overlay(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    flow_generation_page.evaluate(
        "document.body.insertAdjacentHTML('beforeend', '<dialog open aria-label=\"Blocking\"></dialog>')"
    )

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )

    assert raised.value.failed_step == "upload_reference"


def test_prepare_sanitizes_injected_file_input_error_and_metadata(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    prompt = "PRIVATE_PROMPT_VALUE"
    auth = "Bearer PRIVATE_AUTH_VALUE"
    dom = "<main data-private='DOM_PRIVATE_VALUE'>"
    token_url = "https://labs.google/fx/tools/flow?token=PRIVATE_TOKEN_VALUE"

    def fail_upload(_locator: Locator, _path: Path) -> None:
        raise RuntimeError(f"{prompt} {reference_png} {auth} {dom} {token_url}")

    runtime = _runtime_for_fixture(
        "ready.html",
        flow_generation_page,
        set_input_files=fail_upload,
    )

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot=prompt,
            prompt_sha256=_sha256_text(prompt),
        )

    serialized = json.dumps(raised.value.__dict__, sort_keys=True) + str(raised.value)
    for forbidden in (prompt, str(reference_png), auth, dom, token_url, "PRIVATE_TOKEN_VALUE"):
        assert forbidden not in serialized


def _make_generate_show_generating(page: Page) -> None:
    page.evaluate(
        """() => {
            window.generateClicks = 0;
            document.querySelector('button').addEventListener('click', () => {
                window.generateClicks += 1;
                document.querySelector('main').insertAdjacentHTML(
                    'beforeend',
                    '<output role="status" aria-label="Generating">Generating</output>',
                );
            });
        }"""
    )


def _make_generate_show_completed_result(page: Page) -> None:
    page.evaluate(
        """() => {
            window.generateClicks = 0;
            document.querySelector('button').addEventListener('click', () => {
                window.generateClicks += 1;
                document.querySelector('main').insertAdjacentHTML(
                    'beforeend',
                    '<ul aria-label="Generated candidates">'
                    + '<li role="listitem" data-flow-candidate-id="result-a" data-flow-completion-role="completed"><button>Request 2K</button></li>'
                    + '<li role="listitem" data-flow-candidate-id="result-b" data-flow-completion-role="completed"><button>Request 2K</button></li>'
                    + '</ul>',
                );
            });
        }"""
    )


def test_dispatch_commits_intent_before_exactly_one_click(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_generating(flow_generation_page)
    checkpoint_sink = _CheckpointSink(flow_generation_page)

    runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert checkpoint_sink.events == [
        "inputs_verified",
        "dispatch_intent_recorded",
        "dispatch_confirmed",
    ]
    assert checkpoint_sink.click_counts == [0, 0, 1]
    assert flow_generation_page.evaluate("window.generateClicks") == 1


@pytest.mark.parametrize("crash_point", ("after_intent", "during_click", "before_confirmation"))
def test_post_intent_failure_is_ambiguous_and_never_clicks_twice(
    flow_generation_page: Page,
    reference_png: Path,
    crash_point: str,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_generating(flow_generation_page)
    checkpoint_sink = _CheckpointSink(flow_generation_page)
    runtime.inject_crash(crash_point)

    with pytest.raises(FlowDispatchAmbiguousError):
        runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert flow_generation_page.evaluate("window.generateClicks || 0") <= 1
    reconciled = runtime.reconcile(_workspace(), checkpoint_sink)
    assert reconciled is False
    assert flow_generation_page.evaluate("window.generateClicks || 0") <= 1


def test_dispatch_confirms_attributable_completed_result_transition(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_completed_result(flow_generation_page)
    checkpoint_sink = _CheckpointSink(flow_generation_page)

    runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert checkpoint_sink.events[-1] == "dispatch_confirmed"
    assert flow_generation_page.evaluate("window.generateClicks") == 1


@pytest.mark.parametrize("post_click_state", ("ready", "empty_grid"))
def test_dispatch_rejects_nonpositive_click_return_confirmation(
    flow_generation_page: Page,
    reference_png: Path,
    post_click_state: str,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    flow_generation_page.evaluate(
        """state => {
            window.generateClicks = 0;
            document.querySelector('button').addEventListener('click', () => {
                window.generateClicks += 1;
                if (state === 'empty_grid') {
                    document.querySelector('main').insertAdjacentHTML(
                        'beforeend', '<ul aria-label="Generated candidates"></ul>',
                    );
                }
            });
        }""",
        post_click_state,
    )
    checkpoint_sink = _CheckpointSink(flow_generation_page)

    with pytest.raises(FlowDispatchAmbiguousError):
        runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert flow_generation_page.evaluate("window.generateClicks") == 1
    assert checkpoint_sink.events == ["inputs_verified", "dispatch_intent_recorded"]


def test_dispatch_rejects_workspace_identity_mismatch_before_click(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_generating(flow_generation_page)
    request = _prepared_request(reference_png)
    wrong_workspace = FlowWorkspaceIdentity(
        workspace_path="fx/tools/flow/other-workspace",
        fingerprint=_sha256_text("other-workspace"),
    )
    checkpoint_sink = _CheckpointSink(flow_generation_page)

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_and_dispatch(
            FlowGenerationRequest(
                reference_path=request.reference_path,
                reference_sha256=request.reference_sha256,
                prompt_snapshot=request.prompt_snapshot,
                prompt_sha256=request.prompt_sha256,
                workspace=wrong_workspace,
            ),
            checkpoint_sink,
        )

    assert raised.value.failed_step == "open_workspace"
    assert flow_generation_page.evaluate("window.generateClicks || 0") == 0
    assert checkpoint_sink.events == []


def test_dispatch_revalidates_current_route_before_workspace_bound_click(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    class RedirectingSink(_CheckpointSink):
        def record_inputs_verified(self, observation: FlowGenerationObservation) -> None:
            super().record_inputs_verified(observation)
            flow_generation_page.goto("data:text/html,redirected")

    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_generating(flow_generation_page)
    checkpoint_sink = RedirectingSink(flow_generation_page)

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert raised.value.failed_step == "open_workspace"
    assert flow_generation_page.evaluate("window.generateClicks || 0") == 0


def test_route_change_after_intent_is_ambiguous_without_click(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    class IntentRedirectingSink(_CheckpointSink):
        def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None:
            super().record_dispatch_intent(workspace)
            flow_generation_page.goto("data:text/html,redirected")

    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_generating(flow_generation_page)

    with pytest.raises(FlowDispatchAmbiguousError):
        runtime.prepare_and_dispatch(_prepared_request(reference_png), IntentRedirectingSink(flow_generation_page))

    assert flow_generation_page.evaluate("window.generateClicks || 0") == 0


def test_reconcile_never_confirms_caller_supplied_or_new_result_evidence(
    flow_generation_page: Page,
) -> None:
    runtime = _runtime_for_fixture(
        "grid-two.html",
        flow_generation_page,
        upload_completes=False,
        generation_timeout_seconds=0,
    )
    checkpoint_sink = _CheckpointSink(flow_generation_page)
    assert runtime.reconcile(_workspace(), checkpoint_sink) is False
    flow_generation_page.evaluate("document.querySelector('li').remove()")
    assert runtime.reconcile(_workspace(), checkpoint_sink) is False
    flow_generation_page.evaluate(
        """document.querySelector('ul').insertAdjacentHTML(
            'beforeend',
            '<li role="listitem" data-flow-candidate-id="candidate-c" data-flow-completion-role="completed"><button>Request 2K</button></li>',
        )"""
    )

    assert runtime.reconcile(_workspace(), checkpoint_sink) is False
    assert "prior_result_fingerprints" not in inspect.signature(runtime.reconcile).parameters
    with pytest.raises(TypeError):
        runtime.reconcile(  # type: ignore[call-arg]
            _workspace(), checkpoint_sink, prior_result_fingerprints=frozenset({"0" * 64})
        )
    assert checkpoint_sink.events == []


def test_reconcile_rejects_preexisting_generating_state_without_attempt_evidence(
    flow_generation_page: Page,
) -> None:
    runtime = _runtime_for_fixture(
        "generating.html",
        flow_generation_page,
        upload_completes=False,
        generation_timeout_seconds=0,
    )
    checkpoint_sink = _CheckpointSink(flow_generation_page)

    assert runtime.reconcile(_workspace(), checkpoint_sink) is False
    assert checkpoint_sink.events == []


def test_prepare_rejects_stale_upload_completion_and_waits_for_new_completion(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    stale_runtime = _runtime_for_fixture(
        "ready.html", flow_generation_page, upload_completes=False, generation_timeout_seconds=0
    )
    flow_generation_page.evaluate(
        "document.querySelector('main').insertAdjacentHTML('beforeend', '<output role=\"status\" aria-label=\"Reference upload complete\">old</output>')"
    )
    with pytest.raises(FlowGenerationRuntimeError) as stale:
        stale_runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )
    assert stale.value.failed_step == "verify_reference"

    delayed_runtime = _runtime_for_fixture(
        "ready.html", flow_generation_page, upload_completes=False
    )
    flow_generation_page.locator('input[type="file"]').evaluate(
        """element => element.addEventListener('change', () => setTimeout(() => {
            document.querySelector('main').insertAdjacentHTML(
                'beforeend', '<output role="status" aria-label="Reference upload complete">new</output>',
            );
        }, 100))"""
    )
    observed = delayed_runtime.prepare_inputs(
        reference_path=reference_png,
        reference_sha256=_sha256(reference_png),
        prompt_snapshot="private prompt",
        prompt_sha256=_sha256_text("private prompt"),
    )
    assert observed.reference_verified is True


@pytest.mark.parametrize("after_intent", (False, True))
def test_session_close_failure_is_sanitized_or_ambiguous_by_dispatch_boundary(
    flow_generation_page: Page,
    reference_png: Path,
    after_intent: bool,
) -> None:
    runtime = _runtime_for_fixture(
        "ready.html",
        flow_generation_page,
        close_error=RuntimeError("PRIVATE_CLOSE_TOKEN https://private.invalid/?token=secret"),
    )
    if after_intent:
        _make_generate_show_generating(flow_generation_page)
        with pytest.raises(FlowDispatchAmbiguousError):
            runtime.prepare_and_dispatch(_prepared_request(reference_png), _CheckpointSink(flow_generation_page))
        assert flow_generation_page.evaluate("window.generateClicks || 0") <= 1
    else:
        with pytest.raises(FlowGenerationRuntimeError) as raised:
            runtime.prepare_inputs(
                reference_path=reference_png,
                reference_sha256=_sha256(reference_png),
                prompt_snapshot="PRIVATE_PROMPT",
                prompt_sha256=_sha256_text("PRIVATE_PROMPT"),
            )
        assert raised.value.failed_step == "open_workspace"
        assert "PRIVATE_CLOSE_TOKEN" not in str(raised.value)


def test_preintent_session_factory_error_is_sanitized(
    reference_png: Path,
) -> None:
    def fail_factory() -> AbstractContextManager[_LocalAuthenticatedSession]:
        raise RuntimeError(f"PRIVATE_FACTORY {reference_png} https://private.invalid/?token=secret")

    runtime = FlowGenerationRuntime(
        FlowGenerationConfig(generation_timeout_seconds=1, download_timeout_seconds=1),
        _session_factory=fail_factory,
    )
    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="PRIVATE_PROMPT",
            prompt_sha256=_sha256_text("PRIVATE_PROMPT"),
        )
    assert raised.value.failed_step == "open_workspace"
    assert "PRIVATE_FACTORY" not in str(raised.value)


@pytest.mark.parametrize("failure_point", ("open", "route"))
def test_preintent_session_open_and_route_errors_are_sanitized(
    reference_png: Path,
    failure_point: str,
) -> None:
    private = f"PRIVATE_{failure_point.upper()} {reference_png} https://private.invalid/?token=secret"

    class FailingSession:
        @property
        def page(self) -> Page:
            raise RuntimeError(private)

        def require_current_flow_page(self) -> None:
            raise RuntimeError(private)

        def workspace_identity(self) -> FlowWorkspaceIdentity:
            raise RuntimeError(private)

    class FailingManager:
        def __enter__(self) -> FailingSession:
            if failure_point == "open":
                raise RuntimeError(private)
            return FailingSession()

        def __exit__(self, *_args: object) -> bool:
            return False

    runtime = FlowGenerationRuntime(
        FlowGenerationConfig(generation_timeout_seconds=1, download_timeout_seconds=1),
        _session_factory=lambda: FailingManager(),
    )
    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="PRIVATE_PROMPT",
            prompt_sha256=_sha256_text("PRIVATE_PROMPT"),
        )
    assert raised.value.failed_step == "open_workspace"
    assert private not in str(raised.value)


def test_generation_error_facts_are_read_only_and_context_transportable() -> None:
    error = FlowGenerationRuntimeError(failed_step="verify_prompt")
    with pytest.raises(AttributeError):
        error.failed_step = "upload_reference"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        error._failed_step = "upload_reference"  # type: ignore[misc]
    error.__dict__["_failed_step"] = "upload_reference"
    error.__dict__["_failed_locator"] = "REFERENCE_INPUT"
    assert error.failed_step == "verify_prompt"
    assert error.failed_locator is None

    @contextmanager
    def transport() -> Iterator[None]:
        yield
        raise error

    with pytest.raises(FlowGenerationRuntimeError) as caught:
        with transport():
            pass
    assert caught.value.failed_step == "verify_prompt"


def test_production_session_holds_goal_4b_lock_through_session_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class FakeLock:
        def __init__(self, _path: Path) -> None:
            pass

        def acquire(self) -> None:
            events.append("lock_acquired")

        def release(self) -> None:
            events.append("lock_released")

    class FakeSession:
        def __enter__(self) -> "FakeSession":
            events.append("session_opened")
            return self

        def open_workspace(self, workspace: FlowWorkspaceIdentity) -> None:
            assert workspace == _workspace()
            events.append("workspace_opened")

        def __exit__(self, *_args: object) -> bool:
            events.append("session_closed")
            return False

    runtime_config = FlowRuntimeConfig(
        profile_dir=tmp_path / "profile",
        diagnostics_dir=tmp_path / "diagnostics",
        lock_path=tmp_path / "flow.lock",
        staging_root=tmp_path / "staging",
        login_timeout_seconds=1,
        navigation_timeout_seconds=1,
    )
    monkeypatch.setattr(generation_module, "BrowserRuntimeLock", FakeLock)
    monkeypatch.setattr(generation_module, "FlowBrowserSession", lambda _config: FakeSession())
    runtime = FlowGenerationRuntime(
        FlowGenerationConfig(generation_timeout_seconds=1, download_timeout_seconds=1),
        runtime_config=runtime_config,
    )

    with runtime._open_authenticated_session(workspace=_workspace()):
        assert events == ["lock_acquired", "session_opened", "workspace_opened"]

    assert events == [
        "lock_acquired",
        "session_opened",
        "workspace_opened",
        "session_closed",
        "lock_released",
    ]

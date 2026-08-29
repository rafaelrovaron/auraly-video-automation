"""Deterministic local-browser coverage for authenticated Flow generation dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
import hashlib
import json
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright
import pytest

from auraly_pipeline.flow.config import FlowGenerationConfig
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
from auraly_pipeline.flow.generation_locators import _GenerationLocatorTarget, _local_test_target


FLOW_GENERATION_ROOT = Path(__file__).parent / "fakes" / "flow-generation"


def _fixture_url(name: str) -> str:
    return (FLOW_GENERATION_ROOT / name).resolve(strict=True).as_uri()


LOCAL_TARGET = _local_test_target(
    *(_fixture_url(name) for name in ("ready.html", "upload-complete.html"))
)


class _LocalAuthenticatedSession:
    def __init__(self, page: Page, target: _GenerationLocatorTarget) -> None:
        self.page = page
        self._target = target

    def require_current_flow_page(self) -> None:
        if not self._target.allows_url(self.page.url):
            raise FlowGenerationRuntimeError(failed_step="open_workspace")


@contextmanager
def _session(page: Page) -> Iterator[_LocalAuthenticatedSession]:
    yield _LocalAuthenticatedSession(page, LOCAL_TARGET)


def _runtime_for_fixture(
    fixture: str,
    page: Page,
    *,
    upload_completes: bool = True,
    set_input_files: Callable[[Locator, Path], None] | None = None,
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
        return _session(page)

    return FlowGenerationRuntime(
        FlowGenerationConfig(
            generation_timeout_seconds=1,
            download_timeout_seconds=1,
        ),
        session_factory=session_factory,
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
    assert reconciled is (crash_point != "after_intent")
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

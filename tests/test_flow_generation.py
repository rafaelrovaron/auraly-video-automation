"""Deterministic local-browser coverage for authenticated Flow generation dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import base64
from contextlib import AbstractContextManager, contextmanager
from io import BytesIO
import hashlib
import inspect
import json
from pathlib import Path
from typing import Literal

from PIL import Image
from playwright.sync_api import Locator, Page, sync_playwright
import pytest

from auraly_pipeline.flow.artifacts import (
    FlowArtifactInvalidError,
    FlowReferenceUpload,
    capture_flow_reference,
)
from auraly_pipeline.flow.config import FlowGenerationConfig
from auraly_pipeline.flow.config import FlowRuntimeConfig
from auraly_pipeline.flow.domain import FlowUnexpectedStateError
from auraly_pipeline.flow import generation as generation_module
from auraly_pipeline.flow.generation import (
    FlowGenerationCheckpointSink,
    FlowGenerationRequest,
    FlowGenerationRuntime,
)
from auraly_pipeline.flow.generation_domain import (
    FlowCandidateObservation,
    FlowDispatchAmbiguousError,
    FlowDownloadCorrelationError,
    FlowGenerationObservation,
    FlowGenerationRuntimeError,
    FlowGenerationUiContractError,
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
    set_input_files: Callable[[Locator, FlowReferenceUpload], None] | None = None,
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
        reference=capture_flow_reference(reference_png, _sha256(reference_png)),
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


class _Task9CheckpointSink(_CheckpointSink):
    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self.bound_slots: dict[int, str] = {}
        self._slot_events: dict[int, list[str]] = {0: [], 1: []}
        self._slot_states: dict[int, str] = {0: "pending", 1: "pending"}
        self.downloaded_paths: dict[int, str] = {}
        self.grid_evidence: object | None = None
        self.run_state = "dispatch_confirmed"

    def bind_candidate_slot(self, slot_index: int, observation: FlowCandidateObservation) -> None:
        assert slot_index == observation.semantic_order
        existing = self.bound_slots.get(slot_index)
        assert existing in {None, observation.fingerprint}
        self.bound_slots[slot_index] = observation.fingerprint
        self._slot_states[slot_index] = "observed"

    def record_candidates_observed(self, evidence: object) -> None:
        self.grid_evidence = evidence
        self.run_state = "candidates_observed"

    def candidate_fingerprint(self, slot_index: int) -> str:
        return self.bound_slots[slot_index]

    def record_download_intent(self, slot_index: int, fingerprint: str) -> None:
        assert self.bound_slots[slot_index] == fingerprint
        self._slot_events[slot_index].append("download_intent_recorded")
        self._slot_states[slot_index] = "download_intent_recorded"

    def record_downloaded(
        self,
        slot_index: int,
        *,
        relative_path: str,
        sha256: str,
    ) -> None:
        assert len(sha256) == 64
        self._slot_events[slot_index].append("downloaded")
        self._slot_states[slot_index] = "downloaded"
        self.downloaded_paths[slot_index] = relative_path

    def slot_events(self, slot_index: int) -> list[str]:
        return list(self._slot_events[slot_index])

    def slot_state(self, slot_index: int) -> str:
        return self._slot_states[slot_index]


TASK9_SCENE_ID = "11111111-1111-4111-8111-111111111111"


def _task9_runtime(
    fixture: str,
    page: Page,
    work_root: Path,
    *,
    generation_timeout_seconds: int = 1,
) -> FlowGenerationRuntime:
    page.goto(_fixture_url(fixture))
    context_type = getattr(generation_module, "FlowGenerationArtifactContext")
    context = context_type(
        campaign_id="campaign-1",
        scene_variant_id=TASK9_SCENE_ID,
        generation_number=1,
        work_root=work_root,
        workspace=_workspace(),
    )

    def session_factory() -> AbstractContextManager[_LocalAuthenticatedSession]:
        return _session(page)

    return FlowGenerationRuntime(
        FlowGenerationConfig(
            generation_timeout_seconds=generation_timeout_seconds,
            download_timeout_seconds=1,
        ),
        _session_factory=session_factory,
        _locator_target=LOCAL_TARGET,
        artifact_context=context,
    )


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
    Image.new("RGB", (4, 4), color=(16, 32, 64)).save(path, format="PNG")
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
    def upload_then_redirect(locator: Locator, reference: FlowReferenceUpload) -> None:
        generation_module._playwright_set_input_files(locator, reference)
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

    def fail_upload(_locator: Locator, _reference: FlowReferenceUpload) -> None:
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


@pytest.mark.parametrize("interruption", (KeyboardInterrupt(), SystemExit(9)))
def test_prepare_does_not_translate_process_interruptions(
    flow_generation_page: Page,
    reference_png: Path,
    interruption: BaseException,
) -> None:
    def interrupt_upload(_locator: Locator, _reference: FlowReferenceUpload) -> None:
        raise interruption

    runtime = _runtime_for_fixture(
        "ready.html",
        flow_generation_page,
        set_input_files=interrupt_upload,
    )

    with pytest.raises(type(interruption)):
        runtime.prepare_inputs(
            reference_path=reference_png,
            reference_sha256=_sha256(reference_png),
            prompt_snapshot="private prompt",
            prompt_sha256=_sha256_text("private prompt"),
        )


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


def _make_generate_without_confirmation(page: Page) -> None:
    page.evaluate(
        """() => {
            window.generateClicks = 0;
            document.querySelector('button').addEventListener('click', () => {
                window.generateClicks += 1;
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


def test_dispatch_resolves_a_fresh_unique_generate_control_after_intent(
    flow_generation_page: Page,
    reference_png: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_generating(flow_generation_page)
    resolved_controls: list[Locator] = []
    intent_returned = False
    original_resolver = generation_module.resolve_generate_control

    def track_resolver(page: Page, *, _target: _GenerationLocatorTarget) -> Locator:
        assert intent_returned is True
        control = original_resolver(page, _target=_target)
        resolved_controls.append(control)
        return control

    monkeypatch.setattr(generation_module, "resolve_generate_control", track_resolver)

    class IntentSink(_CheckpointSink):
        def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None:
            nonlocal intent_returned
            super().record_dispatch_intent(workspace)
            intent_returned = True

    runtime.prepare_and_dispatch(_prepared_request(reference_png), IntentSink(flow_generation_page))

    assert len(resolved_controls) == 1
    assert flow_generation_page.evaluate("window.generateClicks") == 1


@pytest.mark.parametrize(
    "mutation",
    (
        "document.body.insertAdjacentHTML('beforeend', '<dialog open aria-label=\"Blocking\"></dialog>')",
        "document.querySelector('button').setAttribute('aria-disabled', 'true')",
        "document.querySelector('button').insertAdjacentHTML('afterend', '<button>Generate</button>')",
        "document.querySelector('input[aria-label=\"Prompt\"]').value = 'mutated'",
        "document.querySelector('[aria-label=\"Reference upload complete\"]').remove()",
    ),
)
def test_post_intent_gate_rejects_mutated_dispatch_surface_without_click(
    flow_generation_page: Page,
    reference_png: Path,
    mutation: str,
) -> None:
    class MutatingIntentSink(_CheckpointSink):
        def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None:
            super().record_dispatch_intent(workspace)
            flow_generation_page.evaluate(mutation)

    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    _make_generate_show_generating(flow_generation_page)
    checkpoint_sink = MutatingIntentSink(flow_generation_page)

    with pytest.raises(FlowDispatchAmbiguousError):
        runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert flow_generation_page.evaluate("window.generateClicks || 0") == 0
    assert checkpoint_sink.events == ["inputs_verified", "dispatch_intent_recorded"]


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


@pytest.mark.parametrize(
    ("provider_state", "expected"),
    [("generating", True), ("ready", False)],
)
def test_recovery_dispatch_observation_never_clicks_generate(
    flow_generation_page: Page,
    provider_state: str,
    expected: bool,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    if provider_state == "generating":
        flow_generation_page.locator("main").evaluate(
            "element => element.insertAdjacentHTML("
            "'beforeend', '<output role=\"status\" aria-label=\"Generating\">Generating</output>')"
        )

    observed = runtime.recover_dispatch(_workspace())

    assert observed is expected
    assert flow_generation_page.evaluate("window.generateClicks || 0") == 0


def test_recovery_dispatch_requires_exact_persisted_candidate_fingerprints(
    flow_generation_page: Page,
) -> None:
    flow_generation_page.goto(_fixture_url("grid-two.html"))

    def session_factory() -> AbstractContextManager[_LocalAuthenticatedSession]:
        return _session(flow_generation_page)

    runtime = FlowGenerationRuntime(
        FlowGenerationConfig(generation_timeout_seconds=1, download_timeout_seconds=1),
        _session_factory=session_factory,
        _locator_target=LOCAL_TARGET,
    )
    observed = generation_module.observe_completed_candidate_slots(
        flow_generation_page,
        _target=LOCAL_TARGET,
    )
    expected = tuple(item.fingerprint for item in observed[:2])

    assert runtime.recover_dispatch(_workspace(), expected_fingerprints=expected) is True
    assert (
        runtime.recover_dispatch(
            _workspace(),
            expected_fingerprints=("0" * 64, expected[1]),
        )
        is False
    )
    assert flow_generation_page.evaluate("window.generateClicks || 0") == 0


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


def test_dispatch_requires_a_trustworthy_result_baseline_before_attribution(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    """Collapsing an invalid baseline to empty would attribute a pre-existing result to this click."""
    class RepairingIntentSink(_CheckpointSink):
        def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None:
            super().record_dispatch_intent(workspace)
            flow_generation_page.evaluate("document.querySelectorAll('li')[1].remove()")

    runtime = _runtime_for_fixture("ready.html", flow_generation_page, generation_timeout_seconds=0)
    flow_generation_page.evaluate(
        """document.querySelector('main').insertAdjacentHTML(
            'beforeend',
            '<ul aria-label="Generated candidates">'
            + '<li role="listitem" data-flow-candidate-id="preexisting" data-flow-completion-role="completed"><button>Request 2K</button></li>'
            + '<li role="listitem" data-flow-candidate-id="preexisting" data-flow-completion-role="completed"><button>Request 2K</button></li>'
            + '</ul>',
        )"""
    )
    _make_generate_without_confirmation(flow_generation_page)
    checkpoint_sink = RepairingIntentSink(flow_generation_page)

    with pytest.raises(FlowGenerationUiContractError) as raised:
        runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert raised.value.failed_step == "observe_candidates"
    assert flow_generation_page.evaluate("window.generateClicks || 0") == 0
    assert checkpoint_sink.events == ["inputs_verified"]


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
                reference=request.reference,
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


def test_close_failure_before_confirmation_is_ambiguous_after_intent(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture(
        "ready.html",
        flow_generation_page,
        close_error=FlowUnexpectedStateError(failed_step="close_browser"),
        generation_timeout_seconds=0,
    )
    checkpoint_sink = _CheckpointSink(flow_generation_page)
    _make_generate_without_confirmation(flow_generation_page)

    with pytest.raises(FlowDispatchAmbiguousError):
        runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert flow_generation_page.evaluate("window.generateClicks || 0") == 1
    assert checkpoint_sink.events == ["inputs_verified", "dispatch_intent_recorded"]


def test_close_failure_after_durable_confirmation_preserves_close_browser(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture(
        "ready.html",
        flow_generation_page,
        close_error=FlowUnexpectedStateError(failed_step="close_browser"),
    )
    _make_generate_show_generating(flow_generation_page)
    checkpoint_sink = _CheckpointSink(flow_generation_page)

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.prepare_and_dispatch(_prepared_request(reference_png), checkpoint_sink)

    assert raised.value.failed_step == "close_browser"
    assert flow_generation_page.evaluate("window.generateClicks || 0") == 1
    assert checkpoint_sink.events == [
        "inputs_verified",
        "dispatch_intent_recorded",
        "dispatch_confirmed",
    ]


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

        def __exit__(self, *_args: object) -> Literal[False]:
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

        def __exit__(self, *_args: object) -> Literal[False]:
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


def test_observe_binds_first_two_validated_semantic_slots(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)

    observations = runtime.observe_candidates(checkpoint_sink)

    assert [item.semantic_order for item in observations] == [0, 1]
    assert len({item.fingerprint for item in observations}) == 2
    assert checkpoint_sink.bound_slots == {
        0: observations[0].fingerprint,
        1: observations[1].fingerprint,
    }
    assert checkpoint_sink.run_state == "candidates_observed"


def test_grid_evidence_masks_input_and_identity_regions(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)

    published = runtime.capture_grid_evidence()

    payload = (tmp_path / published.relative_path).read_bytes()
    assert published.sha256 == hashlib.sha256(payload).hexdigest()
    assert b"PRIVATE PROMPT" not in payload
    assert b"person@example.com" not in payload
    assert b"reference-secret.png" not in payload
    with Image.open(BytesIO(payload)) as image:
        assert (255, 0, 255) in set(image.convert("RGB").getdata())


@pytest.mark.parametrize("completed_count", [0, 1])
def test_candidate_observation_times_out_without_two_completed_slots(
    flow_generation_page: Page,
    tmp_path: Path,
    completed_count: int,
) -> None:
    runtime = _task9_runtime(
        "grid-two.html",
        flow_generation_page,
        tmp_path,
        generation_timeout_seconds=0,
    )
    flow_generation_page.evaluate(
        "count => Array.from(document.querySelectorAll('[data-flow-candidate-id]')).slice(count).forEach(item => item.remove())",
        completed_count,
    )
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)

    with pytest.raises(FlowGenerationUiContractError) as raised:
        runtime.observe_candidates(checkpoint_sink)

    assert raised.value.failed_step == "observe_candidates"
    assert checkpoint_sink.bound_slots == {}


def test_candidate_observation_rejects_duplicate_fingerprints(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime(
        "grid-two.html", flow_generation_page, tmp_path, generation_timeout_seconds=0
    )
    flow_generation_page.locator("[data-flow-candidate-id='candidate-b']").evaluate(
        "item => item.dataset.flowCandidateId = 'candidate-a'"
    )
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)

    with pytest.raises(FlowGenerationUiContractError):
        runtime.observe_candidates(checkpoint_sink)

    assert checkpoint_sink.bound_slots == {}


def test_candidate_change_between_observation_and_evidence_fails_closed(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)

    class MutatingSink(_Task9CheckpointSink):
        def bind_candidate_slot(
            self, slot_index: int, observation: FlowCandidateObservation
        ) -> None:
            super().bind_candidate_slot(slot_index, observation)
            if slot_index == 1:
                flow_generation_page.locator("[data-flow-candidate-id='candidate-b']").evaluate(
                    "item => item.dataset.flowCandidateId = 'candidate-changed'"
                )

    checkpoint_sink = MutatingSink(flow_generation_page)

    with pytest.raises(FlowGenerationUiContractError) as raised:
        runtime.observe_candidates(checkpoint_sink)

    assert raised.value.failed_step == "capture_grid_evidence"
    assert set(checkpoint_sink.bound_slots) == {0, 1}
    assert checkpoint_sink.run_state == "dispatch_confirmed"


def test_missing_required_mask_fails_before_evidence_publication(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    flow_generation_page.get_by_label("Account identity", exact=True).evaluate(
        "item => item.remove()"
    )
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.observe_candidates(checkpoint_sink)

    assert raised.value.failed_step == "capture_grid_evidence"
    assert not list(tmp_path.rglob("grid.png"))
    assert checkpoint_sink.run_state == "dispatch_confirmed"


def test_grid_sanitizer_failure_does_not_advance_observation_checkpoint(
    flow_generation_page: Page,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)

    def reject_grid(*_args: object, **_kwargs: object) -> object:
        from auraly_pipeline.flow.domain import FlowDiagnosticSanitizationError

        raise FlowDiagnosticSanitizationError()

    monkeypatch.setattr(generation_module, "publish_flow_grid_evidence", reject_grid)

    from auraly_pipeline.flow.domain import FlowDiagnosticSanitizationError

    with pytest.raises(FlowDiagnosticSanitizationError):
        runtime.observe_candidates(checkpoint_sink)

    assert checkpoint_sink.run_state == "dispatch_confirmed"


def test_route_change_before_grid_evidence_preserves_only_bound_slots(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)

    class RedirectingSink(_Task9CheckpointSink):
        def bind_candidate_slot(
            self, slot_index: int, observation: FlowCandidateObservation
        ) -> None:
            super().bind_candidate_slot(slot_index, observation)
            if slot_index == 1:
                flow_generation_page.goto("data:text/html,redirected")

    checkpoint_sink = RedirectingSink(flow_generation_page)

    with pytest.raises(FlowGenerationRuntimeError) as raised:
        runtime.observe_candidates(checkpoint_sink)

    assert raised.value.failed_step == "open_workspace"
    assert checkpoint_sink.run_state == "dispatch_confirmed"


@pytest.mark.parametrize("slot_index", [0, 1])
def test_download_records_intent_before_exact_2k_action(
    flow_generation_page: Page,
    tmp_path: Path,
    slot_index: int,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)
    runtime.observe_candidates(checkpoint_sink)

    artifact = runtime.download_slot(slot_index, checkpoint_sink)

    assert checkpoint_sink.slot_events(slot_index) == [
        "download_intent_recorded",
        "downloaded",
    ]
    assert runtime.download_actions == [(slot_index, "2K")]
    assert max(artifact.width, artifact.height) >= 2048
    assert flow_generation_page.evaluate("window.flowDownloadActions") == [
        f"candidate-{'a' if slot_index == 0 else 'b'}"
    ]


def test_unrelated_download_event_cannot_satisfy_slot_when_selected_action_emits_none(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)
    runtime.observe_candidates(checkpoint_sink)
    selected_action = flow_generation_page.locator(
        "[data-flow-candidate-id='candidate-a'] button"
    )
    selected_action.evaluate("button => button.dataset.downloadDisabled = 'true'")
    flow_generation_page.get_by_role("button", name="Unrelated download", exact=True).click()

    with pytest.raises(FlowDownloadCorrelationError):
        runtime.download_slot(0, checkpoint_sink)

    assert checkpoint_sink.slot_state(0) == "download_intent_recorded"
    assert checkpoint_sink.slot_state(1) == "observed"
    assert not list(tmp_path.rglob("candidate-0000.png"))
    assert flow_generation_page.evaluate("window.flowDownloadActions") == ["unrelated"]


@pytest.mark.parametrize("event_case", ["none", "two", "cancelled"])
def test_download_requires_one_successful_event_from_the_exact_action(
    flow_generation_page: Page,
    tmp_path: Path,
    event_case: str,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)
    runtime.observe_candidates(checkpoint_sink)
    action = flow_generation_page.locator("[data-flow-candidate-id='candidate-a'] button")
    if event_case == "none":
        action.evaluate("button => button.dataset.downloadDisabled = 'true'")
    if event_case == "two":
        action.evaluate("button => button.dataset.downloadCount = '2'")

    def cancel(download: object) -> None:
        getattr(download, "cancel")()

    if event_case == "cancelled":
        flow_generation_page.on("download", cancel)
    try:
        with pytest.raises(FlowDownloadCorrelationError):
            runtime.download_slot(0, checkpoint_sink)
    finally:
        if event_case == "cancelled":
            flow_generation_page.remove_listener("download", cancel)

    assert checkpoint_sink.slot_state(0) == "download_intent_recorded"
    assert checkpoint_sink.slot_state(1) == "observed"


@pytest.mark.parametrize("artifact_case", ["partial", "1k"])
def test_invalid_download_bytes_never_reach_downloaded_checkpoint(
    flow_generation_page: Page,
    tmp_path: Path,
    artifact_case: str,
) -> None:
    invalid_path = tmp_path / f"{artifact_case}.png"
    if artifact_case == "partial":
        invalid_path.write_bytes(b"\x89PNG\r\n\x1a\npartial")
    else:
        Image.new("RGBA", (1024, 1), (1, 2, 3, 255)).save(invalid_path)
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)
    runtime.observe_candidates(checkpoint_sink)
    flow_generation_page.locator("[data-flow-candidate-id='candidate-a'] button").evaluate(
        "(button, payload) => button.dataset.downloadBytes = payload",
        base64.b64encode(invalid_path.read_bytes()).decode("ascii"),
    )

    with pytest.raises(FlowArtifactInvalidError):
        runtime.download_slot(0, checkpoint_sink)

    assert checkpoint_sink.slot_state(0) == "download_intent_recorded"


def test_changed_download_slot_fingerprint_never_records_intent_or_uses_another_slot(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)
    runtime.observe_candidates(checkpoint_sink)
    flow_generation_page.locator("[data-flow-candidate-id='candidate-a']").evaluate(
        "item => item.dataset.flowCandidateId = 'candidate-changed'"
    )

    with pytest.raises(FlowDownloadCorrelationError):
        runtime.download_slot(0, checkpoint_sink)

    assert checkpoint_sink.slot_state(0) == "observed"
    assert checkpoint_sink.slot_state(1) == "observed"
    assert flow_generation_page.evaluate("window.flowDownloadActions") == []


@pytest.mark.parametrize(
    ("crash_point", "durable_state", "final_visible"),
    (
        ("after_download_event", "download_intent_recorded", False),
        ("after_download_save", "download_intent_recorded", False),
        ("after_download_checkpoint", "downloaded", False),
        ("after_download_publication", "downloaded", True),
    ),
)
def test_download_crash_boundaries_preserve_last_durable_slot_state(
    flow_generation_page: Page,
    tmp_path: Path,
    crash_point: str,
    durable_state: str,
    final_visible: bool,
) -> None:
    runtime = _task9_runtime("grid-two.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)
    runtime.observe_candidates(checkpoint_sink)
    runtime.inject_crash(crash_point)

    with pytest.raises(FlowDownloadCorrelationError):
        runtime.download_slot(0, checkpoint_sink)

    assert checkpoint_sink.slot_state(0) == durable_state
    assert checkpoint_sink.slot_state(1) == "observed"
    assert flow_generation_page.evaluate("window.flowDownloadActions") == ["candidate-a"]
    final_files = list(tmp_path.rglob("candidate-0000.png"))
    assert bool(final_files) is final_visible


def test_observe_and_download_ignores_third_slot_without_interaction(
    flow_generation_page: Page,
    tmp_path: Path,
) -> None:
    runtime = _task9_runtime("grid-three.html", flow_generation_page, tmp_path)
    checkpoint_sink = _Task9CheckpointSink(flow_generation_page)

    artifacts = runtime.observe_and_download(checkpoint_sink)

    assert len(artifacts) == 2
    assert set(checkpoint_sink.bound_slots) == {0, 1}
    assert flow_generation_page.evaluate("window.flowDownloadActions") == [
        "candidate-a",
        "candidate-b",
    ]

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from playwright.sync_api import Locator, Page, sync_playwright
import pytest

from auraly_pipeline.flow.generation_domain import (
    FlowCandidateObservation,
    FlowGenerationUiContractError,
)
from auraly_pipeline.flow import generation_locators as locator_module
from auraly_pipeline.flow.generation_locators import (
    _GenerationLocatorTarget,
    _PRODUCTION_GENERATION_TARGET,
    _local_test_target,
    observe_completed_candidate_slots,
    resolve_candidate_2k_action,
    resolve_generate_control,
    resolve_generation_prompt,
    resolve_generating_indicator,
    resolve_reference_input,
    resolve_upload_complete,
)


FLOW_GENERATION_ROOT = Path(__file__).parent / "fakes" / "flow-generation"
FLOW_GENERATION_FIXTURES = (
    "ready.html",
    "upload-complete.html",
    "generating.html",
    "grid-two.html",
    "grid-three.html",
    "ambiguous-grid.html",
    "missing-2k.html",
    "loading-grid.html",
    "failed-grid.html",
    "production-safe-grid.html",
    "live-preflight.html",
)


def fake_generation_url(fixture: str) -> str:
    """Return the private deterministic local-page target for Flow generation tests."""
    return (FLOW_GENERATION_ROOT / fixture).resolve(strict=True).as_uri()


LOCAL_FLOW_TARGET = _local_test_target(
    *(fake_generation_url(fixture) for fixture in FLOW_GENERATION_FIXTURES)
)


class _ScalarResolver(Protocol):
    """One semantic control resolver with the private deterministic-target seam."""

    def __call__(self, page: Page, *, _target: _GenerationLocatorTarget) -> Locator: ...


class _CandidateObserver(Protocol):
    """The candidate observation resolver with the private deterministic-target seam."""

    def __call__(
        self,
        page: Page,
        *,
        _target: _GenerationLocatorTarget,
    ) -> tuple[FlowCandidateObservation, ...]: ...


class _CandidateActionResolver(Protocol):
    """The exact candidate-action resolver with the private deterministic-target seam."""

    def __call__(
        self,
        page: Page,
        fingerprint: str,
        *,
        _target: _GenerationLocatorTarget,
    ) -> Locator: ...


def resolve_local_locator(resolver: _ScalarResolver, page: Page) -> Locator:
    """Resolve one scalar control through the exact local-only target policy."""
    return resolver(page, _target=LOCAL_FLOW_TARGET)


def observe_local_candidates(page: Page) -> tuple[FlowCandidateObservation, ...]:
    """Observe local fixture candidates through the exact local-only target policy."""
    observer: _CandidateObserver = observe_completed_candidate_slots
    return observer(page, _target=LOCAL_FLOW_TARGET)


def resolve_local_candidate_2k(page: Page, fingerprint: str) -> Locator:
    """Resolve the exact local fixture 2K action for one observed candidate."""
    resolver: _CandidateActionResolver = resolve_candidate_2k_action
    return resolver(page, fingerprint, _target=LOCAL_FLOW_TARGET)


@pytest.fixture(scope="module", name="flow_generation_page")
def provide_flow_generation_page() -> Iterator[Page]:
    """Provide a managed headed Chromium page without a network-capable provider target."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_ready_page_resolves_exact_generation_controls(flow_generation_page: Page) -> None:
    """A missing or duplicate input/control would make provider mutation ambiguous."""
    flow_generation_page.goto(fake_generation_url("ready.html"))

    assert resolve_local_locator(resolve_reference_input, flow_generation_page).count() == 1
    assert resolve_local_locator(resolve_generation_prompt, flow_generation_page).count() == 1
    assert resolve_local_locator(resolve_generate_control, flow_generation_page).count() == 1


def test_live_upload_menu_button_and_item_resolve_uniquely(
    flow_generation_page: Page,
) -> None:
    """Removing either exact live control must prevent a deterministic filechooser path."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))

    control = locator_module.resolve_reference_upload_control(
        flow_generation_page,
        _target=LOCAL_FLOW_TARGET,
    )
    assert control.kind == "menu"
    assert control.locator.get_attribute("aria-label") == "Menu para adicionar arquivos"
    control.locator.click()
    menu_item = locator_module.resolve_upload_menu_item(
        flow_generation_page,
        _target=LOCAL_FLOW_TARGET,
    )

    assert menu_item.get_by_text("Enviar", exact=True).count() == 1


@pytest.mark.parametrize(
    "mutation",
    (
        "document.querySelector('main').insertAdjacentHTML('beforeend', document.getElementById('upload-menu').outerHTML)",
        "document.getElementById('upload-menu').remove()",
    ),
)
def test_live_upload_menu_button_ambiguity_or_absence_fails_closed(
    flow_generation_page: Page,
    mutation: str,
) -> None:
    """Choosing among zero or multiple upload entry points could upload to the wrong surface."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))
    flow_generation_page.evaluate(mutation)

    with pytest.raises(FlowGenerationUiContractError):
        locator_module.resolve_reference_upload_control(
            flow_generation_page,
            _target=LOCAL_FLOW_TARGET,
        )


def test_live_upload_menu_item_ambiguity_fails_closed(flow_generation_page: Page) -> None:
    """Multiple Enviar actions must not be reduced to a positional filechooser click."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))
    flow_generation_page.get_by_role(
        "button", name="Menu para adicionar arquivos", exact=True
    ).click()
    flow_generation_page.get_by_role("menu").evaluate(
        "menu => menu.insertAdjacentHTML('beforeend', '<button role=menuitem>Enviar</button>')"
    )

    with pytest.raises(FlowGenerationUiContractError):
        locator_module.resolve_upload_menu_item(
            flow_generation_page,
            _target=LOCAL_FLOW_TARGET,
        )


def test_live_upload_menu_item_requires_one_menu(flow_generation_page: Page) -> None:
    """A second visible menu must prevent a page-global Enviar match from being trusted."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))
    flow_generation_page.get_by_role(
        "button", name="Menu para adicionar arquivos", exact=True
    ).click()
    flow_generation_page.evaluate(
        "document.body.insertAdjacentHTML('beforeend', '<div role=menu><button role=menuitem>Other</button></div>')"
    )

    with pytest.raises(FlowGenerationUiContractError):
        locator_module.resolve_upload_menu_item(
            flow_generation_page,
            _target=LOCAL_FLOW_TARGET,
        )


def test_live_prompt_uses_one_scoped_editor_and_ignores_global_editor(
    flow_generation_page: Page,
) -> None:
    """A global contenteditable must never displace the editor inside the exact Flow host."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))

    prompt = resolve_local_locator(resolve_generation_prompt, flow_generation_page)

    assert prompt.get_attribute("contenteditable") == "true"
    assert prompt.get_attribute("aria-label") is None


@pytest.mark.parametrize(
    "mutation",
    (
        "document.querySelector('main').insertAdjacentHTML('beforeend', '<flow-rich-text-editor><div contenteditable=true></div></flow-rich-text-editor>')",
        "document.querySelector('flow-rich-text-editor').insertAdjacentHTML('beforeend', '<div contenteditable=true></div>')",
    ),
)
def test_live_prompt_rejects_multiple_hosts_or_scoped_editors(
    flow_generation_page: Page,
    mutation: str,
) -> None:
    """An ambiguous host or editor would make prompt entry nondeterministic."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))
    flow_generation_page.evaluate(mutation)

    with pytest.raises(FlowGenerationUiContractError):
        resolve_local_locator(resolve_generation_prompt, flow_generation_page)


def test_live_generate_is_visible_unique_and_may_be_disabled_only_for_preflight(
    flow_generation_page: Page,
) -> None:
    """Preflight observes disabled submit readiness while dispatch still requires enabled state."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))

    preflight = locator_module.resolve_preflight_generate_control(
        flow_generation_page,
        _target=LOCAL_FLOW_TARGET,
    )
    assert preflight.get_attribute("aria-label") == "Iniciar geração"
    assert preflight.is_disabled()
    with pytest.raises(FlowGenerationUiContractError):
        resolve_local_locator(resolve_generate_control, flow_generation_page)

    preflight.evaluate("button => button.disabled = false")
    assert resolve_local_locator(resolve_generate_control, flow_generation_page).count() == 1


def test_live_generate_missing_or_ambiguous_fails_preflight_closed(
    flow_generation_page: Page,
) -> None:
    """Preflight cannot approve an absent or duplicate localized submit control."""
    for mutation in (
        "document.getElementById('generate-live').remove()",
        "document.querySelector('main').insertAdjacentHTML('beforeend', document.getElementById('generate-live').outerHTML)",
    ):
        flow_generation_page.goto(fake_generation_url("live-preflight.html"))
        flow_generation_page.evaluate(mutation)
        with pytest.raises(FlowGenerationUiContractError):
            locator_module.resolve_preflight_generate_control(
                flow_generation_page,
                _target=LOCAL_FLOW_TARGET,
            )


def test_live_pre_generation_surface_does_not_require_post_generation_controls(
    flow_generation_page: Page,
) -> None:
    """Candidate and download controls are invalid before a provider result exists."""
    flow_generation_page.goto(fake_generation_url("live-preflight.html"))

    assert flow_generation_page.get_by_role(
        "list", name="Generated candidates", exact=True
    ).count() == 0
    assert flow_generation_page.get_by_role(
        "button", name="Request 2K", exact=True
    ).count() == 0
    with pytest.raises(FlowGenerationUiContractError):
        observe_local_candidates(flow_generation_page)


def test_upload_complete_and_generating_pages_resolve_exact_state_indicators(
    flow_generation_page: Page,
) -> None:
    """Treating generic page readiness as positive provider state would permit unsafe progress."""
    flow_generation_page.goto(fake_generation_url("upload-complete.html"))
    assert resolve_local_locator(resolve_upload_complete, flow_generation_page).count() == 1

    flow_generation_page.goto(fake_generation_url("generating.html"))
    assert resolve_local_locator(resolve_generating_indicator, flow_generation_page).count() == 1


def test_grid_three_returns_unique_semantic_identities_in_validated_order(
    flow_generation_page: Page,
) -> None:
    """Position-only candidate selection would bind downloads to the wrong provider result."""
    flow_generation_page.goto(fake_generation_url("grid-three.html"))

    observations = observe_local_candidates(flow_generation_page)

    assert [item.semantic_order for item in observations] == [0, 1, 2]
    assert len({item.fingerprint for item in observations}) == 3
    assert all(item.completed for item in observations)


def test_exact_candidate_2k_action_is_bound_by_observed_fingerprint(
    flow_generation_page: Page,
) -> None:
    """Changing the fingerprint match must prevent a 2K action on another candidate."""
    flow_generation_page.goto(fake_generation_url("grid-two.html"))
    (first, second) = observe_local_candidates(flow_generation_page)

    action = resolve_local_candidate_2k(flow_generation_page, second.fingerprint)

    assert action.count() == 1
    assert action.is_visible()
    assert action.is_enabled()
    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize(
    ("fixture", "target_id", "duplicate_html", "resolver"),
    (
        ("ready.html", "reference-image", '<input type="file" aria-label="Reference image">', resolve_reference_input),
        ("ready.html", "generation-prompt", '<textarea aria-label="Prompt"></textarea>', resolve_generation_prompt),
        ("ready.html", "generate", "<button type=\"button\">Generate</button>", resolve_generate_control),
        ("upload-complete.html", "upload-complete", '<output role="status" aria-label="Reference upload complete">Complete</output>', resolve_upload_complete),
        ("generating.html", "generating-indicator", '<output role="status" aria-label="Generating">Generating</output>', resolve_generating_indicator),
    ),
)
def test_every_scalar_locator_rejects_zero_multiple_hidden_disabled_and_blocking_matches(
    flow_generation_page: Page,
    fixture: str,
    target_id: str,
    duplicate_html: str,
    resolver: _ScalarResolver,
) -> None:
    """Allowing a non-unique or unusable UI element would make a browser action non-deterministic."""
    assert callable(resolver)
    for mutation in (
        f"document.getElementById('{target_id}').remove()",
        f"document.body.insertAdjacentHTML('beforeend', '{duplicate_html}')",
        f"document.getElementById('{target_id}').hidden = true",
        f"document.getElementById('{target_id}').setAttribute('aria-disabled', 'true')",
        "document.body.insertAdjacentHTML('beforeend', '<dialog open aria-label=\"Blocking\"></dialog>')",
    ):
        flow_generation_page.goto(fake_generation_url(fixture))
        flow_generation_page.evaluate(mutation)
        with pytest.raises(FlowGenerationUiContractError):
            resolve_local_locator(resolver, flow_generation_page)


def test_duplicate_or_missing_candidate_identity_and_2k_action_fail_closed(
    flow_generation_page: Page,
) -> None:
    """Duplicate provider identities or absent 2K actions must block rather than guess."""
    flow_generation_page.goto(fake_generation_url("ambiguous-grid.html"))
    with pytest.raises(FlowGenerationUiContractError):
        observe_local_candidates(flow_generation_page)

    flow_generation_page.goto(fake_generation_url("missing-2k.html"))
    (candidate,) = observe_local_candidates(flow_generation_page)
    with pytest.raises(FlowGenerationUiContractError):
        resolve_local_candidate_2k(flow_generation_page, candidate.fingerprint)


def test_candidate_grid_rejects_zero_multiple_hidden_disabled_and_blocking_slots(
    flow_generation_page: Page,
) -> None:
    """Accepting a structurally unavailable grid would make candidate binding non-recoverable."""
    for mutation in (
        "document.querySelector('ul').remove()",
        "document.body.insertAdjacentHTML('beforeend', document.querySelector('ul').outerHTML)",
        "document.querySelector('ul').hidden = true",
        "document.querySelector('[role=listitem]').setAttribute('aria-disabled', 'true')",
        "document.body.insertAdjacentHTML('beforeend', '<dialog open aria-label=\"Blocking\"></dialog>')",
    ):
        flow_generation_page.goto(fake_generation_url("grid-two.html"))
        flow_generation_page.evaluate(mutation)
        with pytest.raises(FlowGenerationUiContractError):
            observe_local_candidates(flow_generation_page)


def test_candidate_2k_action_rejects_zero_multiple_hidden_disabled_and_blocking_matches(
    flow_generation_page: Page,
) -> None:
    """A non-unique 2K action must not be allowed to select an arbitrary provider control."""
    for mutation in (
        "document.querySelector('button').remove()",
        "document.querySelector('[role=listitem]').insertAdjacentHTML('beforeend', '<button type=\"button\">Request 2K</button>')",
        "document.querySelector('button').hidden = true",
        "document.querySelector('button').disabled = true",
        "document.body.insertAdjacentHTML('beforeend', '<dialog open aria-label=\"Blocking\"></dialog>')",
    ):
        flow_generation_page.goto(fake_generation_url("grid-two.html"))
        fingerprint = observe_local_candidates(flow_generation_page)[0].fingerprint
        flow_generation_page.evaluate(mutation)
        with pytest.raises(FlowGenerationUiContractError):
            resolve_local_candidate_2k(flow_generation_page, fingerprint)


@pytest.mark.parametrize(
    ("fixture", "mutation"),
    (
        (
            "grid-three.html",
            "document.querySelectorAll('[role=listitem]')[1].setAttribute('data-flow-candidate-id', 'candidate-c')",
        ),
        ("grid-two.html", "document.querySelectorAll('[role=listitem]')[1].hidden = true"),
        (
            "grid-two.html",
            "document.querySelectorAll('[role=listitem]')[1].setAttribute('aria-disabled', 'true')",
        ),
        (
            "grid-two.html",
            "document.querySelectorAll('[role=listitem]')[1].removeAttribute('data-flow-completion-role')",
        ),
        ("loading-grid.html", None),
        ("failed-grid.html", None),
    ),
)
def test_candidate_2k_action_revalidates_every_non_target_grid_slot(
    flow_generation_page: Page,
    fixture: str,
    mutation: str | None,
) -> None:
    """Ignoring one invalid non-target slot could make the selected candidate's grid evidence stale."""
    flow_generation_page.goto(fake_generation_url("grid-two.html"))
    target_fingerprint = observe_local_candidates(flow_generation_page)[0].fingerprint
    flow_generation_page.goto(fake_generation_url(fixture))
    if mutation is not None:
        flow_generation_page.evaluate(mutation)

    with pytest.raises(FlowGenerationUiContractError):
        resolve_local_candidate_2k(flow_generation_page, target_fingerprint)


def test_generation_locators_reject_an_untrusted_route(flow_generation_page: Page) -> None:
    """Resolving controls after a redirect would violate the fixed Flow trust boundary."""
    flow_generation_page.goto("data:text/html,<button>Generate</button>")

    with pytest.raises(FlowGenerationUiContractError) as caught:
        resolve_generate_control(flow_generation_page)

    assert caught.value.failed_step == "open_workspace"


@pytest.mark.parametrize(
    "resolver",
    (
        resolve_reference_input,
        resolve_upload_complete,
        resolve_generation_prompt,
        resolve_generate_control,
        resolve_generating_indicator,
        observe_completed_candidate_slots,
        lambda page, **kwargs: resolve_candidate_2k_action(page, "a" * 64, **kwargs),
    ),
)
def test_every_generation_resolver_rejects_an_untrusted_route(
    flow_generation_page: Page,
    resolver: Callable[[Page], object],
) -> None:
    """A redirect must prevent every observation or action resolver before DOM inspection."""
    flow_generation_page.goto("data:text/html,<main></main>")

    with pytest.raises(FlowGenerationUiContractError) as caught:
        resolver(flow_generation_page)

    assert caught.value.failed_step == "open_workspace"


def test_local_generation_pages_and_click_paths_make_no_http_requests(flow_generation_page: Page) -> None:
    """A fixture request would invalidate deterministic browser coverage without a provider call."""
    requested_urls: list[str] = []
    flow_generation_page.on("request", lambda request: requested_urls.append(request.url))

    for fixture in FLOW_GENERATION_FIXTURES:
        flow_generation_page.goto(fake_generation_url(fixture))
    flow_generation_page.goto(fake_generation_url("grid-two.html"))
    candidate = observe_local_candidates(flow_generation_page)[0]
    resolve_local_candidate_2k(flow_generation_page, candidate.fingerprint).click()

    assert requested_urls
    assert {urlsplit(url).scheme for url in requested_urls} == {"file"}


@pytest.mark.parametrize("aria_disabled", ("TRUE", "1", "False", "", " true"))
def test_scalar_locator_rejects_noncanonical_aria_disabled_values(
    flow_generation_page: Page,
    aria_disabled: str,
) -> None:
    """Accepting a malformed disabled state could treat an unavailable provider action as enabled."""
    flow_generation_page.goto(fake_generation_url("ready.html"))
    flow_generation_page.get_by_role("button", name="Generate").evaluate(
        "(element, value) => element.setAttribute('aria-disabled', value)", aria_disabled
    )

    with pytest.raises(FlowGenerationUiContractError):
        resolve_local_locator(resolve_generate_control, flow_generation_page)


def test_scalar_locator_accepts_only_canonical_false_aria_disabled_value(
    flow_generation_page: Page,
) -> None:
    """Rejecting canonical false would make an explicitly enabled semantic control unusable."""
    flow_generation_page.goto(fake_generation_url("ready.html"))
    flow_generation_page.get_by_role("button", name="Generate").evaluate(
        "element => element.setAttribute('aria-disabled', 'false')"
    )

    assert resolve_local_locator(resolve_generate_control, flow_generation_page).count() == 1


@pytest.mark.parametrize(
    ("url", "allowed"),
    (
        ("https://labs.google/fx/tools/flow", True),
        ("https://labs.google/fx/tools/flow/workspaces/approved", True),
        ("https://flow.google.com/project/4f4aeb44-ea73-43f9-b622-77080a525fe8", True),
        ("https://flow.google.com/project/not-a-uuid", False),
        ("https://flow.google.com/project/4f4aeb44-ea73-43f9-b622-77080a525fe8/extra", False),
        ("https://flow.google.com/project/4f4aeb44-ea73-43f9-b622-77080a525fe8?token=private", False),
        ("https://flow.google.com/project/4f4aeb44-ea73-43f9-b622-77080a525fe8#private", False),
        ("https://evil.google.com/project/4f4aeb44-ea73-43f9-b622-77080a525fe8", False),
        ("https://labs.google/fx/tools/flow-evil", False),
        ("https://labs.google/fx/tools/flow?token=private", False),
        ("https://labs.google/fx/tools/flow#private", False),
        ("https://evil.invalid/fx/tools/flow", False),
    ),
)
def test_production_target_accepts_only_the_exact_flow_route_boundary(url: str, allowed: bool) -> None:
    """A prefix match could redirect the worker to an attacker-controlled Flow-looking route."""
    assert _PRODUCTION_GENERATION_TARGET.allows_url(url) is allowed


def test_local_target_accepts_only_explicit_fixture_urls() -> None:
    """Accepting arbitrary local files would turn the test seam into a production route bypass."""
    ready_url = fake_generation_url("ready.html")
    target = _local_test_target(ready_url)

    assert target.allows_url(ready_url) is True
    assert target.allows_url(fake_generation_url("grid-two.html")) is False


def test_production_identity_source_rejects_fixture_only_data_flow_attributes(
    flow_generation_page: Page,
) -> None:
    """Treating data-flow attributes as provider identity would make test scaffolding a live contract."""
    flow_generation_page.goto(fake_generation_url("grid-two.html"))
    target = _local_test_target(
        fake_generation_url("grid-two.html"),
        identity_source=_PRODUCTION_GENERATION_TARGET.identity_source,
    )

    with pytest.raises(FlowGenerationUiContractError):
        observe_completed_candidate_slots(flow_generation_page, _target=target)


def test_production_safe_identity_source_requires_explicit_allowlisted_attributes(
    flow_generation_page: Page,
) -> None:
    """A provider without the reviewed semantic attributes must fail closed rather than hash UI text."""
    flow_generation_page.goto(fake_generation_url("production-safe-grid.html"))
    target = _local_test_target(
        fake_generation_url("production-safe-grid.html"),
        identity_source=_PRODUCTION_GENERATION_TARGET.identity_source,
    )

    observations = observe_completed_candidate_slots(flow_generation_page, _target=target)

    assert [item.semantic_order for item in observations] == [0, 1]
    assert len({item.fingerprint for item in observations}) == 2


def test_flow_locator_modules_prohibit_unsafe_selector_and_coordinate_escape_hatches() -> None:
    """Adding a positional/image selector would undermine semantic candidate recovery."""
    flow_root = Path(__file__).parents[1] / "src" / "auraly_pipeline" / "flow"
    sources = [
        (flow_root / name).read_text(encoding="utf-8")
        for name in ("locators.py", "generation_locators.py", "runtime.py")
    ]
    forbidden_text = ("xpath=", "nth-child", ".nth(", "get_by_alt_text", "class=")
    approved_structural_selectors = {
        "flow-rich-text-editor",
        "[contenteditable='true']",
    }

    for source in sources:
        tree = ast.parse(source)
        assert not any(marker in source.casefold() for marker in forbidden_text)
        assert not any(
            isinstance(node, ast.Attribute) and node.attr in {"mouse", "nth"}
            for node in ast.walk(tree)
        )
        locator_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "locator"
        ]
        assert all(
            len(call.args) == 1
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value in approved_structural_selectors
            for call in locator_calls
        )
        assert not any(
            isinstance(node, ast.keyword) and node.arg in {"position", "x", "y"}
            for node in ast.walk(tree)
        )

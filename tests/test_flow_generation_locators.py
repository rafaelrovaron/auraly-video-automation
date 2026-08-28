from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright
import pytest

from auraly_pipeline.flow.generation_domain import FlowGenerationUiContractError
from auraly_pipeline.flow.generation_locators import (
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
)


def fake_generation_url(fixture: str) -> str:
    """Return the private deterministic local-page target for Flow generation tests."""
    return (FLOW_GENERATION_ROOT / fixture).resolve(strict=True).as_uri()


LOCAL_FLOW_TARGET = _local_test_target(
    *(fake_generation_url(fixture) for fixture in FLOW_GENERATION_FIXTURES)
)


def resolve_on_local_page(resolver: object, page: Page, *args: object) -> object:
    """Invoke a real resolver through the explicit local-only route and identity policy."""
    assert callable(resolver)
    return resolver(page, *args, _target=LOCAL_FLOW_TARGET)  # type: ignore[operator]


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

    assert resolve_on_local_page(resolve_reference_input, flow_generation_page).count() == 1
    assert resolve_on_local_page(resolve_generation_prompt, flow_generation_page).count() == 1
    assert resolve_on_local_page(resolve_generate_control, flow_generation_page).count() == 1


def test_upload_complete_and_generating_pages_resolve_exact_state_indicators(
    flow_generation_page: Page,
) -> None:
    """Treating generic page readiness as positive provider state would permit unsafe progress."""
    flow_generation_page.goto(fake_generation_url("upload-complete.html"))
    assert resolve_on_local_page(resolve_upload_complete, flow_generation_page).count() == 1

    flow_generation_page.goto(fake_generation_url("generating.html"))
    assert resolve_on_local_page(resolve_generating_indicator, flow_generation_page).count() == 1


def test_grid_three_returns_unique_semantic_identities_in_validated_order(
    flow_generation_page: Page,
) -> None:
    """Position-only candidate selection would bind downloads to the wrong provider result."""
    flow_generation_page.goto(fake_generation_url("grid-three.html"))

    observations = resolve_on_local_page(observe_completed_candidate_slots, flow_generation_page)

    assert [item.semantic_order for item in observations] == [0, 1, 2]
    assert len({item.fingerprint for item in observations}) == 3
    assert all(item.completed for item in observations)


def test_exact_candidate_2k_action_is_bound_by_observed_fingerprint(
    flow_generation_page: Page,
) -> None:
    """Changing the fingerprint match must prevent a 2K action on another candidate."""
    flow_generation_page.goto(fake_generation_url("grid-two.html"))
    (first, second) = resolve_on_local_page(observe_completed_candidate_slots, flow_generation_page)

    action = resolve_on_local_page(
        resolve_candidate_2k_action, flow_generation_page, second.fingerprint
    )

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
    resolver: object,
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
            resolve_on_local_page(resolver, flow_generation_page)


def test_duplicate_or_missing_candidate_identity_and_2k_action_fail_closed(
    flow_generation_page: Page,
) -> None:
    """Duplicate provider identities or absent 2K actions must block rather than guess."""
    flow_generation_page.goto(fake_generation_url("ambiguous-grid.html"))
    with pytest.raises(FlowGenerationUiContractError):
        resolve_on_local_page(observe_completed_candidate_slots, flow_generation_page)

    flow_generation_page.goto(fake_generation_url("missing-2k.html"))
    (candidate,) = resolve_on_local_page(observe_completed_candidate_slots, flow_generation_page)
    with pytest.raises(FlowGenerationUiContractError):
        resolve_on_local_page(resolve_candidate_2k_action, flow_generation_page, candidate.fingerprint)


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
            resolve_on_local_page(observe_completed_candidate_slots, flow_generation_page)


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
        fingerprint = resolve_on_local_page(
            observe_completed_candidate_slots, flow_generation_page
        )[0].fingerprint
        flow_generation_page.evaluate(mutation)
        with pytest.raises(FlowGenerationUiContractError):
            resolve_on_local_page(resolve_candidate_2k_action, flow_generation_page, fingerprint)


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
    target_fingerprint = resolve_on_local_page(
        observe_completed_candidate_slots, flow_generation_page
    )[0].fingerprint
    flow_generation_page.goto(fake_generation_url(fixture))
    if mutation is not None:
        flow_generation_page.evaluate(mutation)

    with pytest.raises(FlowGenerationUiContractError):
        resolve_on_local_page(resolve_candidate_2k_action, flow_generation_page, target_fingerprint)


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
    resolver: object,
) -> None:
    """A redirect must prevent every observation or action resolver before DOM inspection."""
    assert callable(resolver)
    flow_generation_page.goto("data:text/html,<main></main>")

    with pytest.raises(FlowGenerationUiContractError) as caught:
        resolver(flow_generation_page)  # type: ignore[operator]

    assert caught.value.failed_step == "open_workspace"


def test_local_generation_pages_and_click_paths_make_no_http_requests(flow_generation_page: Page) -> None:
    """A fixture request would invalidate deterministic browser coverage without a provider call."""
    requested_urls: list[str] = []
    flow_generation_page.on("request", lambda request: requested_urls.append(request.url))

    for fixture in (
        "ready.html",
        "upload-complete.html",
        "generating.html",
        "grid-two.html",
        "grid-three.html",
        "ambiguous-grid.html",
        *FLOW_GENERATION_FIXTURES[7:],
    ):
        flow_generation_page.goto(fake_generation_url(fixture))
    flow_generation_page.goto(fake_generation_url("grid-two.html"))
    candidate = resolve_on_local_page(observe_completed_candidate_slots, flow_generation_page)[0]
    resolve_on_local_page(resolve_candidate_2k_action, flow_generation_page, candidate.fingerprint).click()

    assert requested_urls
    assert {urlsplit(url).scheme for url in requested_urls} == {"file"}


@pytest.mark.parametrize(
    ("url", "allowed"),
    (
        ("https://labs.google/fx/tools/flow", True),
        ("https://labs.google/fx/tools/flow/workspaces/approved", True),
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

    for source in sources:
        tree = ast.parse(source)
        assert not any(marker in source.casefold() for marker in forbidden_text)
        assert not any(
            isinstance(node, ast.Attribute) and node.attr in {"mouse", "locator", "nth"}
            for node in ast.walk(tree)
        )
        assert not any(
            isinstance(node, ast.keyword) and node.arg in {"position", "x", "y"}
            for node in ast.walk(tree)
        )

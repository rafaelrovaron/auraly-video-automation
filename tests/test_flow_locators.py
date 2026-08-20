from __future__ import annotations

import ast
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page
import pytest

from auraly_pipeline.flow.domain import FlowLocatorName, FlowUiContractError
from auraly_pipeline.flow.locators import (
    REQUIRED_FLOW_LOCATORS,
    LocatorStrategy,
    RequiredLocator,
    blocking_overlay_present,
    resolve_account_identity_masks,
    resolve_required_locator,
)
from tests.flow_browser_support import fake_flow_url, provide_flow_page as provide_flow_page


REQUIRED_LOCATOR_NAMES: tuple[FlowLocatorName, ...] = (
    "FLOW_WORKSPACE",
    "CREATE_ENTRY_POINT",
    "PROMPT_INPUT",
)
FLOW_FIXTURES = (
    "ready.html",
    "login-required.html",
    "login-completes.html",
    "ambiguous-ui.html",
    "missing-prompt.html",
    "blocking-modal.html",
)


def test_required_locator_registry_has_exact_initial_semantic_strategies() -> None:
    assert REQUIRED_FLOW_LOCATORS == {
        "FLOW_WORKSPACE": RequiredLocator(
            name="FLOW_WORKSPACE",
            strategies=(
                LocatorStrategy("role", role="main", value="Flow workspace"),
            ),
        ),
        "CREATE_ENTRY_POINT": RequiredLocator(
            name="CREATE_ENTRY_POINT",
            strategies=(LocatorStrategy("role", role="button", value="Create"),),
        ),
        "PROMPT_INPUT": RequiredLocator(
            name="PROMPT_INPUT",
            strategies=(
                LocatorStrategy("label", value="Prompt"),
                LocatorStrategy("placeholder", value="Describe your image"),
            ),
        ),
    }


def test_ready_page_resolves_exactly_one_required_semantic_element(flow_page: Page) -> None:
    flow_page.goto(fake_flow_url("ready.html"))

    for name in REQUIRED_LOCATOR_NAMES:
        resolved = resolve_required_locator(flow_page, name)
        assert resolved.count() == 1
        assert resolved.is_visible()
        assert resolved.is_enabled()


def test_prompt_resolution_falls_back_to_placeholder_when_label_is_not_applicable(
    flow_page: Page,
) -> None:
    flow_page.goto(fake_flow_url("ready.html"))
    flow_page.evaluate(
        "document.getElementById('prompt-label').removeAttribute('for')"
    )

    resolved = resolve_required_locator(flow_page, "PROMPT_INPUT")

    assert resolved.get_attribute("id") == "prompt"


@pytest.mark.parametrize("fixture", ["missing-prompt.html", "ambiguous-ui.html"])
def test_zero_or_multiple_prompt_matches_fail_closed(flow_page: Page, fixture: str) -> None:
    flow_page.goto(fake_flow_url(fixture))

    with pytest.raises(FlowUiContractError) as caught:
        resolve_required_locator(flow_page, "PROMPT_INPUT")

    assert caught.value.failed_locator == "PROMPT_INPUT"


def test_account_identity_masks_include_every_visible_semantic_match(flow_page: Page) -> None:
    flow_page.goto(fake_flow_url("ready.html"))

    masks = resolve_account_identity_masks(flow_page)

    assert len(masks) == 2
    assert all(mask.count() == 1 and mask.is_visible() for mask in masks)


def test_visible_dialog_or_alertdialog_is_a_blocking_overlay(flow_page: Page) -> None:
    flow_page.goto(fake_flow_url("blocking-modal.html"))

    assert blocking_overlay_present(flow_page) is True

    flow_page.get_by_role("dialog").evaluate("element => { element.hidden = true; }")
    flow_page.get_by_role("alertdialog", include_hidden=True).evaluate(
        "element => { element.hidden = false; }"
    )
    assert blocking_overlay_present(flow_page) is True

    flow_page.get_by_role("alertdialog").evaluate("element => { element.hidden = true; }")
    assert blocking_overlay_present(flow_page) is False


def test_ready_page_has_no_blocking_overlay(flow_page: Page) -> None:
    flow_page.goto(fake_flow_url("ready.html"))

    assert blocking_overlay_present(flow_page) is False


def test_local_flow_fixtures_make_no_http_requests(flow_page: Page) -> None:
    requested_urls: list[str] = []
    flow_page.on("request", lambda request: requested_urls.append(request.url))

    for fixture in FLOW_FIXTURES:
        flow_page.goto(fake_flow_url(fixture))
        if fixture == "login-completes.html":
            flow_page.wait_for_url(fake_flow_url("ready.html"))

    assert requested_urls
    assert {urlsplit(url).scheme for url in requested_urls} == {"file"}


def test_locator_source_is_semantic_observation_only() -> None:
    source_path = Path(__file__).parents[1] / "src" / "auraly_pipeline" / "flow" / "locators.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_attributes = {
        "click",
        "fill",
        "first",
        "hover",
        "locator",
        "mouse",
        "nth",
        "press",
        "query_selector",
        "query_selector_all",
        "set_input_files",
        "type",
        "upload",
    }

    assert "playwright" not in source.casefold()
    assert "xpath" not in source.casefold()
    assert "nth-child" not in source.casefold()
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Subscript)
        and (
            isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
            or isinstance(node.slice, ast.UnaryOp)
            and isinstance(node.slice.operand, ast.Constant)
            and isinstance(node.slice.operand.value, int)
        )
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.keyword) and node.arg in {"position", "x", "y"}
        for node in ast.walk(tree)
    )

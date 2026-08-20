from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


FAKE_FLOW_ROOT = Path(__file__).parent / "fakes" / "flow"


def fake_flow_url(fixture: str) -> str:
    """Return a local file URL for one deterministic Flow fixture."""
    return (FAKE_FLOW_ROOT / fixture).resolve(strict=True).as_uri()


@pytest.fixture(scope="module", name="flow_page")
def provide_flow_page() -> Iterator[Page]:
    """Provide a Playwright-managed headed Chromium page for local Flow tests."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            yield browser.new_page()
        finally:
            browser.close()

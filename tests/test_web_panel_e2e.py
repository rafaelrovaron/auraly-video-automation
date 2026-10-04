from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import socket

import httpx
from playwright.sync_api import Page, expect, sync_playwright
import pytest

from tests.api_helpers import database_dump
from tests.web_panel_support import PanelServers, reserve_port
from tests.web_panel_support import panel_servers as provide_panel_servers  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


@pytest.fixture
def panel_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_panel_read_navigation_is_non_mutating(panel_servers: PanelServers, panel_page: Page) -> None:
    before = database_dump(panel_servers.settings.database)
    writes: list[str] = []
    panel_page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' else None)
    panel_page.goto(panel_servers.ui_url)
    panel_page.get_by_role('link', name='campaign-one', exact=True).click()
    expect(panel_page.get_by_role('heading', name='campaign-one', exact=True)).to_be_visible()
    voices = panel_page.get_by_role('region', name='Voice Masters')
    expect(voices.get_by_text('elevenlabs', exact=True)).to_be_visible()
    expect(panel_page.get_by_role('region', name='HeyGen').get_by_text('Disponível', exact=True).first).to_be_visible()
    expect(panel_page.get_by_role('region', name='Imagens').get_by_text('png', exact=True).first).to_be_visible()
    panel_page.reload()
    expect(panel_page.get_by_role('button', name='Iniciar worker', exact=True)).to_be_enabled()
    # Wait for a second real polling response, not an arbitrary sleep.
    with panel_page.expect_response(lambda response: response.url.endswith('/status')):
        pass
    assert writes == []
    assert database_dump(panel_servers.settings.database) == before
    assert panel_servers.provider.events == []
    assert panel_page.locator('audio,video,img').count() == 0


def test_panel_dev_server_does_not_serve_repository_files(panel_servers: PanelServers) -> None:
    root = Path(__file__).resolve().parents[1]
    reference = (root / 'AGENTS.md').read_text(encoding='utf-8').splitlines()
    with httpx.Client(base_url=panel_servers.ui_url) as client:
        alias = client.get('node_modules/auraly-video-pipeline/AGENTS.md')
        assert alias.text.splitlines() != reference
        direct = client.get('/@fs/' + (root / 'AGENTS.md').as_posix())
        assert direct.status_code == 403


def test_explicit_worker_start_stop_through_real_proxy(panel_servers: PanelServers, panel_page: Page) -> None:
    panel_page.goto(panel_servers.ui_url + '#/campaigns/campaign-one')
    start = panel_page.get_by_role('button', name='Iniciar worker', exact=True)
    expect(start).to_be_enabled()
    start.click()
    expect(panel_page.get_by_text('Jobs já enfileirados podem consumir créditos.')).to_be_visible()
    assert not panel_servers.entered.is_set()
    with panel_page.expect_response(lambda response: response.url.endswith('/worker/start')) as response:
        panel_page.get_by_role('button', name='Confirmar início').click()
    assert response.value.status == 202
    assert panel_servers.entered.wait(10)
    stop = panel_page.get_by_role('button', name='Parar worker', exact=True)
    expect(stop).to_be_enabled()
    with panel_page.expect_response(lambda response: response.url.endswith('/worker/stop')) as stopped:
        stop.click()
    assert stopped.value.status == 200
    expect(panel_page.get_by_text('Parando após o trabalho atual', exact=True)).to_be_visible()
    commands = panel_servers.app.state.commands
    assert commands.jobs.get_job(panel_servers.job_ids[0]).status == 'running'
    assert commands.jobs.get_job(panel_servers.job_ids[1]).attempt_count == 0
    panel_servers.release.set()
    expect(panel_page.get_by_text('Parado', exact=True)).to_be_visible()
    assert commands.jobs.get_job(panel_servers.job_ids[0]).status == 'completed'
    assert commands.jobs.get_job(panel_servers.job_ids[1]).status == 'queued'
    panel_page.goto(panel_servers.ui_url + '#/campaigns/campaign-two')
    expect(panel_page.get_by_text('Estado desconhecido: worker não associado a esta campanha.')).to_be_visible()
    expect(panel_page.get_by_role('button', name='Parar worker', exact=True)).to_be_disabled()
    assert panel_servers.provider.events == []


@pytest.mark.parametrize('origin', ['https://foreign.example', 'null'])
def test_proxy_preserves_origin_boundary(panel_servers: PanelServers, origin: str) -> None:
    before = database_dump(panel_servers.settings.database)
    path = panel_servers.ui_url + 'api/v1/campaigns/campaign-one/worker/start'
    response = httpx.post(path, json={'campaignId': 'campaign-one', 'kind': 'local_operations'}, headers={'Origin': origin})
    assert response.status_code == 422
    assert response.json() == {'error': {'code': 'invalid_request', 'message': 'Invalid request.', 'field': None}}
    malformed = httpx.post(path, content='private-token-not-json', headers={'Origin': panel_servers.ui_url.rstrip('/'), 'Content-Type': 'application/json'})
    assert malformed.status_code == 422
    assert 'private-token' not in malformed.text
    assert database_dump(panel_servers.settings.database) == before


def test_panel_accessible_at_narrow_width(panel_servers: PanelServers, panel_page: Page) -> None:
    panel_page.set_viewport_size({'width': 390, 'height': 844})
    panel_page.goto(panel_servers.ui_url + '#/campaigns/campaign-one')
    button = panel_page.get_by_role('button', name='Iniciar worker', exact=True)
    expect(button).to_be_enabled()
    button.focus()
    panel_page.keyboard.press('Enter')
    expect(panel_page.get_by_role('group', name='Confirmação de início')).to_be_visible()
    panel_page.get_by_role('button', name='Cancelar', exact=True).focus()
    panel_page.keyboard.press('Enter')
    expect(panel_page.get_by_role('group', name='Confirmação de início')).to_have_count(0)
    assert panel_page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert panel_servers.provider.events == []


def test_panel_servers_refuse_occupied_ports() -> None:
    with socket.socket() as owned:
        owned.bind(('127.0.0.1', 0))
        owned.listen()
        port = owned.getsockname()[1]
        with pytest.raises(ValueError, match='Local panel port is occupied'):
            reserve_port(port)
        assert owned.fileno() != -1

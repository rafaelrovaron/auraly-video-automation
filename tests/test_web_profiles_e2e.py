from __future__ import annotations

from playwright.sync_api import Page, Route, expect
import pytest

from tests.api_helpers import database_dump
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.web_panel_support import PanelServers
from tests.web_panel_support import panel_servers as provide_panel_servers  # noqa: F401
from tests.web_panel_support import panel_heygen_provider, panel_speech_provider, panel_transcriber  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


def create_profile(servers: PanelServers, page: Page, profile_id: str = 'ui-test') -> None:
    page.goto(servers.ui_url + '#/profiles')
    page.get_by_role('button', name='Novo profile', exact=True).click()
    page.get_by_label('ID do profile', exact=True).fill(profile_id)
    page.get_by_label('Nome do profile', exact=True).fill('Local profile')
    page.get_by_role('button', name='Salvar versão', exact=True).click()
    expect(page.get_by_text('Versão publicada e confirmada.', exact=True)).to_be_visible()


def test_profiles_create_version_and_reload(panel_servers: PanelServers, panel_page: Page) -> None:
    before = database_dump(panel_servers.settings.database)
    events = list(panel_servers.provider.events)
    create_profile(panel_servers, panel_page)
    path = panel_servers.settings.work_root / 'editing/profiles/ui-test/1/profile.json'
    original = path.read_bytes()
    panel_page.get_by_role('button', name='Criar nova versão', exact=True).click()
    panel_page.get_by_label('Headline · Cor (RGB/RGBA)', exact=True).fill('#FFFFFF80')
    panel_page.get_by_label('Headline · Fonte: caminho relativo', exact=True).fill('fonts/local.ttf')
    panel_page.get_by_label('Headline · Fonte: SHA-256', exact=True).fill('a' * 64)
    panel_page.get_by_label('Música · Asset: caminho relativo', exact=True).fill('music/local.wav')
    panel_page.get_by_label('Música · Asset: SHA-256', exact=True).fill('b' * 64)
    panel_page.get_by_label('Música · Volume (dB)', exact=True).fill('0')
    panel_page.get_by_label('Música · Loop', exact=True).uncheck()
    panel_page.get_by_label('Enquadramento · Fit', exact=True).select_option('contain')
    panel_page.get_by_role('button', name='Salvar versão', exact=True).click()
    expect(panel_page.get_by_text('Versão publicada e confirmada.', exact=True)).to_be_visible()
    profile = panel_servers.app.state.queries.get_profile('ui-test', 2).profile
    assert profile.defaults.headline.color == '#FFFFFF80'
    assert profile.defaults.headline.font.path == 'fonts/local.ttf'
    assert profile.defaults.music.volume_db == 0 and not profile.defaults.music.loop
    assert profile.defaults.framing.fit == 'contain'
    assert path.read_bytes() == original
    panel_page.reload()
    panel_page.get_by_label('Consultar profile e versão', exact=True).select_option('ui-test/2')
    expect(panel_page.get_by_label('Headline · Cor (RGB/RGBA)', exact=True)).to_have_value('#FFFFFF80')
    expect(panel_page.get_by_label('Nome do profile', exact=True)).to_be_disabled()
    assert database_dump(panel_servers.settings.database) == before
    assert panel_servers.provider.events == events


def test_saved_profile_with_lost_response(panel_servers: PanelServers, panel_page: Page) -> None:
    posts: list[str] = []

    def lose_response(route: Route) -> None:
        if route.request.method == 'POST':
            posts.append(route.request.url)
            response = route.fetch()
            assert response.status == 201
            route.fulfill(status=503, json={'error': {'code': 'storage_unavailable'}})
        else:
            route.continue_()

    panel_page.route('**/api/v1/editing/profiles', lose_response)
    panel_page.goto(panel_servers.ui_url + '#/profiles')
    panel_page.get_by_role('button', name='Novo profile', exact=True).click()
    panel_page.get_by_label('ID do profile', exact=True).fill('lost-response')
    panel_page.get_by_label('Nome do profile', exact=True).fill('Local profile')
    panel_page.get_by_role('button', name='Salvar versão', exact=True).click()
    expect(panel_page.get_by_role('button', name='Salvar versão', exact=True)).to_be_disabled()
    panel_page.get_by_role('button', name='Consultar versão enviada', exact=True).click()
    expect(panel_page.get_by_text('Versão publicada e confirmada.', exact=True)).to_be_visible()
    assert panel_servers.app.state.queries.get_profile('lost-response', 1).profile.version == 1
    assert len(posts) == 1
    assert panel_servers.provider.events == []


def test_profile_conflict_preserves_existing(panel_servers: PanelServers, panel_page: Page) -> None:
    create_profile(panel_servers, panel_page)
    commands = panel_servers.app.state.commands
    profile = panel_servers.app.state.queries.get_profile('ui-test', 1).profile.model_copy(deep=True)
    profile.version = 2
    profile.name = 'External version'
    commands.publish_profile(profile, base_version=1)
    path = panel_servers.settings.work_root / 'editing/profiles/ui-test/2/profile.json'
    original = path.read_bytes()
    panel_page.get_by_role('button', name='Criar nova versão', exact=True).click()
    panel_page.get_by_label('Nome do profile', exact=True).fill('Conflicting version')
    panel_page.get_by_role('button', name='Salvar versão', exact=True).click()
    expect(panel_page.get_by_text('Artefato armazenado inválido. (artifact_invalid)', exact=True)).to_be_visible()
    panel_page.get_by_role('button', name='Consultar versão enviada', exact=True).click()
    expect(panel_page.get_by_text('A versão existente tem conteúdo diferente. Não será sobrescrita.', exact=True)).to_be_visible()
    assert path.read_bytes() == original
    assert panel_servers.app.state.queries.get_profile('ui-test', 2).profile.name == 'External version'
    assert panel_servers.provider.events == []


def test_profiles_discard_navigation(panel_servers: PanelServers, panel_page: Page) -> None:
    create_profile(panel_servers, panel_page)
    panel_page.get_by_role('button', name='Criar nova versão', exact=True).click()
    panel_page.get_by_label('Nome do profile', exact=True).fill('Keep draft')
    panel_page.on('dialog', lambda dialog: dialog.dismiss())
    panel_page.get_by_role('button', name='Novo profile', exact=True).click()
    expect(panel_page.get_by_label('Nome do profile', exact=True)).to_have_value('Keep draft')
    panel_page.get_by_role('link', name='Campanhas', exact=True).click()
    expect(panel_page.get_by_label('Nome do profile', exact=True)).to_have_value('Keep draft')
    assert panel_page.url.endswith('#/profiles')
    assert panel_servers.provider.events == []


@pytest.mark.parametrize('width', [320, 390])
def test_profiles_narrow_width(panel_servers: PanelServers, panel_page: Page, width: int) -> None:
    panel_page.set_viewport_size({'width': width, 'height': 844})
    create_profile(panel_servers, panel_page, 'p' * 64)
    panel_page.get_by_role('button', name='Criar nova versão', exact=True).focus()
    panel_page.keyboard.press('Enter')
    panel_page.get_by_label('Nome do profile', exact=True).fill('Long name ' * 20)
    panel_page.get_by_label('Headline · Fonte: caminho relativo', exact=True).fill('fonts/' + 'a' * 150 + '.ttf')
    assert panel_page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), panel_page.evaluate(
        'Array.from(document.querySelectorAll("select,input,button")).filter(el => el.getBoundingClientRect().right > innerWidth).map(el => ({tag:el.tagName,label:el.closest("label")?.textContent}))'
    )
    assert panel_servers.provider.events == []

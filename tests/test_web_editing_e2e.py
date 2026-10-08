from __future__ import annotations

import json
import sqlite3
import re
import struct
from pathlib import Path
from collections.abc import Iterator
from tempfile import TemporaryDirectory
from contextlib import closing

from playwright.sync_api import Locator, Page, Route, expect
import pytest

from tests.editing_helpers import file_sha
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.test_web_voice_e2e import start_kind
from tests.web_panel_support import PanelServers
from tests.web_panel_support import panel_servers as provide_panel_servers  # noqa: F401
from tests.web_panel_support import panel_heygen_provider, panel_speech_provider, panel_transcriber  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


@pytest.fixture(name='tmp_path')
def short_root() -> Iterator[Path]:
    # Keep deep immutable artifact paths below Windows legacy MAX_PATH.
    with TemporaryDirectory(prefix='ae-') as directory:
        yield Path(directory)


def draft(servers: PanelServers, page: Page) -> Locator:
    servers.release.set()
    page.goto(servers.ui_url)
    page.get_by_role('link', name='campaign-one', exact=True).click()
    panel = page.get_by_role('region', name='Edição e variantes', exact=True)
    source = panel.get_by_label('MP4 HeyGen para edição', exact=True)
    expect(source.locator('option')).to_have_count(4)
    source.select_option(index=1)
    panel.get_by_label('Profile de edição', exact=True).select_option('plain/1')
    panel.get_by_label('Headline base', exact=True).fill('Base')
    return panel


def validate(page: Page, panel: Locator) -> None:
    panel.get_by_role('button', name='Validar plano', exact=True).click()
    expect(panel.get_by_text(re.compile('^Operação editorial na fila'))).to_be_visible()
    start_kind(page, 'local_operations')
    expect(panel.get_by_text('Plano validado. Nenhum arquivo de edição publicado.', exact=True)).to_be_visible(timeout=15000)


def save(page: Page, panel: Locator) -> None:
    panel.get_by_role('button', name='Salvar plano', exact=True).click()
    expect(panel.get_by_text(re.compile('^Operação editorial na fila'))).to_be_visible()
    start_kind(page, 'local_operations')
    expect(panel.get_by_text('Plano salvo e confirmado. Não é um vídeo renderizado.', exact=True)).to_be_visible(timeout=15000)


def upstream(servers: PanelServers) -> tuple[list[tuple[Path, str]], list[tuple[object, ...]]]:
    files = [(p, file_sha(p)) for p in servers.settings.work_root.rglob('*')
             if p.suffix in {'.mp4', '.wav', '.png'} or p.name == 'profile.json']
    with closing(sqlite3.connect(servers.settings.database)) as connection:
        rows = connection.execute('SELECT budget_json FROM campaigns ORDER BY id').fetchall()
        rows += connection.execute('SELECT approval_state,approved_by FROM copy_masters ORDER BY id').fetchall()
    return files, rows


def preview_headline(servers: PanelServers, panel: Locator) -> None:
    font = servers.settings.project_root / 'preview-font.ttf'
    tags = [b'cmap', b'head', b'hhea', b'hmtx', b'maxp', b'name']
    font.write_bytes(struct.pack('>IHHHH', 0x10000, len(tags), 0, 0, 0)
                    + b''.join(struct.pack('>4sIII', tag, 0, 108 + index, 1)
                               for index, tag in enumerate(tags)) + b'\0' * len(tags))
    group = panel.get_by_role('group', name='Vídeo · Overrides', exact=True)
    group.get_by_text('Vídeo · Opções avançadas', exact=True).click()
    group.get_by_text('Headline', exact=True).click()
    group.get_by_label('Vídeo · Headline · enabled · Modo', exact=True).select_option('replace')
    group.get_by_label('Vídeo · Headline · enabled', exact=True).check()
    group.get_by_label('Vídeo · Headline · font · Modo', exact=True).select_option('replace')
    group.get_by_label('Vídeo · Headline · font · Caminho', exact=True).fill('preview-font.ttf')
    group.get_by_label('Vídeo · Headline · font · SHA-256', exact=True).fill(file_sha(font))


def test_three_headlines_validate_save_reload(panel_servers: PanelServers, panel_page: Page) -> None:
    before = upstream(panel_servers)
    panel = draft(panel_servers, panel_page)
    requests: list[tuple[str, str]] = []
    panel_page.on('request', lambda request: requests.append((request.method, request.url)))
    preview_headline(panel_servers, panel)
    preview = panel.get_by_role('region', name='Preview da edição', exact=True)
    image = preview.get_by_alt_text('Frame do MP4 selecionado')
    expect(image).to_be_visible()
    expect(image).to_have_js_property('naturalWidth', 405)
    expect(image).to_have_js_property('naturalHeight', 720)
    for key in ['a', 'b', 'c']:
        if key != 'a':
            panel.get_by_role('button', name='Adicionar variante', exact=True).click()
        panel.get_by_label(f'Variante {key} · Headline · Texto · Modo', exact=True).select_option('replace')
        panel.get_by_label(f'Variante {key} · Headline · Texto', exact=True).fill(key.upper())
        panel.get_by_label('Variante no preview', exact=True).select_option(label=f'{key} · {key.upper()}')
        expect(preview.get_by_label('Headline no preview')).to_have_text(key.upper())
    assert not [method for method, _ in requests if method == 'POST']
    assert len([url for _, url in requests if '/poster/' in url]) <= 1
    validate(panel_page, panel)
    assert not list(panel_servers.settings.work_root.rglob('plan.json'))
    assert not list(panel_servers.settings.work_root.rglob('manifest.json'))
    save(panel_page, panel)
    plans = list(panel_servers.settings.work_root.rglob('plan.json'))
    assert len(plans) == 1
    plan = json.loads(plans[0].read_text(encoding='utf-8'))
    assert [o['manifest']['headline']['text'] for o in plan['outputs']] == ['A', 'B', 'C']
    panel_page.reload()
    panel.get_by_label('Consultar plano salvo', exact=True).select_option(f"{plan['videoId']}/{plan['planHash']}")
    expect(panel.get_by_text('Saída planejada: ' + plan['outputs'][0]['filename'] + '. Nenhum MP4 final foi renderizado.', exact=True)).to_be_visible()
    assert upstream(panel_servers) == before
    assert panel_servers.provider.events == []


def test_preview_frame_failure_and_readonly_keep_draft(panel_servers: PanelServers, panel_page: Page) -> None:
    panel_page.route('**/poster/**', lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
    panel = draft(panel_servers, panel_page)
    preview = panel.get_by_role('region', name='Preview da edição', exact=True)
    expect(preview.get_by_text(re.compile('^Frame indisponível'))).to_be_visible()
    validate(panel_page, panel)
    save(panel_page, panel)
    plan = json.loads(next(panel_servers.settings.work_root.rglob('plan.json')).read_text(encoding='utf-8'))
    panel.get_by_label('Headline base', exact=True).fill('Rascunho preservado')
    panel.get_by_label('Consultar plano salvo', exact=True).select_option(f"{plan['videoId']}/{plan['planHash']}")
    expect(panel.get_by_label('Origem do preview', exact=True).locator('option[value="stored"]')).to_have_count(1)
    panel.get_by_label('Origem do preview', exact=True).select_option('stored')
    expect(preview.get_by_text('Plano salvo · consulta readonly', exact=True)).to_be_visible()
    expect(panel.get_by_label('Headline base', exact=True)).to_have_value('Rascunho preservado')
    panel.get_by_label('Origem do preview', exact=True).select_option('draft')
    expect(panel.get_by_role('button', name='Salvar plano', exact=True)).to_be_disabled()
    assert panel_servers.provider.events == []


@pytest.mark.parametrize('width', [320, 390])
def test_preview_narrow_width_and_long_text(panel_servers: PanelServers, panel_page: Page, width: int) -> None:
    panel_page.set_viewport_size({'width': width, 'height': 900})
    panel = draft(panel_servers, panel_page)
    preview_headline(panel_servers, panel)
    panel.get_by_label('Headline base', exact=True).fill('Long headline for testing wrapping and overflow ' * 30)
    preview = panel.get_by_role('region', name='Preview da edição', exact=True)
    expect(preview.get_by_text(re.compile('overflow aproximado'))).to_be_visible()
    expect(preview.get_by_alt_text('Frame do MP4 selecionado')).to_be_visible()
    assert panel_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    preview.get_by_role('button', name='Recarregar frame').focus()
    panel_page.keyboard.press('Enter')
    expect(preview.get_by_alt_text('Frame do MP4 selecionado')).to_be_visible()
    screenshot = Path(__file__).resolve().parents[1] / '.superpowers' / 'sdd' / '2026-10-08-d4b3c-approximate-preview' / f'preview-{width}.png'
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    preview.screenshot(path=str(screenshot))


@pytest.mark.parametrize(('output_width', 'output_height'), [(16, 1024), (1024, 16)])
def test_preview_safe_zones_and_extreme_ratios(panel_servers: PanelServers, panel_page: Page,
                                             output_width: int, output_height: int) -> None:
    panel = draft(panel_servers, panel_page)
    preview_headline(panel_servers, panel)
    group = panel.get_by_role('group', name='Vídeo · Overrides', exact=True)
    preview = panel.get_by_role('region', name='Preview da edição', exact=True)
    warning = preview.get_by_text(re.compile('Headline no preview: overflow aproximado'))
    for field, value in [('anchor', 'top'), ('y', '1')]:
        group.get_by_label(f'Vídeo · Headline · {field} · Modo', exact=True).select_option('replace')
        control = group.get_by_label(f'Vídeo · Headline · {field}', exact=True)
        if field == 'anchor':
            control.select_option(value)
        else:
            control.fill(value)
    expect(warning).to_be_visible()
    group.get_by_label('Vídeo · Headline · y', exact=True).fill('0.5')
    expect(warning).to_have_count(0)
    group.get_by_label('Vídeo · Headline · safeTop · Modo', exact=True).select_option('replace')
    group.get_by_label('Vídeo · Headline · safeTop', exact=True).fill('0.8')
    expect(warning).to_be_visible()
    group.get_by_text('Output', exact=True).click()
    for field, value in [('width', output_width), ('height', output_height)]:
        group.get_by_label(f'Vídeo · Output · {field} · Modo', exact=True).select_option('replace')
        group.get_by_label(f'Vídeo · Output · {field}', exact=True).fill(str(value))
    viewport = preview.locator('.preview-viewport')
    box = viewport.bounding_box()
    assert box is not None
    assert box['height'] <= 641 and box['width'] <= 361
    assert abs(box['width'] / box['height'] - output_width / output_height) < 0.01
    expect(warning).to_be_visible()
    assert panel_servers.provider.events == []


def test_caption_timing_missing_is_pending(panel_servers: PanelServers, panel_page: Page) -> None:
    panel = draft(panel_servers, panel_page)
    font = panel_servers.settings.project_root / 'font.ttf'
    # Synthetic SFNT tables exercise real local asset/hash validation, not rendered glyphs.
    tags = [b'cmap', b'head', b'hhea', b'hmtx', b'maxp', b'name']
    font.write_bytes(struct.pack('>IHHHH', 0x10000, len(tags), 0, 0, 0)
                    + b''.join(struct.pack('>4sIII', tag, 0, 108 + index, 1)
                               for index, tag in enumerate(tags)) + b'\0' * len(tags))
    panel.get_by_text('Vídeo · Opções avançadas', exact=True).click()
    panel.get_by_role('group', name='Vídeo · Overrides', exact=True).get_by_text('Legendas', exact=True).click()
    panel.get_by_label('Vídeo · Legendas · enabled · Modo', exact=True).select_option('replace')
    panel.get_by_label('Vídeo · Legendas · enabled', exact=True).check()
    panel.get_by_label('Vídeo · Legendas · font · Modo', exact=True).select_option('replace')
    panel.get_by_label('Vídeo · Legendas · font · Caminho', exact=True).fill('font.ttf')
    panel.get_by_label('Vídeo · Legendas · font · SHA-256', exact=True).fill(file_sha(font))
    validate(panel_page, panel)
    expect(panel.get_by_text('Legendas: Timing pendente', exact=True)).to_be_visible()
    save(panel_page, panel)
    plan = json.loads(next(panel_servers.settings.work_root.rglob('plan.json')).read_text(encoding='utf-8'))
    assert plan['outputs'][0]['captionState'] == 'timing_missing'
    assert panel_servers.provider.events == []


def test_lost_save_response_never_reposts(panel_servers: PanelServers, panel_page: Page) -> None:
    panel = draft(panel_servers, panel_page)
    validate(panel_page, panel)
    posts: list[str] = []

    def lose(route: Route) -> None:
        body = route.request.post_data_json
        if route.request.method == 'POST' and isinstance(body, dict) and body['persist']:
            posts.append(route.request.url)
            assert route.fetch().status == 202
            route.fulfill(status=503, json={'error': {'code': 'storage_unavailable'}})
        else:
            route.continue_()

    panel_page.route('**/editing/plans', lose)
    panel.get_by_role('button', name='Salvar plano', exact=True).click()
    expect(panel.get_by_text('Resultado editorial desconhecido. Nenhum POST será reenviado.', exact=True)).to_be_visible()
    start_kind(panel_page, 'local_operations')
    expect(panel_page.get_by_role('button', name='Iniciar worker', exact=True)).to_be_enabled(timeout=15000)
    panel.get_by_role('button', name='Consultar plano enviado', exact=True).click()
    expect(panel.get_by_text('Resultado editorial desconhecido. Artefato encontrado; isto não confirma autoria do POST perdido.', exact=True)).to_be_visible()
    expect(panel.get_by_role('button', name='Salvar plano', exact=True)).to_be_disabled()
    assert len(posts) == 1
    assert len(list(panel_servers.settings.work_root.rglob('plan.json'))) == 1
    assert panel_servers.provider.events == []


def test_editing_navigation_and_back_cancel(panel_servers: PanelServers, panel_page: Page) -> None:
    panel = draft(panel_servers, panel_page)
    panel_page.on('dialog', lambda dialog: dialog.dismiss())
    panel.get_by_label('MP4 HeyGen para edição', exact=True).select_option(index=2)
    panel.get_by_label('Profile de edição', exact=True).select_option('')
    expect(panel.get_by_label('Headline base', exact=True)).to_have_value('Base')
    panel.get_by_role('link', name='Gerenciar profiles de edição', exact=True).click()
    expect(panel.get_by_label('Headline base', exact=True)).to_have_value('Base')
    panel_page.go_back()
    expect(panel.get_by_label('Headline base', exact=True)).to_have_value('Base')
    assert panel_page.url.endswith('#/campaigns/campaign-one')


def test_variant_fallback_collision_does_not_hang(panel_servers: PanelServers, panel_page: Page) -> None:
    panel = draft(panel_servers, panel_page)
    panel.get_by_label('Limite de saídas', exact=True).fill('30')
    panel.get_by_label('Key da variante a', exact=True).fill('v27')
    for _ in range(25):
        panel.get_by_role('button', name='Adicionar variante', exact=True).click()
    panel.get_by_role('button', name='Adicionar variante', exact=True).click(timeout=5000)
    expect(panel.get_by_label('Key da variante v26', exact=True)).to_be_visible()
    assert panel_servers.provider.events == []


@pytest.mark.parametrize('width', [320, 390])
def test_editing_narrow_width(panel_servers: PanelServers, panel_page: Page, width: int) -> None:
    panel_page.set_viewport_size({'width': width, 'height': 844})
    panel = draft(panel_servers, panel_page)
    panel.get_by_label('ID do vídeo editorial', exact=True).fill('v' * 64)
    panel.get_by_label('Headline base', exact=True).fill('Long headline ' * 30)
    panel.get_by_role('button', name='Adicionar variante', exact=True).focus()
    panel_page.keyboard.press('Enter')
    expect(panel.get_by_label('Key da variante b', exact=True)).to_be_visible()
    assert panel_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    validate(panel_page, panel)
    panel.get_by_text('Configuração resolvida e origem · a', exact=True).click()
    assert panel_page.evaluate('document.documentElement.scrollWidth <= innerWidth'), panel_page.evaluate(
        'Array.from(document.querySelectorAll("pre,select,input,button")).filter(el=>el.getBoundingClientRect().right>innerWidth||el.scrollWidth>el.clientWidth).map(el=>({tag:el.tagName,width:el.clientWidth,scroll:el.scrollWidth,text:el.textContent?.slice(0,30)}))'
    )

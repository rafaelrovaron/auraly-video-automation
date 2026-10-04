from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image
from playwright.sync_api import Locator, Page, Route, expect
import pytest

from auraly_pipeline.api.contracts import QueryError

from tests.test_api_operations import count_images
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.web_panel_support import PanelServers
from tests.web_panel_support import panel_servers as provide_panel_servers  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


def fill_copy(form: Locator, headline: str = 'Headline') -> None:
    for label, value in [('Headline', headline), ('Hook', 'Hook'), ('Body', 'Body'), ('CTA', 'CTA'), ('Aprovado por', 'tester')]:
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_label('Confirmo a aprovação da copy', exact=True).check()


def create_campaign(servers: PanelServers, page: Page, scene_count: int) -> str:
    campaign_id = 'browser-import'
    page.goto(servers.ui_url)
    page.get_by_text('Criar campanha', exact=True).click()
    form = page.get_by_role('form', name='Criar campanha', exact=True)
    for label, value in [('ID da campanha', campaign_id), ('Objeto de prova', 'cards'), ('Voice preset', 'voice'), ('Edit preset', 'edit')]:
        form.get_by_label(label, exact=True).fill(value)
    fill_copy(form)
    for index in range(1, scene_count + 1):
        if index > 1:
            form.get_by_role('button', name='Adicionar cena', exact=True).click()
        for label, value in [('ID da variante', f'scene-{index}'), ('Localização', f'Room {index}'), ('Ação', 'Talk'), ('Prompt', 'Portrait')]:
            form.get_by_label(f'{label} {index}', exact=True).fill(value)
    form.get_by_role('button', name='Criar campanha com copy aprovada').click()
    expect(page.get_by_role('heading', name=campaign_id, exact=True)).to_be_visible()
    return campaign_id


def start_local_worker(page: Page) -> None:
    start = page.get_by_role('button', name='Iniciar worker', exact=True)
    expect(start).to_be_enabled(timeout=15000)
    start.click()
    page.get_by_role('button', name='Confirmar início', exact=True).click()


def prepare_and_validate(servers: PanelServers, page: Page, count: int) -> list[tuple[Path, bytes]]:
    servers.release.set()
    panel = page.get_by_role('region', name='Importação manual de imagens')
    panel.get_by_label('Pasta de importação (relativa ao work root)', exact=True).fill('browser inbox')
    panel.get_by_role('button', name='Preparar pasta', exact=True).click()
    assert not servers.entered.is_set()
    start_local_worker(page)
    expect(panel.get_by_label('Pasta preparada (relativa ao projeto)', exact=True)).not_to_have_value('', timeout=15000)
    sources = []
    for index in range(1, count + 1):
        path = servers.settings.work_root / 'browser inbox' / 'images' / f'imagem {index}.png'
        Image.new('RGB', (360, 640), (index * 40, 60, 90)).save(path)
        sources.append((path, path.read_bytes()))
        panel.get_by_label(f'Arquivo de scene-{index}', exact=True).fill(f'images/imagem {index}.png')
    panel.get_by_role('button', name='Salvar associações', exact=True).click()
    start_local_worker(page)
    expect(panel.get_by_text('Associações salvas. Valide o batch antes de importar.', exact=True)).to_be_visible(timeout=15000)
    panel.get_by_role('button', name='Validar batch', exact=True).click()
    start_local_worker(page)
    expect(panel.get_by_role('heading', name='Batch válido', exact=True)).to_be_visible(timeout=15000)
    return sources


def import_validated(page: Page) -> None:
    panel = page.get_by_role('region', name='Importação manual de imagens')
    panel.get_by_label('Confirmo a importação deste batch', exact=True).check()
    panel.get_by_role('button', name='Importar batch validado', exact=True).click()
    start_local_worker(page)


def test_create_campaign_import_three_images_and_review_through_panel(panel_servers: PanelServers, panel_page: Page) -> None:
    before = count_images(panel_servers.settings.database)
    campaign_id = create_campaign(panel_servers, panel_page, 3)
    panel_page.get_by_text('Nova versão de copy', exact=True).click()
    copy_form = panel_page.get_by_role('form', name='Nova versão de copy', exact=True)
    fill_copy(copy_form, 'Headline B')
    copy_form.get_by_role('button', name='Adicionar versão de copy aprovada').click()
    expect(copy_form.get_by_text('Versão de copy registrada. Histórico preservado.', exact=True)).to_be_visible()
    campaign = panel_servers.app.state.commands.campaigns.get_campaign(campaign_id)
    assert len(campaign.scene_variants) == 3
    assert len(campaign.copy_masters) == 2
    assert campaign.copy_masters[0].spoken_text == 'Hook\n\nBody\n\nCTA'
    assert 'Headline' not in campaign.copy_masters[0].spoken_text
    sources = prepare_and_validate(panel_servers, panel_page, 3)
    import_validated(panel_page)
    panel = panel_page.get_by_role('region', name='Importação manual de imagens')
    expect(panel.get_by_text('Importação concluída. Revise os candidatos antes de aprovar.', exact=True)).to_be_visible(timeout=15000)
    assert count_images(panel_servers.settings.database) == before + 3
    candidates = panel_servers.app.state.queries.list_images(campaign_id)
    ids = [image.image_candidate_id for scene in candidates for image in scene.items]
    assert len(ids) == 3
    for candidate_id in ids:
        panel.get_by_role('combobox', name='Candidato de imagem', exact=True).select_option(candidate_id)
        panel.get_by_label('Ator da revisão', exact=True).fill('tester')
        panel.get_by_label('Confirmo esta revisão de imagem', exact=True).check()
        panel.get_by_role('button', name='Aplicar revisão', exact=True).click()
        expect(panel.get_by_text('Estado solicitado observado após a revisão. Esta consulta não informa autoria.', exact=True)).to_be_visible()
        expect(panel.get_by_role('combobox', name='Candidato de imagem', exact=True)).to_be_enabled()
    assert all(panel_servers.app.state.commands.image_review.get_candidate(candidate_id).review_status == 'approved' for candidate_id in ids)
    assert all(path.read_bytes() == original for path, original in sources)
    posts: list[str] = []
    panel_page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    panel_page.reload()
    expect(panel_page.get_by_role('heading', name='Batch manual de imagens', exact=True)).to_be_visible()
    expect(panel_page.get_by_role('button', name='Importar batch validado', exact=True)).to_be_disabled()
    with panel_page.expect_response(lambda response: response.url.endswith('/status')):
        pass
    assert posts == []
    assert panel_servers.provider.events == []


def test_changed_source_requires_revalidation_through_panel(panel_servers: PanelServers, panel_page: Page) -> None:
    before = count_images(panel_servers.settings.database)
    create_campaign(panel_servers, panel_page, 1)
    sources = prepare_and_validate(panel_servers, panel_page, 1)
    path, original = sources[0]
    Image.new('RGB', (360, 640), 'purple').save(path)
    assert path.read_bytes() != original
    import_validated(panel_page)
    panel = panel_page.get_by_role('region', name='Importação manual de imagens')
    expect(panel.get_by_text('Job Falhou (artifact_invalid). Consulte o job antes de uma nova ação.', exact=True)).to_be_visible(timeout=15000)
    assert count_images(panel_servers.settings.database) == before
    expect(panel.get_by_role('button', name='Importar batch validado', exact=True)).to_be_disabled()
    panel.get_by_role('button', name='Validar batch', exact=True).click()
    start_local_worker(panel_page)
    expect(panel.get_by_role('heading', name='Batch válido', exact=True)).to_be_visible(timeout=15000)
    expect(panel.get_by_text(hashlib.sha256(path.read_bytes()).hexdigest(), exact=True)).to_be_visible()
    import_validated(panel_page)
    expect(panel.get_by_text('Importação concluída. Revise os candidatos antes de aprovar.', exact=True)).to_be_visible(timeout=15000)
    assert count_images(panel_servers.settings.database) == before + 1
    panel_page.set_viewport_size({'width': 390, 'height': 844})
    panel.get_by_label('Ator da revisão', exact=True).focus()
    panel_page.keyboard.type('tester')
    expect(panel.get_by_label('Ator da revisão', exact=True)).to_have_value('tester')
    assert panel_page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert panel_servers.provider.events == []


def test_committed_copy_with_failed_response_query_requires_reconciliation(
    panel_servers: PanelServers, panel_page: Page, monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = create_campaign(panel_servers, panel_page, 1)
    panel_page.get_by_text('Nova versão de copy', exact=True).click()
    form = panel_page.get_by_role('form', name='Nova versão de copy', exact=True)
    fill_copy(form, 'Headline B')
    failed = False
    held: list[Route] = []

    def fail_response(campaign: str) -> object:
        nonlocal failed
        failed = True
        raise QueryError('storage_unavailable')

    def hold_reconciliation(route: Route) -> None:
        if failed:
            held.append(route)
        else:
            route.continue_()

    monkeypatch.setattr(panel_servers.app.state.commands.queries, 'get_campaign', fail_response)
    panel_page.route(f'**/api/v1/campaigns/{campaign_id}', hold_reconciliation)
    posts: list[str] = []
    panel_page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    with panel_page.expect_response(lambda response: response.url.endswith('/copies')) as response:
        form.get_by_role('button', name='Adicionar versão de copy aprovada').click()
    assert response.value.status == 503
    assert len(panel_servers.app.state.commands.campaigns.get_campaign(campaign_id).copy_masters) == 2
    expect(form.get_by_role('status')).to_contain_text('command_unknown')
    expect(form.get_by_role('button', name='Adicionar versão de copy aprovada')).to_be_disabled()
    assert len(held) == 1
    held.pop().continue_()
    expect(form.get_by_text('Versão de copy registrada. Histórico preservado.', exact=True)).to_be_visible()
    assert len(posts) == 1
    assert panel_servers.provider.events == []

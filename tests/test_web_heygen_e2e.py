from __future__ import annotations

from functools import partial
import hashlib
from pathlib import Path
import subprocess

import httpx
from playwright.sync_api import Page, Route, expect
import pytest

from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.provider import HeyGenProviderFailure
from auraly_pipeline.heygen.video_domain import VideoPlanItem
from auraly_pipeline.heygen.video_handler import HeyGenVideoHandler
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.test_web_voice_e2e import start_kind
from tests.web_panel_support import PanelServers
from tests.web_panel_support import panel_servers as provide_panel_servers  # noqa: F401
from tests.web_panel_support import panel_speech_provider, panel_transcriber  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


class BrowserHeyGen(FakeHeyGenProvider):
    lose_next_dispatch = False
    lost_video_id: str | None = None

    def create_video(self, item: VideoPlanItem, *, callback_id: str) -> str:
        video_id = super().create_video(item, callback_id=callback_id)
        if self.lose_next_dispatch:
            self.lose_next_dispatch = False
            self.lost_video_id = video_id
            raise HeyGenProviderFailure('ambiguous', 'Fake response lost after dispatch', request_dispatched=True)
        return video_id


@pytest.fixture
def panel_heygen_provider(monkeypatch: pytest.MonkeyPatch, mp4: bytes, tmp_path: Path) -> BrowserHeyGen:
    import auraly_pipeline.api.commands as module

    # Preserve the 720p historical seed; new UI requests are fixed at 1080p.
    output = tmp_path / 'browser-download-1080.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', 'pipe:0', '-vf', 'scale=1080:1920', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(output)],
                   input=mp4, check=True, capture_output=True, timeout=30)
    media = output.read_bytes()

    def download(request: httpx.Request) -> httpx.Response:
        assert request.url.host == 'fake.invalid' and request.url.path.endswith('.mp4')
        return httpx.Response(200, content=media)

    monkeypatch.setattr(module, 'HeyGenVideoHandler', partial(HeyGenVideoHandler,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(download))))
    return BrowserHeyGen(account_ref='account-browser')


def prepare_assets(servers: PanelServers, page: Page) -> None:
    servers.release.set()
    page.goto(servers.ui_url + '#/campaigns/campaign-one')
    prepare = page.get_by_role('button', name='Preparar assets HeyGen', exact=True)
    expect(prepare).to_be_enabled()
    prepare.click()
    expect(page.get_by_text('Operação na fila. Inicie local_operations explicitamente.', exact=True)).to_be_visible()
    assert not any(event.startswith('put:') or event == 'create_video' for event in servers.provider.events)
    start_kind(page, 'local_operations')
    expect(page.get_by_text('Uploads planejados: 3 · reuso: 0', exact=True)).to_be_visible(timeout=15000)
    assert not any(event.startswith('put:') for event in servers.provider.events)
    start_kind(page, 'heygen_assets')
    expect(page.get_by_text('Upload concluído.', exact=True)).to_be_visible(timeout=15000)
    assert len([event for event in servers.provider.events if event.startswith('put:')]) == 3
    assert servers.provider.events.count('create_video') == 0


def reserve_videos(servers: PanelServers, page: Page) -> set[str]:
    previous = {render.render_id for render in servers.app.state.queries.list_renders('campaign-one')}
    page.get_by_label('Limite total de renders reservados da campanha', exact=True).fill('6')
    page.get_by_role('button', name='Planejar batch HeyGen', exact=True).click()
    start_kind(page, 'local_operations')
    expect(page.get_by_text('Novos: 3 · reuso: 0 · Reservas históricas: 3', exact=True)).to_be_visible(timeout=15000)
    submit = page.get_by_role('button', name='Enfileirar geração HeyGen', exact=True)
    expect(submit).to_be_disabled()
    assert servers.provider.events.count('create_video') == 0
    page.get_by_label('Responsável pela geração HeyGen', exact=True).fill('tester')
    page.get_by_label('Autorizo a geração paga deste batch; o limite inclui o histórico da campanha.', exact=True).check()
    submit.click()
    start_kind(page, 'local_operations')
    expect(page.get_by_text('Reservas aceitas;', exact=False)).to_be_visible(timeout=15000)
    renders = servers.app.state.queries.list_renders('campaign-one')
    assert len(renders) == 6
    created = {render.render_id for render in renders} - previous
    assert len(created) == 3
    assert servers.provider.events.count('create_video') == 0
    return created


def test_heygen_panel_prepares_plans_submits_and_downloads(panel_servers: PanelServers, panel_page: Page) -> None:
    before = {path: path.read_bytes() for path in panel_servers.settings.work_root.rglob('*') if path.is_file() and path.suffix in {'.wav', '.png'}}
    prepare_assets(panel_servers, panel_page)
    created = reserve_videos(panel_servers, panel_page)
    start_kind(panel_page, 'heygen_videos')
    for render_id in created:
        article = panel_page.get_by_role('region', name='HeyGen', exact=True).locator('article').filter(has=panel_page.get_by_role('heading', name=render_id, exact=True))
        expect(article.get_by_text('Disponível', exact=True)).to_be_visible(timeout=30000)
        expect(article.get_by_text('Caminho relativo ao work root.', exact=False)).to_be_visible()
    renders = [render for render in panel_servers.app.state.queries.list_renders('campaign-one') if render.render_id in created]
    assert all(render.status == 'ready' and render.source is not None for render in renders)
    assert len({render.audio_sha256 for render in renders}) == 1
    for render in renders:
        assert render.source is not None
        path = panel_servers.settings.work_root / render.source.path
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == render.source.sha256
        assert path.stat().st_size == render.source.size_bytes
        assert render.source.probe.video.codec == 'h264' and render.source.probe.duration_sec > 0
    assert all(path.read_bytes() == content for path, content in before.items())
    assert panel_servers.provider.events.count('create_video') == 3
    panel_page.get_by_role('button', name='Preparar assets HeyGen', exact=True).click()
    start_kind(panel_page, 'local_operations')
    expect(panel_page.get_by_text('Uploads planejados: 0 · reuso: 3', exact=True)).to_be_visible(timeout=15000)
    assert len([event for event in panel_servers.provider.events if event.startswith('put:')]) == 3
    events = list(panel_servers.provider.events)
    posts: list[str] = []
    panel_page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    panel_page.on('dialog', lambda dialog: dialog.accept())
    panel_page.reload()
    expect(panel_page.get_by_role('button', name='Preparar assets HeyGen', exact=True)).to_be_enabled()
    with panel_page.expect_response(lambda response: response.url.endswith('/status')):
        pass
    assert posts == [] and panel_servers.provider.events == events
    assert panel_page.locator('audio,video,img').count() == 0


def test_heygen_panel_reconciliation_never_duplicates_dispatch(
    panel_servers: PanelServers, panel_page: Page, panel_heygen_provider: BrowserHeyGen,
) -> None:
    prepare_assets(panel_servers, panel_page)
    created = reserve_videos(panel_servers, panel_page)
    panel_heygen_provider.lose_next_dispatch = True
    start_kind(panel_page, 'heygen_videos')
    expect(panel_page.get_by_text('Requer reconciliação', exact=True)).to_be_visible(timeout=30000)
    blocked = [render for render in panel_servers.app.state.queries.list_renders('campaign-one') if render.render_id in created and render.status == 'reconciliation_required']
    assert len(blocked) == 1 and panel_heygen_provider.lost_video_id is not None
    exact = panel_heygen_provider.lost_video_id
    evidence = panel_heygen_provider.get_video(exact)
    assert evidence.video_id == exact
    render_id = blocked[0].render_id
    panel_page.get_by_role('combobox', name='Render para reconciliação', exact=True).select_option(render_id)
    panel_page.get_by_label('ID exato do vídeo HeyGen', exact=True).fill(exact)
    reconcile = panel_page.get_by_role('button', name='Reconciliar render HeyGen', exact=True)
    expect(reconcile).to_be_disabled()
    panel_page.get_by_label('Confirmo o vínculo manual deste ID exato com o render selecionado.', exact=True).check()
    reconcile.click()
    start_kind(panel_page, 'local_operations')
    expect(panel_page.get_by_text('Reconciliação recebida; consulte o render e o Job atual.', exact=True)).to_be_visible(timeout=15000)
    assert panel_heygen_provider.events.count('create_video') == 3
    start_kind(panel_page, 'heygen_videos')
    article = panel_page.get_by_role('region', name='HeyGen', exact=True).locator('article').filter(has=panel_page.get_by_role('heading', name=render_id, exact=True))
    expect(article.get_by_text('Disponível', exact=True)).to_be_visible(timeout=30000)
    stored = next(render for render in panel_servers.app.state.queries.list_renders('campaign-one') if render.render_id == render_id)
    assert stored.remote_video_id == exact and stored.manual_binding and stored.source is not None
    assert panel_heygen_provider.events.count('create_video') == 3


def test_lost_heygen_response_does_not_repost(panel_servers: PanelServers, panel_page: Page) -> None:
    panel_page.goto(panel_servers.ui_url + '#/campaigns/campaign-one')
    posts: list[str] = []

    def lost(route: Route) -> None:
        if route.request.method != 'POST':
            route.continue_()
            return
        posts.append(route.request.url)
        assert route.fetch().status == 202
        route.fulfill(status=503, content_type='application/json', body='{"error":{"code":"internal_error"}}')

    panel_page.route('**/heygen/assets/prepare', lost)
    prepare = panel_page.get_by_role('button', name='Preparar assets HeyGen', exact=True)
    expect(prepare).to_be_enabled()
    prepare.click()
    expect(panel_page.get_by_text('Resultado do comando desconhecido.', exact=False)).to_be_visible()
    expect(prepare).to_be_disabled()
    wrappers = [job for job in panel_servers.app.state.queries.list_jobs('campaign-one')
                if job.job_type == 'api.local.operation' and panel_servers.app.state.commands.jobs.get_job(job.job_id).input.get('operation') == 'heygen_assets']
    assert len(posts) == 1 and len(wrappers) == 1
    assert panel_servers.provider.events == []
    expect(panel_page.get_by_text('Intenção:', exact=False)).to_be_visible()
    assert panel_page.get_by_text('Operação:', exact=False).count() == 0


def test_heygen_panel_narrow_width(panel_servers: PanelServers, panel_page: Page) -> None:
    panel_page.set_viewport_size({'width': 390, 'height': 844})
    panel_page.goto(panel_servers.ui_url + '#/campaigns/campaign-one')
    expect(panel_page.get_by_role('button', name='Preparar assets HeyGen', exact=True)).to_be_enabled()
    assert panel_page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), panel_page.evaluate(
        'Array.from(document.querySelectorAll("select,input,button")).filter(el => el.getBoundingClientRect().right > innerWidth).map(el => ({tag:el.tagName,label:el.closest("label")?.textContent}))')
    assert panel_servers.provider.events == []

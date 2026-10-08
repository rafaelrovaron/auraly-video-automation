from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import gettempdir, mkdtemp

from playwright.sync_api import Locator, Page, expect
import pytest

from auraly_pipeline.api.render_contracts import RenderJobView
from auraly_pipeline.editing.render_media import check_master
from tests.editing_helpers import file_sha
from tests.test_web_editing_e2e import draft, save, upstream, validate
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.web_panel_support import PanelServers, panel_servers as provide_panel_servers  # noqa: F401
from tests.web_panel_support import panel_heygen_provider, panel_speech_provider, panel_transcriber  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


@pytest.fixture(name='tmp_path')
def short_root() -> Iterator[Path]:
    directory = Path(mkdtemp(prefix='ar-')).resolve()
    assert directory.parent == Path(gettempdir()).resolve() and directory.name.startswith('ar-')
    try:
        yield directory
    finally:
        # Published render filenames exceed legacy MAX_PATH even under a short root.
        cleanup = Path('\\\\?\\' + str(directory)) if os.name == 'nt' else directory
        shutil.rmtree(cleanup)


def headlines(servers: PanelServers, panel: Locator) -> None:
    font = servers.settings.project_root / 'render-font.ttf'
    choices = [Path('C:/Windows/Fonts/arial.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
               Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf')]
    origin = next((p for p in choices if p.is_file()), None)
    assert origin is not None, 'real local font required, no silent skip'
    shutil.copyfile(origin, font)
    group = panel.get_by_role('group', name='Vídeo · Overrides', exact=True)
    group.get_by_text('Vídeo · Opções avançadas', exact=True).click()
    group.get_by_text('Headline', exact=True).click()
    group.get_by_label('Vídeo · Headline · enabled · Modo', exact=True).select_option('replace')
    group.get_by_label('Vídeo · Headline · enabled', exact=True).check()
    group.get_by_label('Vídeo · Headline · font · Modo', exact=True).select_option('replace')
    group.get_by_label('Vídeo · Headline · font · Caminho', exact=True).fill('render-font.ttf')
    group.get_by_label('Vídeo · Headline · font · SHA-256', exact=True).fill(file_sha(font))
    for key in ['a', 'b', 'c']:
        if key != 'a':
            panel.get_by_role('button', name='Adicionar variante', exact=True).click()
        panel.get_by_label(f'Variante {key} · Headline · Texto · Modo', exact=True).select_option('replace')
        panel.get_by_label(f'Variante {key} · Headline · Texto', exact=True).fill('Headline ' + key.upper())


def latest(servers: PanelServers, video_id: str, plan_hash: str) -> RenderJobView:
    views = servers.app.state.commands.editorial_renders.list('campaign-one', video_id=video_id, plan_hash=plan_hash)
    assert views
    return views[-1]


def test_saved_plan_renders_three_variants_then_reuses_after_reload(panel_servers: PanelServers, panel_page: Page) -> None:
    servers, page = panel_servers, panel_page
    before = upstream(servers)
    panel = draft(servers, page)
    headlines(servers, panel)
    validate(page, panel)
    save(page, panel)
    plan = json.loads(next(servers.settings.work_root.rglob('plan.json')).read_bytes())
    render = panel.get_by_role('region', name='Render final', exact=True)
    render.get_by_role('button', name='Renderizar', exact=True).click()
    expect(render.get_by_text('Render concluído', exact=True)).to_be_visible(timeout=60000)
    first = latest(servers, plan['videoId'], plan['planHash'])
    assert first.result is not None and first.render_status == 'succeeded'
    assert [o.status for o in first.result.outputs] == ['rendered'] * 3
    assert len({o.render_key for o in first.result.outputs}) == 3
    receipts = {}
    for item in first.result.outputs:
        assert item.path is not None
        receipt_path = (servers.app.state.commands.renderer.work_root / item.path).with_name('render.json')
        receipts[receipt_path] = receipt_path.read_bytes()
    assert len(receipts) == 3
    hashes = []
    for index in range(3):
        with page.expect_download() as downloaded:
            render.get_by_role('link', name='Baixar MP4', exact=True).nth(index).click()
        target = servers.settings.project_root / f'download-{index}.mp4'
        downloaded.value.save_as(str(target))
        probe = check_master(target, duration_sec=plan['source']['durationSec'], full_decode=True)
        assert probe.video.width == 1080 and probe.video.height == 1920 and probe.video.fps == 30
        hashes.append(file_sha(target))
    assert len(set(hashes)) == 3
    page.reload()
    panel.get_by_label('Consultar plano salvo', exact=True).select_option(f"{plan['videoId']}/{plan['planHash']}")
    render.get_by_label('Execução de render', exact=True).select_option(first.job_id)
    expect(render.get_by_text('Render concluído', exact=True)).to_be_visible()
    render.get_by_role('button', name='Nova execução', exact=True).click()
    expect(render.get_by_text('Render concluído', exact=True)).to_be_visible(timeout=60000)
    views = servers.app.state.commands.editorial_renders.list('campaign-one', video_id=plan['videoId'], plan_hash=plan['planHash'])
    second = next(v for v in views if v.job_id != first.job_id)
    assert second.result is not None and second.render_status == 'succeeded'
    assert second.execution_id != first.execution_id
    assert [o.status for o in second.result.outputs] == ['reused'] * 3
    assert receipts == {p: p.read_bytes() for p in receipts}
    assert [(p, file_sha(p)) for p, _ in before[0]] == before[0]
    with closing(sqlite3.connect(servers.settings.database)) as connection:
        rows = connection.execute('SELECT budget_json FROM campaigns ORDER BY id').fetchall()
        rows += connection.execute('SELECT approval_state,approved_by FROM copy_masters ORDER BY id').fetchall()
    assert rows == before[1] and servers.provider.events == []


def test_editorial_draft_does_not_change_saved_render_target(panel_servers: PanelServers, panel_page: Page) -> None:
    servers, page = panel_servers, panel_page
    panel = draft(servers, page)
    validate(page, panel)
    save(page, panel)
    plan = json.loads(next(servers.settings.work_root.rglob('plan.json')).read_bytes())
    panel.get_by_label('Headline base', exact=True).fill('Different unsaved headline')
    preview = panel.get_by_role('region', name='Preview da edição', exact=True)
    expect(preview.get_by_text('Preview aproximado', exact=False)).to_be_visible()
    render = panel.get_by_role('region', name='Render final', exact=True)
    expect(render.get_by_text(plan['planHash'], exact=False)).to_be_visible()
    render.get_by_role('button', name='Renderizar', exact=True).click()
    expect(render.get_by_text('Render concluído', exact=True)).to_be_visible(timeout=60000)
    view = latest(servers, plan['videoId'], plan['planHash'])
    assert view.plan_hash == plan['planHash'] and view.result is not None
    assert view.result.plan_hash == plan['planHash']
    assert json.loads(next(servers.settings.work_root.rglob('plan.json')).read_bytes()) == plan
    assert servers.provider.events == []

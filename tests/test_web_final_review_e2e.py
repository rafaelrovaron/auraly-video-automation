from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess

from playwright.sync_api import Locator, Page, expect
import pytest

from tests.editing_helpers import file_sha
from tests.test_web_editing_e2e import draft, save, upstream, validate
from tests.test_web_render_e2e import headlines, latest, short_root as provide_short_root  # noqa: F401
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.web_panel_support import PanelServers, panel_servers as provide_panel_servers  # noqa: F401
from tests.web_panel_support import panel_heygen_provider, panel_speech_provider, panel_transcriber  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


@pytest.fixture
def mp4(tmp_path: Path) -> bytes:
    # Shared HeyGen media fixture is silent; final approval needs measurable audio.
    path = tmp_path / 'review-fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=720x1280:r=5:d=1',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=1', '-t', '1',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)],
        check=True, capture_output=True, timeout=30)
    return path.read_bytes()


def prepare(servers: PanelServers, page: Page) -> tuple[Locator, dict[str, str], str]:
    panel = draft(servers, page)
    headlines(servers, panel)
    panel.get_by_role('button', name='Remover variante c', exact=True).click()
    validate(page, panel)
    save(page, panel)
    plan = json.loads(next(servers.settings.work_root.rglob('plan.json')).read_bytes())
    render = panel.get_by_role('region', name='Render final', exact=True)
    render.get_by_role('button', name='Renderizar', exact=True).click()
    expect(render.get_by_text('Render concluído', exact=True)).to_be_visible(timeout=60000)
    view = latest(servers, plan['videoId'], plan['planHash'])
    assert view.result is not None and len(view.result.outputs) == 2
    assert [item.status for item in view.result.outputs] == ['rendered', 'rendered']
    return render, plan, view.job_id


def qc(review: Locator) -> None:
    review.get_by_role('button', name='Executar QC', exact=True).click()
    expect(review.get_by_text('QC técnico: Aprovado', exact=True)).to_be_visible(timeout=60000)


def decide(page: Page, review: Locator, decision: str, comment: str | None = None) -> None:
    review.get_by_label('Nome do revisor', exact=True).fill('E2E Tester')
    if comment is not None:
        review.get_by_label('Comentário', exact=True).fill(comment)
    if decision == 'Aprovar':
        review.get_by_label('Assisti e ouvi este master', exact=True).check()
    page.once('dialog', lambda dialog: dialog.accept())
    review.get_by_role('button', name=decision + ' master', exact=True).click()
    expect(review.get_by_text('Decisão definitiva: ' + ('Aprovado' if decision == 'Aprovar' else 'Rejeitado'), exact=True)).to_be_visible()


def reload_render(page: Page, render: Locator, plan: dict[str, str], job_id: str) -> None:
    page.reload()
    page.get_by_label('Consultar plano salvo', exact=True).select_option(f"{plan['videoId']}/{plan['planHash']}")
    render.get_by_label('Execução de render', exact=True).select_option(job_id)
    expect(render.get_by_text('Render concluído', exact=True)).to_be_visible()


def test_render_output_qc_approval_reload_and_rejection(panel_servers: PanelServers, panel_page: Page,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    servers, page = panel_servers, panel_page
    before = upstream(servers)
    render, plan, job_id = prepare(servers, page)
    root = servers.app.state.commands.renderer.work_root
    immutable = {path: file_sha(path) for path in root.rglob('*') if path.name in {'master.mp4', 'render.json', 'plan.json'} or path.suffix == '.mp4'}
    def no_encode(*args: object, **kwargs: object) -> None:
        pytest.fail('QC / review must not render')
    monkeypatch.setattr(servers.app.state.commands.renderer, 'render', no_encode)
    reviews = render.get_by_role('region', name='QC e review', exact=False)
    expect(reviews).to_have_count(2)
    first, second = reviews.nth(0), reviews.nth(1)
    expect(first.get_by_label('Master final')).to_have_attribute('preload', 'metadata')
    page.wait_for_function("""() => document.querySelector('video[aria-label="Master final"]').readyState >= 1""", timeout=30000)
    metadata = first.get_by_label('Master final').evaluate('(v) => ({width:v.videoWidth,height:v.videoHeight,duration:v.duration,controls:v.controls,autoplay:v.autoplay,muted:v.muted})')
    assert metadata['width'] == 1080 and metadata['height'] == 1920 and metadata['duration'] > 0
    assert metadata['controls'] and not metadata['autoplay'] and not metadata['muted']
    qc(first)
    decide(page, first, 'Aprovar')
    records = {path: path.read_bytes() for path in root.rglob('review.json')}
    assert len(records) == 1
    reload_render(page, render, plan, job_id)
    expect(first.get_by_text('Decisão definitiva: Aprovado', exact=True)).to_be_visible()
    expect(first.get_by_role('button', name='Aprovar master')).to_have_count(0)
    qc(second)
    decide(page, second, 'Rejeitar', 'Headline precisa de ajuste')
    records.update({path: path.read_bytes() for path in root.rglob('review.json')})
    assert len(records) == 2
    reload_render(page, render, plan, job_id)
    expect(first.get_by_text('Decisão definitiva: Aprovado', exact=True)).to_be_visible()
    expect(second.get_by_text('Decisão definitiva: Rejeitado', exact=True)).to_be_visible()
    expect(second.get_by_text('Comentário: Headline precisa de ajuste', exact=True)).to_be_visible()
    assert records == {path: path.read_bytes() for path in records}
    assert immutable == {path: file_sha(path) for path in immutable}
    assert [(path, file_sha(path)) for path, _ in before[0]] == before[0]
    with closing(sqlite3.connect(servers.settings.database)) as connection:
        rows = connection.execute('SELECT budget_json FROM campaigns ORDER BY id').fetchall()
        rows += connection.execute('SELECT approval_state,approved_by FROM copy_masters ORDER BY id').fetchall()
    assert rows == before[1] and servers.provider.events == []


def test_changed_evidence_removes_actionable_approval(panel_servers: PanelServers, panel_page: Page) -> None:
    servers, page = panel_servers, panel_page
    render, _, _ = prepare(servers, page)
    first = render.get_by_role('region', name='QC e review', exact=False).nth(0)
    qc(first)
    decide(page, first, 'Aprovar')
    report_path = next(servers.app.state.commands.editorial_reviews.qc.work_root.rglob('report.json'))
    report_path.write_bytes(b'private-secret-corrupt-evidence')
    first.get_by_role('button', name='Consultar QC e review', exact=True).click()
    expect(first.get_by_text('Artefato armazenado inválido.', exact=True)).to_be_visible()
    expect(first.get_by_text('Decisão definitiva: Aprovado', exact=True)).to_have_count(0)
    expect(first.get_by_role('button', name='Aprovar master', exact=True)).to_be_disabled()
    expect(first.get_by_role('button', name='Executar QC', exact=True)).to_be_disabled()
    expect(page.get_by_text('private-secret-corrupt-evidence')).to_have_count(0)

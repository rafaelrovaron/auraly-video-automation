from __future__ import annotations

from contextlib import closing
import json
import sqlite3

from playwright.sync_api import Page, expect
import pytest

from tests.editing_helpers import file_sha
from tests.test_web_editing_e2e import upstream
from tests.test_web_final_review_e2e import prepare, qc, decide, reload_render
from tests.test_web_final_review_e2e import mp4 as mp4
from tests.test_web_render_e2e import short_root as provide_short_root  # noqa: F401
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.web_panel_support import PanelServers, panel_servers as provide_panel_servers  # noqa: F401
from tests.web_panel_support import panel_heygen_provider, panel_speech_provider, panel_transcriber  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


def test_approved_master_delivery_reload_and_corruption(panel_servers: PanelServers, panel_page: Page,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    servers, page = panel_servers, panel_page
    before = upstream(servers)
    render, plan, job_id = prepare(servers, page)
    root = servers.app.state.commands.renderer.work_root
    immutable = {path: file_sha(path) for path in root.rglob('*')
                 if path.name in {'master.mp4', 'render.json', 'plan.json'} or path.suffix == '.mp4'}

    def no_encode(*args: object, **kwargs: object) -> None:
        pytest.fail('Delivery must not render again')
    monkeypatch.setattr(servers.app.state.commands.renderer, 'render', no_encode)
    reviews = render.get_by_role('region', name='QC e review', exact=False)
    first, second = reviews.nth(0), reviews.nth(1)
    qc(first)
    decide(page, first, 'Aprovar')
    delivery = first.get_by_role('region', name='Entrega local', exact=False)
    send = delivery.get_by_role('button', name='Entregar master aprovado', exact=True)
    expect(send).to_be_enabled()
    service = servers.app.state.commands.editorial_reviews.deliveries
    assert not service.delivery_root.exists()
    expect(second.get_by_role('button', name='Entregar master aprovado', exact=True)).to_have_count(0)
    evidence = {path: path.read_bytes() for path in root.rglob('*') if path.name in {'review.json', 'report.json'}}
    send.click()
    expect(delivery.get_by_text('Entrega local verificada', exact=True)).to_be_visible()
    receipts = list(service.delivery_root.rglob('delivery.json'))
    assert len(receipts) == 1
    receipt = receipts[0]
    original = receipt.read_bytes()
    record = json.loads(original)
    master = service.delivery_root / record['path']
    assert file_sha(master) == record['masterSha256']
    assert master.stat().st_size == record['sizeBytes']
    assert record['masterSha256'] == json.loads(next(iter(evidence.values())))['masterSha256']
    expect(send).to_be_disabled()
    reload_render(page, render, plan, job_id)
    expect(delivery.get_by_text('Entrega local verificada', exact=True)).to_be_visible()
    expect(delivery.get_by_text('Arquivo: master.mp4', exact=False)).to_be_visible()
    delivery.get_by_text('Detalhes técnicos da entrega', exact=True).click()
    expect(delivery.get_by_text(record['deliveredAt'] + ' (UTC)', exact=True)).to_be_visible()
    assert receipt.read_bytes() == original
    assert immutable == {path: file_sha(path) for path in immutable}
    assert evidence == {path: path.read_bytes() for path in evidence}
    assert [(path, file_sha(path)) for path, _ in before[0]] == before[0]
    with closing(sqlite3.connect(servers.settings.database)) as connection:
        rows = connection.execute('SELECT budget_json FROM campaigns ORDER BY id').fetchall()
        rows += connection.execute('SELECT approval_state,approved_by FROM copy_masters ORDER BY id').fetchall()
    assert rows == before[1] and servers.provider.events == []
    master.write_bytes(b'private-corrupt-destination')
    delivery.get_by_role('button', name='Consultar entrega', exact=True).click()
    expect(delivery.get_by_text('Artefato armazenado inválido.', exact=True)).to_be_visible()
    expect(delivery.get_by_text('Entrega local verificada', exact=True)).to_have_count(0)
    expect(send).to_be_disabled()
    expect(page.get_by_text('private-corrupt-destination')).to_have_count(0)
    assert master.read_bytes() == b'private-corrupt-destination' and receipt.read_bytes() == original

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, Route, expect
import pytest

from tests.test_voice_external_import import Transcript, make_audio
from tests.test_voice_service import FakeElevenLabs, _mp3
from tests.test_web_import_e2e import create_campaign, start_local_worker
from tests.test_web_panel_e2e import panel_page as provide_panel_page  # noqa: F401
from tests.web_panel_support import PanelServers
from tests.web_panel_support import panel_heygen_provider  # noqa: F401
from tests.web_panel_support import panel_servers as provide_panel_servers  # noqa: F401

pytest_plugins = ['tests.test_heygen_video_media']


@pytest.fixture
def panel_speech_provider(tmp_path: Path) -> FakeElevenLabs:
    return FakeElevenLabs(_mp3(tmp_path), 'Hook\n\nBody\n\nCTA')


@pytest.fixture
def panel_transcriber() -> Transcript:
    return Transcript('Hook\n\nBody\n\nCTA')


def save_budget(page: Page) -> None:
    page.get_by_label('Moeda da campanha', exact=True).fill('USD')
    page.get_by_label('Limite da campanha (centavos)', exact=True).fill('1000')
    page.get_by_label('Confirmo este orçamento inicial', exact=True).check()
    page.get_by_role('button', name='Configurar orçamento inicial', exact=True).click()
    expect(page.get_by_text('USD · limite 1000 centavos', exact=True)).to_be_visible()


def start_kind(page: Page, kind: str) -> None:
    page.get_by_role('combobox', name='Tipo de worker', exact=True).select_option(kind)
    start_local_worker(page)


def test_generate_voice_from_new_campaign_without_auto_start(
    panel_servers: PanelServers, panel_page: Page, panel_speech_provider: FakeElevenLabs,
) -> None:
    campaign_id = create_campaign(panel_servers, panel_page, 1)
    commands = panel_servers.app.state.commands
    assert commands._speech_provider is panel_speech_provider, 'Never start a voice worker without the fake provider.'
    before_jobs = panel_servers.app.state.queries.list_jobs(campaign_id)
    assert commands.campaigns.get_campaign(campaign_id).budget == {}
    save_budget(panel_page)
    assert panel_servers.app.state.queries.list_jobs(campaign_id) == before_jobs
    assert panel_speech_provider.calls == []
    panel_page.get_by_role('combobox', name='Versão da copy para voz', exact=True).select_option('1')
    for label, value in [('Voice ID', 'voice-test'), ('Model ID', 'model-test'), ('Responsável pela geração', 'tester'), ('Teto desta geração (centavos)', '500')]:
        panel_page.get_by_label(label, exact=True).fill(value)
    panel_page.get_by_label('Autorizo o gasto desta geração', exact=True).check()
    panel_page.get_by_role('button', name='Enfileirar geração de voz', exact=True).click()
    expect(panel_page.get_by_text('voice.generate · queued', exact=True)).to_be_visible()
    assert panel_speech_provider.calls == []
    start_kind(panel_page, 'voice_generate')
    expect(panel_page.get_by_text('voice.generate · completed', exact=True)).to_be_visible(timeout=30000)
    assert len(panel_speech_provider.calls) == 1
    assert panel_speech_provider.calls[0]['text'] == 'Hook\n\nBody\n\nCTA'
    voice = commands.voices.list(campaign_id=campaign_id)[0]
    assert voice.status == 'review_required'
    assert (panel_servers.settings.work_root / str(voice.processed_audio_path)).is_file()
    assert panel_servers.provider.events == []
    posts: list[str] = []
    panel_page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    panel_page.reload()
    expect(panel_page.get_by_role('combobox', name='Voice Master para revisar', exact=True)).to_be_visible()
    with panel_page.expect_response(lambda response: response.url.endswith('/status')):
        pass
    assert posts == []


def test_import_voice_two_workers_then_review_preserves_source(
    panel_servers: PanelServers, panel_page: Page, panel_speech_provider: FakeElevenLabs,
) -> None:
    campaign_id = create_campaign(panel_servers, panel_page, 1)
    assert panel_servers.app.state.commands._transcriber is not None, 'Never run a real transcriber in this suite.'
    source = panel_servers.settings.project_root / 'voice-source.wav'
    make_audio(source)
    original = source.read_bytes()
    panel_page.get_by_role('combobox', name='Versão da copy para voz', exact=True).select_option('1')
    panel_page.get_by_label('Caminho relativo do áudio', exact=True).fill('voice-source.wav')
    panel_page.get_by_label('Confirmo o arquivo e a versão da copy', exact=True).check()
    panel_page.get_by_role('button', name='Enfileirar importação de voz', exact=True).click()
    expect(panel_page.get_by_text('Fase 1: executar operação local (local_operations)', exact=True)).to_be_visible()
    assert panel_servers.app.state.commands.voices.list(campaign_id=campaign_id) == []
    start_kind(panel_page, 'local_operations')
    expect(panel_page.get_by_text('voice.import · queued', exact=True)).to_be_visible(timeout=15000)
    voice = panel_servers.app.state.commands.voices.list(campaign_id=campaign_id)[0]
    assert voice.status == 'pending'
    start_kind(panel_page, 'voice_import')
    expect(panel_page.get_by_text('voice.import · completed', exact=True)).to_be_visible(timeout=30000)
    panel_page.get_by_role('combobox', name='Voice Master para revisar', exact=True).select_option(voice.voice_master_id)
    expect(panel_page.get_by_text(f'Revisando {voice.voice_master_id} · copy v1 · imported · review_required', exact=True)).to_be_visible(timeout=15000)
    panel_page.get_by_label('Responsável pela revisão de voz', exact=True).fill('tester')
    panel_page.get_by_label('Ouvi o WAV processado e revisei os resultados', exact=True).check()
    panel_page.get_by_role('button', name='Enfileirar aprovação de voz', exact=True).click()
    expect(panel_page.get_by_text('Review enfileirado, não é aprovação. Inicie local_operations e consulte a voz persistida.', exact=True)).to_be_visible()
    assert panel_servers.app.state.commands.voices.get(voice.voice_master_id).status == 'review_required'
    start_kind(panel_page, 'local_operations')
    expect(panel_page.get_by_text(f'Estado observado: {voice.voice_master_id} · approved; não confirma autoria do comando.', exact=True)).to_be_visible(timeout=15000)
    assert panel_servers.app.state.commands.voices.get(voice.voice_master_id).status == 'approved'
    assert source.read_bytes() == original
    assert panel_speech_provider.calls == []
    assert panel_servers.provider.events == []


def test_budget_saved_with_response_503_is_observed_without_repost(panel_servers: PanelServers, panel_page: Page) -> None:
    campaign_id = create_campaign(panel_servers, panel_page, 1)
    posts: list[str] = []

    def lost_response(route: Route) -> None:
        if route.request.method != 'POST':
            route.continue_()
            return
        posts.append(route.request.url)
        response = route.fetch()
        assert response.status == 200
        route.fulfill(status=503, content_type='application/json', body='{"error":{"code":"internal_error"}}')

    panel_page.route(f'**/campaigns/{campaign_id}/budget', lost_response)
    save_budget(panel_page)
    expect(panel_page.get_by_text('Configuração solicitada observada. Isso não autoriza geração nem informa autoria.', exact=True)).to_be_visible()
    assert len(posts) == 1
    assert panel_servers.app.state.commands.campaigns.get_campaign(campaign_id).budget == {'currency': 'USD', 'limitCents': 1000}
    assert panel_servers.provider.events == []

from __future__ import annotations

from pathlib import Path

from tests.api_helpers import create_ready_api_fixture, database_dump
from tests.test_api_http import client_for

pytest_plugins = ['tests.test_heygen_video_media']


def test_campaign_image_and_processed_voice_are_verified_media(tmp_path: Path, mp4: bytes) -> None:
    settings, _ = create_ready_api_fixture(tmp_path, mp4)
    before = database_dump(settings.database)
    with client_for(settings) as client:
        image = client.get('/api/v1/campaigns/campaign-one/images').json()['items'][0]['items'][0]
        voice = client.get('/api/v1/campaigns/campaign-one/voices').json()['items'][0]
        image_url = f"/api/v1/campaigns/campaign-one/images/{image['imageCandidateId']}/media/{image['sha256']}"
        voice_url = f"/api/v1/campaigns/campaign-one/voices/{voice['voiceMasterId']}/media/{voice['processedSha256']}"
        preview = client.get(image_url)
        audio = client.get(voice_url)
        assert preview.status_code == 200
        assert preview.content == (settings.work_root / image['sourcePath']).read_bytes()
        assert preview.headers['content-type'] == 'image/png'
        assert audio.status_code == 200
        assert audio.content == (settings.work_root / voice['processedAudioPath']).read_bytes()
        assert audio.headers['content-type'] == 'audio/wav'
        assert preview.headers['cache-control'] == audio.headers['cache-control'] == 'no-store'
        assert client.get(image_url.replace('campaign-one', 'campaign-other')).status_code == 404
        assert client.get(voice_url.replace('campaign-one', 'campaign-other')).status_code == 404
        assert client.get(image_url[:-64] + '0' * 64).status_code == 409
        assert client.get(voice_url[:-64] + '0' * 64).status_code == 409
    assert database_dump(settings.database) == before


def test_changed_image_and_voice_bytes_are_not_served(tmp_path: Path, mp4: bytes) -> None:
    settings, _ = create_ready_api_fixture(tmp_path, mp4)
    with client_for(settings) as client:
        image = client.get('/api/v1/campaigns/campaign-one/images').json()['items'][0]['items'][0]
        voice = client.get('/api/v1/campaigns/campaign-one/voices').json()['items'][0]
        (settings.work_root / image['sourcePath']).write_bytes(b'private-image-secret')
        (settings.work_root / voice['processedAudioPath']).write_bytes(b'private-audio-secret')
        image_url = f"/api/v1/campaigns/campaign-one/images/{image['imageCandidateId']}/media/{image['sha256']}"
        voice_url = f"/api/v1/campaigns/campaign-one/voices/{voice['voiceMasterId']}/media/{voice['processedSha256']}"
        for url in (image_url, voice_url):
            response = client.get(url)
            assert response.status_code == 409
            assert b'private' not in response.content

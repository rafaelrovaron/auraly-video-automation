from __future__ import annotations

import io
from pathlib import Path
import wave

from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.service import HeyGenService
from tests.heygen_support import create_ready_campaign


def ready_video_campaign(database: Path, root: Path, provider: FakeHeyGenProvider) -> None:
    content=io.BytesIO()
    with wave.open(content,'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        audio.writeframes(b'\0\0'*44100)
    create_ready_campaign(database,root,duplicate_first_two_images=True,voice_content=content.getvalue())
    service=HeyGenService.for_database(database,root,provider)
    submitted=service.submit_assets(service.plan_assets('campaign-one'))
    assert submitted.job is not None
    service._jobs.worker_once('assets-test')
    service.close()

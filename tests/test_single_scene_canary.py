from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.service import HeyGenService, HeyGenServiceError
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig
from auraly_pipeline.heygen.video_handler import validate_video_item
from auraly_pipeline.heygen.video_service import HeyGenVideoService
from auraly_pipeline.images.import_batch import ImageImportResult, ImageImportService
from auraly_pipeline.voices.domain import VoiceImportRequest
from auraly_pipeline.voices.import_audio import VoiceImportService
from auraly_pipeline.voices.service import VoiceMasterService
from tests.test_campaign_domain import valid_campaign_data
from tests.test_voice_external_import import Transcript, make_audio


def test_one_scene_imports_assets_and_reserves_only_one_render(tmp_path: Path) -> None:
    database = tmp_path / "test.db"
    project = tmp_path / "project"
    project.mkdir()
    root = project / "work"
    data = valid_campaign_data()
    data["campaignId"] = "canary"
    data["sceneVariants"] = data["sceneVariants"][:1]
    campaigns = CampaignService.for_database(database)
    try:
        campaign = campaigns.create_campaign(CampaignCreate.model_validate(data))
        campaign_id = campaign.campaign_id
    finally:
        campaigns.close()
    campaigns = CampaignService.for_database(database)
    try:
        assert len(campaigns.get_campaign(campaign_id).scene_variants) == 1
    finally:
        campaigns.close()

    images = ImageImportService.for_database(database, work_root=root)
    try:
        prepared = images.prepare_directory(campaign_id, project / "incoming")
        assert prepared.variant_count == 1
        image = prepared.images_path / "avatar.png"
        Image.new("RGB", (360, 640), (20, 40, 80)).save(image)
        payload = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
        payload["items"][0]["path"] = "images/avatar.png"
        payload["approveImported"] = True
        payload["approvedBy"] = "tester"
        prepared.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        imported = images.import_batch(prepared.manifest_path)
        assert isinstance(imported, ImageImportResult)
        assert imported.created == imported.approved == 1
        replay = images.import_batch(prepared.manifest_path)
        assert isinstance(replay, ImageImportResult) and replay.created == 0
    finally:
        images.close()

    source = project / "voice.wav"
    make_audio(source)
    imports = VoiceImportService.for_database(
        database,
        project_root=project,
        work_root=root,
        transcriber=Transcript(campaign.copy_masters[0].spoken_text),
    )
    voices = VoiceMasterService.for_database(database, work_root=root)
    provider = FakeHeyGenProvider()
    assets = HeyGenService.for_database(database, root, provider=provider)
    videos = HeyGenVideoService.for_database(database, work_root=root, provider=provider)
    try:
        submitted = imports.import_audio(VoiceImportRequest(campaign_id=campaign_id), source=source)
        worked = imports.worker_once("single-scene", campaign_id=campaign_id)
        assert worked is not None and worked.status == "completed"
        with pytest.raises(HeyGenServiceError, match="approved Voice Master"):
            assets.plan_assets(campaign_id)
        voices.approve(submitted.voice_master.voice_master_id, approved_by="tester")
        plan = assets.plan_assets(campaign_id)
        assert len(plan.sources) == 2
        uploaded = assets.submit_assets(plan)
        assert uploaded.job is not None
        worked = assets._jobs.worker_once(
            "single-upload", campaign_id=campaign_id, job_type="heygen.asset.upload"
        )
        assert worked is not None and worked.status == "completed"
        config = HeyGenVideoConfig(concurrency=1)
        video_plan = videos.plan_videos(campaign_id, config, max_paid_renders=1)
        assert video_plan.new_count == len(video_plan.items) == 1
        changed = video_plan.items[0].model_copy(
            update={"duration_seconds": video_plan.items[0].duration_seconds + 1}
        )
        with pytest.raises(ValueError, match="processed WAV duration changed"):
            validate_video_item(videos._factory, root, changed)
        renders = videos.submit_videos(
            campaign_id, config, max_paid_renders=1, approved_by="tester"
        )
        render_replay = videos.submit_videos(
            campaign_id, config, max_paid_renders=1, approved_by="tester"
        )
        assert (
            len(renders) == len(render_replay) == 1
            and renders[0].render_id == render_replay[0].render_id
        )
        assert "create_video" not in provider.events
    finally:
        videos.close()
        assets.close()
        voices.close()
        imports.close()

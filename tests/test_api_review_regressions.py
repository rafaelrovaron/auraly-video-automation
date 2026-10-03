from __future__ import annotations

from datetime import datetime, timedelta, UTC
import json
from pathlib import Path
import sqlite3
from typing import Any, cast

from fastapi import FastAPI
import pytest

from auraly_pipeline.api.contracts import ApiSettings
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.jobs.handlers import SimulatedWorkerCrash
from tests.api_helpers import create_api_fixture, create_ready_api_fixture
from tests.test_api_heygen_actions import heygen_fixture, video_request
from tests.test_api_http import client_for
from tests.test_api_operations import commands, count_images, request
from tests.test_campaign_domain import valid_campaign_data
from tests.test_image_import_batch import _manifest

pytest_plugins = ["tests.test_heygen_video_media"]


@pytest.mark.parametrize("missing_checkpoint", [False, True])
def test_http_checkpoint_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  missing_checkpoint: bool) -> None:
    import auraly_pipeline.api.app as module
    service, provider = heygen_fixture(tmp_path)
    native = service.heygen_videos.submit_videos

    def crash(*args: Any, **kwargs: Any) -> Any:
        native(*args, **kwargs)
        raise SimulatedWorkerCrash

    monkeypatch.setattr(service.heygen_videos, "submit_videos", crash)
    monkeypatch.setattr(module, "ApiCommands", lambda *args: service)
    prefix = "/api/v1/campaigns/campaign-one"
    data = video_request().model_dump(mode="json", by_alias=True)
    with client_for(service.settings) as client:
        app = cast(FastAPI, client.app)

        def run() -> None:
            assert client.post(prefix + "/worker/start", json={"campaignId": "campaign-one", "kind": "local_operations"}).status_code == 202
            app.state.worker._future.result(timeout=30)

        queued = client.post(prefix + "/heygen/videos/submit", json=data)
        assert queued.status_code == 202
        job_id = queued.json()["jobId"]
        run()
        checkpoint = service.jobs.get_job(job_id).output
        assert len(checkpoint["renders"]) == 3
        monkeypatch.setattr(service.jobs, "_clock", lambda: datetime.now(UTC) + timedelta(hours=1))
        run()  # Public scoped worker performs stale recovery.
        assert client.get(prefix + "/operations/" + job_id).json()["status"] == "failed"
        before = list(provider.events)
        if missing_checkpoint:
            with sqlite3.connect(service.settings.database) as connection:
                connection.execute("UPDATE jobs SET output_json=NULL WHERE id=?", (job_id,))
        resumed = client.post(prefix + f"/jobs/{job_id}/resume", json={"campaignId": "campaign-one"})
        if missing_checkpoint:
            assert resumed.status_code == 409
            assert service.jobs.get_job(job_id).status == "failed"
            assert provider.events == before
            return
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "completed"
        run()
        view = client.get(prefix + "/operations/" + job_id).json()
        assert view["result"] == checkpoint
        assert client.post(prefix + "/heygen/videos/submit", json=data).json()["jobId"] == job_id
        assert provider.events == before and "create_video" not in provider.events
        job = service.jobs.get_job(job_id)
        assert job.attempt_count == job.max_attempts == 1
        assert any(event.event_type == "job.checkpoint_recovered" for event in job.events)


def test_external_db_edit_plan(tmp_path: Path, mp4: bytes) -> None:
    project = tmp_path / "p"
    project.mkdir()
    settings, edit = create_ready_api_fixture(project, mp4)
    external = tmp_path / "external.db"
    with sqlite3.connect(settings.database) as source, sqlite3.connect(external) as target:
        source.backup(target)
    service = commands(ApiSettings(project, settings.work_root, external))
    operation = request(operation="edit_plan", campaignId="campaign-one",
                        request=edit.model_dump(mode="json", by_alias=True), persist=True)
    queued = service.submit_operation(operation)
    service.jobs.worker_once("test", campaign_id="campaign-one", job_type="api.local.operation")
    view = service.get_operation("campaign-one", queued.job_id)
    assert view.status == "completed", view
    assert service.editing.get_plan("campaign-one", view.result.plan.video_id,
                                    view.result.plan.plan_hash) == view.result.plan


def test_manifest_snapshot_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    other = valid_campaign_data()
    other.update(campaignId="campaign-two", sceneVariants=other["sceneVariants"][:1])
    service.campaigns.create_campaign(CampaignCreate.model_validate(other))
    manifest = _manifest(tmp_path / "manual", "campaign-one", ["laundromat"])
    queued = service.submit_operation(request(operation="image_import", campaignId="campaign-one",
                                             manifestPath=str(manifest), mode="execute"))
    original = service.images.plan

    def swap(path: Path, **kwargs: Any) -> Any:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["campaignId"] = "campaign-two"
        path.write_text(json.dumps(data), encoding="utf-8")
        return original(path, **kwargs)

    monkeypatch.setattr(service.images, "plan", swap)
    service.jobs.worker_once("test", campaign_id="campaign-one", job_type="api.local.operation")
    assert service.get_operation("campaign-one", queued.job_id).status == "failed"
    assert count_images(settings.database) == 0

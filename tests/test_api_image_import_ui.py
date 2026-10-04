from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI
import pytest

from tests.api_helpers import create_api_fixture
from tests.test_api_http import client_for
from tests.test_api_operations import commands


def test_manifest_operation_enqueues_without_writing_and_replays(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    campaign = service.campaigns.get_campaign("campaign-one")
    service.images.prepare_directory("campaign-one", settings.work_root / "inbox")
    with client_for(settings) as client:
        relative = (settings.work_root / "inbox").relative_to(settings.project_root).as_posix()
        body = {"campaignId": "campaign-one", "directoryPath": relative,
                "items": [{"variantId": campaign.scene_variants[0].variant_id, "path": "images/a.png"}]}
        url = "/api/v1/campaigns/campaign-one/images/import/manifests"
        response = client.post(url, json=body)
        assert response.status_code == 202, response.text
        job_id = response.json()["jobId"]
        assert list((settings.work_root / "inbox").glob("image-import-*.json")) == []
        app = cast(FastAPI, client.app)
        job = app.state.commands.jobs.get_job(job_id)
        assert job.max_attempts == 1 and job.retry_safety == "manual_only"
        app.state.commands.jobs.worker_once("test-worker", campaign_id="campaign-one", job_type="api.local.operation")
        view = client.get(f"/api/v1/campaigns/campaign-one/operations/{job_id}").json()
        assert view["status"] == "completed"
        assert view["result"]["operation"] == "image_manifest"
        assert view["result"]["manifestPath"].startswith(relative + "/image-import-")
        assert str(tmp_path) not in str(view)
        assert client.post(url, json=body).json()["jobId"] == job_id
        assert client.post(url, json=body | {"campaignId": "other"}).status_code == 422
    service._engine.dispose()


@pytest.mark.parametrize("directory", ["../outside", "C:/outside", "/outside", "other-root/inbox"])
def test_manifest_submission_rejects_untrusted_directory(tmp_path: Path, directory: str) -> None:
    settings = create_api_fixture(tmp_path)
    with client_for(settings) as client:
        response = client.post("/api/v1/campaigns/campaign-one/images/import/manifests", json={
            "campaignId": "campaign-one", "directoryPath": directory,
            "items": [{"variantId": "laundromat", "path": "images/a.png"}],
        })
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert client.get("/api/v1/campaigns/campaign-one/jobs").json()["items"] == []

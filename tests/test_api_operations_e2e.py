from __future__ import annotations

from functools import partial
import json
from pathlib import Path
import shutil
from typing import Any, cast

from fastapi import FastAPI
import httpx
from PIL import Image
import pytest

from auraly_pipeline.api.commands import ApiCommands
from auraly_pipeline.api.contracts import ApiSettings
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.video_handler import HeyGenVideoHandler
from tests.api_helpers import create_api_fixture
from tests.editing_helpers import file_sha, profile_data
from tests.test_api_http import client_for
from tests.test_campaign_domain import valid_campaign_data
from tests.test_voice_external_import import make_audio, Transcript

pytest_plugins = ["tests.test_heygen_video_media"]


def test_http_campaign_ab_flow(
    tmp_path: Path, mp4: bytes, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import auraly_pipeline.api.app as module
    import auraly_pipeline.api.commands as composition
    settings = create_api_fixture(tmp_path)
    # Keep deterministic artifacts below legacy Windows MAX_PATH limits.
    settings = ApiSettings(tmp_path, tmp_path / "w", tmp_path / "api.db")
    provider = FakeHeyGenProvider()
    campaign = valid_campaign_data()
    campaign["campaignId"] = "campaign-e2e"
    text = "\n\n".join(campaign["copyMaster"][field] for field in ("hook", "body", "cta"))
    monkeypatch.setattr(module, "ApiCommands", partial(ApiCommands, heygen_provider=provider,
                                                       transcriber=Transcript(text)))
    # Replace only the external download transport; all services/Jobs/QC remain real.
    monkeypatch.setattr(composition, "HeyGenVideoHandler", partial(
        HeyGenVideoHandler, client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=mp4)),
        ),
    ))
    source = tmp_path / "voice.wav"
    make_audio(source)
    original_voice = source.read_bytes()
    prefix = "/api/v1/campaigns/campaign-e2e"
    with client_for(settings) as client:
        app = cast(FastAPI, client.app)

        def post(suffix: str, body: dict[str, Any]) -> dict[str, Any]:
            response = client.post(prefix + suffix, json={"campaignId": "campaign-e2e"} | body)
            assert response.status_code == 202, response.text
            return cast(dict[str, Any], response.json())

        def run(kind: str) -> None:
            post("/worker/start", {"kind": kind})
            app.state.worker._future.result(timeout=30)
            state = client.get(prefix + "/worker").json()
            assert state["state"] == "idle" and state["errorCode"] is None

        def operation(job_id: str) -> dict[str, Any]:
            response = client.get(prefix + "/operations/" + job_id)
            assert response.status_code == 200
            view = response.json()
            assert view["status"] == "completed", view
            return cast(dict[str, Any], view["result"])

        assert client.post("/api/v1/campaigns", json=campaign).status_code == 201
        assert client.post(prefix + "/copies", json=campaign["copyMaster"]).status_code == 201
        prepared = post("/images/import/prepare", {"outputPath": "inbox"})
        assert not (settings.work_root / "inbox").exists()
        assert client.get(prefix + "/operations/" + prepared["jobId"]).json()["status"] == "queued"
        run("local_operations")
        manifest_path = tmp_path / operation(prepared["jobId"])["manifestPath"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # The operator places images manually; no watcher/import is triggered.
        for index, item in enumerate(manifest["items"]):
            item["path"] = f"images/{item['variantId']}.png"
            destination = manifest_path.parent / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1080, 1920), (index * 70, 40, 90)).save(destination)
        manifest.update(approveImported=True, approvedBy="tester")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert all(not item["items"] for item in client.get(prefix + "/images").json()["items"])
        dry = post("/images/import", {"manifestPath": str(manifest_path), "mode": "dry_run"})
        run("local_operations")
        assert operation(dry["jobId"])["created"] == 0
        imported = post("/images/import", {"manifestPath": str(manifest_path), "mode": "execute"})
        run("local_operations")
        assert operation(imported["jobId"])["created"] == len(campaign["sceneVariants"])
        voice_import = post("/voices/import", {"sourcePath": "voice.wav", "requestId": "voice-1",
                                                "request": {"campaignId": "campaign-e2e"}})
        run("local_operations")
        voice_id = operation(voice_import["jobId"])["voiceMasterId"]
        assert client.get(prefix + "/voices").json()["items"][0]["status"] == "pending"
        run("voice_import")
        assert client.get(prefix + "/voices").json()["items"][0]["status"] == "review_required"
        review = post(f"/voices/{voice_id}/review", {"voiceId": voice_id, "action": "approve", "actor": "tester"})
        run("local_operations")
        assert operation(review["jobId"])["status"] == "approved"
        assets = post("/heygen/assets/prepare", {"requestId": "assets-1"})
        run("local_operations")
        assert operation(assets["jobId"])["uploadCount"] == len(campaign["sceneVariants"]) + 1
        assert "allocate" not in provider.events
        run("heygen_assets")
        config = {"resolution": "720p"}
        preview = post("/heygen/videos/plan", {"requestId": "plan-1", "config": config, "maxPaidRenders": 3})
        run("local_operations")
        assert operation(preview["jobId"])["reservedCount"] == 0
        reserved = post("/heygen/videos/submit", {"requestId": "submit-1", "config": config,
                                                  "maxPaidRenders": 3, "approvedBy": "tester"})
        run("local_operations")
        assert len(operation(reserved["jobId"])["renders"]) == 3
        assert "create_video" not in provider.events
        run("heygen_videos")
        renders = client.get(prefix + "/heygen/renders").json()["items"]
        assert len(renders) == 3 and {r["status"] for r in renders} == {"ready"}
        assert provider.events.count("create_video") == 3
        assert all((settings.work_root / render["source"]["path"]).is_file() for render in renders)
        profile = profile_data()
        font = next((path for path in (Path("C:/Windows/Fonts/arial.ttf"),
                                      Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
                     if path.is_file()), None)
        if font is not None:
            shutil.copyfile(font, tmp_path / "font.ttf")
            profile["defaults"] = {"headline": {"enabled": True, "font": {"path": "font.ttf", "sha256": file_sha(tmp_path / "font.ttf")}}}
        published = client.post("/api/v1/editing/profiles", json=profile)
        assert published.status_code == 201
        reference = {"profileId": "plain", "version": 1, "hash": published.json()["profileHash"]}
        edit = {"campaignId": "campaign-e2e", "renderId": renders[0]["renderId"], "videoId": "video-one",
                "profileRef": reference, "headlineText": "Headline A", "maxOutputs": 2,
                "variants": [{"key": "a", "label": "A"},
                             {"key": "b", "label": "B", "overrides": {"headline": {"text": "Headline B"}}}]}
        before = list(provider.events)
        planned = post("/editing/plans", {"request": edit, "persist": False})
        run("local_operations")
        dry_plan = operation(planned["jobId"])["plan"]
        assert client.get(prefix + "/editing/plans").json()["items"] == []
        persisted = post("/editing/plans", {"request": edit, "persist": True})
        run("local_operations")
        plan = operation(persisted["jobId"])["plan"]
        assert plan == dry_plan and plan["outputCount"] == 2
        assert [output["manifest"]["headline"]["text"] for output in plan["outputs"]] == ["Headline A", "Headline B"]
        assert plan["voiceRef"]["id"] == voice_id and plan["copyRef"]["version"] == 2
        assert all(output["manifest"]["source"] == plan["source"] for output in plan["outputs"])
        assert len(client.get(prefix + "/voices").json()["items"]) == 1
        assert provider.events == before and source.read_bytes() == original_voice
        assert client.get(prefix + f"/editing/plans/video-one/{plan['planHash']}").json() == plan

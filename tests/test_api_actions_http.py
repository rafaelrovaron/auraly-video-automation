from __future__ import annotations

from pathlib import Path
import sqlite3
from threading import Event, Thread
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.editing.service import EditingService
from tests.api_helpers import create_api_fixture, create_ready_api_fixture, database_dump
from tests.editing_helpers import profile_data
from tests.test_api_http import client_for
from tests.test_campaign_domain import valid_campaign_data
from tests.test_image_import_batch import _manifest
from tests.test_voice_service import _authorized_request

pytest_plugins = ["tests.test_heygen_video_media"]
PREFIX = "/api/v1/campaigns/campaign-one"


def test_action_openapi_has_all_20_posts_and_typed_results(tmp_path: Path) -> None:
    with client_for(create_api_fixture(tmp_path)) as client:
        paths = client.get("/openapi.json").json()["paths"]
        posts = {path: data["post"] for path, data in paths.items() if "post" in data}
        assert len(posts) == 20
        for path, endpoint in posts.items():
            status = "201" if path.endswith(("/campaigns", "/copies", "/profiles", "/versions")) else (
                "200" if path.endswith(("/review", "/cancel", "/resume", "/stop"))
                and "/voices/" not in path else "202"
            )
            assert endpoint["responses"][status]["content"]["application/json"]["schema"]
            assert endpoint["requestBody"]["content"]["application/json"]["schema"]
        assert PREFIX.replace("campaign-one", "{campaignId}") + "/worker" in paths
        assert PREFIX.replace("campaign-one", "{campaignId}") + "/operations/{jobId}" in paths


@pytest.mark.parametrize("headers", [
    {"origin": "null"}, {"origin": "https://evil.invalid"},
    {"origin": "http://localhost"}, {"origin": "http://127.0.0.1:8000"},
    {"content-type": "text/plain"},
])
def test_write_boundary_rejects_before_any_side_effect(
    tmp_path: Path, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = create_api_fixture(tmp_path)
    from auraly_pipeline.api.commands import ApiCommands
    from auraly_pipeline.voices.handler import FasterWhisperTranscriber
    before = database_dump(settings.database)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("write boundary invoked command or media work")

    with client_for(settings) as client:
        monkeypatch.setattr(ApiCommands, "submit_operation", forbidden)
        monkeypatch.setattr(FasterWhisperTranscriber, "transcribe", forbidden)
        monkeypatch.setattr(EditingService, "create_profile", forbidden)
        for path, body in [(PREFIX + "/images/import/prepare", {"campaignId": "campaign-one", "outputPath": "inbox"}),
                           ("/api/v1/editing/profiles", profile_data())]:
            response = client.post(path, json=body, headers=headers)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_request"
    assert database_dump(settings.database) == before
    assert not settings.work_root.exists()


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1"])
def test_enqueue_start_poll_and_job_projection(tmp_path: Path, origin: str | None) -> None:
    settings = create_api_fixture(tmp_path)
    with client_for(settings) as client:
        headers = {} if origin is None else {"origin": origin}
        response = client.post(PREFIX + "/images/import/prepare", headers=headers,
                               json={"campaignId": "campaign-one", "outputPath": "inbox"})
        assert response.status_code == 202, response.text
        job_id = response.json()["jobId"]
        operation = PREFIX + "/operations/" + job_id
        assert client.get(operation).json()["status"] == "queued"
        assert not settings.work_root.exists()
        start = client.post(PREFIX + "/worker/start", json={"campaignId": "campaign-one", "kind": "local_operations"})
        assert start.status_code == 202 and start.json()["state"] == "running"
        cast(FastAPI, client.app).state.worker._future.result(timeout=10)
        view = client.get(operation).json()
        assert view["status"] == "completed" and view["result"]["variantCount"] == 1
        assert client.get(PREFIX + "/worker").json()["state"] == "idle"
        stop = client.post(PREFIX + "/worker/stop", json={"campaignId": "campaign-one"})
        assert stop.status_code == 200
        jobs = client.get(PREFIX + "/jobs").json()["items"]
        assert "input" not in jobs[0] and "output" not in jobs[0]
        assert str(tmp_path) not in client.get(operation).text


def test_voice_generation_http_only_enqueues_when_explicitly_authorized(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with sqlite3.connect(settings.database) as connection:
        connection.execute("UPDATE campaigns SET budget_json=?", ('{"currency":"USD","limitCents":1000}',))
    with client_for(settings) as client:
        response = client.post(PREFIX + "/voices/generate", json=_authorized_request("campaign-one").model_dump(mode="json", by_alias=True))
        assert response.status_code == 202, response.text
        assert response.json()["voiceMasterId"]
        assert client.get(PREFIX + "/jobs/" + response.json()["jobId"]).json()["status"] == "queued"
        assert not settings.work_root.exists()


def test_writable_compatibility_error_is_503_without_mutation(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with client_for(settings) as client:
        with sqlite3.connect(settings.database) as connection:
            connection.execute("UPDATE alembic_version SET version_num='old'")
        before = database_dump(settings.database)
        response = client.post(PREFIX + "/images/import/prepare", json={"campaignId": "campaign-one", "outputPath": "inbox"})
        assert response.status_code == 503
        assert database_dump(settings.database) == before


def test_campaign_copy_and_immutable_profile_actions(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with client_for(settings) as client:
        data = valid_campaign_data()
        data["campaignId"] = "campaign-two"
        assert client.post("/api/v1/campaigns", json=data).status_code == 201
        assert client.post("/api/v1/campaigns", json=data).status_code == 409
        copy = client.post(PREFIX + "/copies", json=data["copyMaster"])
        assert copy.status_code == 201 and len(copy.json()["copyMasters"]) == 2
        profile = profile_data()
        assert client.post("/api/v1/editing/profiles", json=profile).status_code == 201
        profile.update(version=2, name="Second")
        assert client.post("/api/v1/editing/profiles/plain/1/versions", json=profile).status_code == 201
        assert client.get("/api/v1/editing/profiles/plain/1").json()["profile"]["name"] != "Second"
        profile.update(version=3)
        assert client.post("/api/v1/editing/profiles/plain/1/versions", json=profile).status_code == 422


def test_owned_queue_and_body_mismatch_fail_before_mutation(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with client_for(settings) as client:
        data = valid_campaign_data()
        data["campaignId"] = "campaign-two"
        client.post("/api/v1/campaigns", json=data)
        job_id = client.post(PREFIX + "/images/import/prepare", json={"campaignId": "campaign-one", "outputPath": "inbox"}).json()["jobId"]
        before = database_dump(settings.database)
        mismatch = client.post(PREFIX + "/images/import/prepare", json={"campaignId": "campaign-two", "outputPath": "other"})
        assert mismatch.status_code == 422
        for suffix in ("/cancel", "/resume"):
            response = client.post(f"/api/v1/campaigns/campaign-two/jobs/{job_id}{suffix}", json={"campaignId": "campaign-two"})
            assert response.status_code == 404
        assert client.get(f"/api/v1/campaigns/campaign-two/operations/{job_id}").status_code == 404
        assert database_dump(settings.database) == before
        cancelled = client.post(PREFIX + f"/jobs/{job_id}/cancel", json={"campaignId": "campaign-one"})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert client.post(PREFIX + f"/jobs/{job_id}/resume", json={"campaignId": "campaign-one"}).status_code == 409


def test_remaining_long_routes_enqueue_without_media_or_provider_work(tmp_path: Path, mp4: bytes) -> None:
    settings, edit = create_ready_api_fixture(tmp_path, mp4)
    manifest = _manifest(tmp_path / "manual", "campaign-one", ["scene-0", "scene-1", "scene-2"])
    with sqlite3.connect(settings.database) as connection:
        connection.execute("UPDATE campaigns SET budget_json=?", ('{"currency":"USD","limitCents":1000}',))
    with client_for(settings) as client:
        render = client.get(PREFIX + "/heygen/renders").json()["items"][0]
        voice = client.get(PREFIX + "/voices").json()["items"][0]
        bodies: list[tuple[str, dict[str, Any]]] = [
            ("/images/import", {"manifestPath": str(manifest), "mode": "dry_run"}),
            ("/voices/import", {"sourcePath": "work/campaigns/campaign-one/voice.wav", "requestId": "voice-1", "request": {"campaignId": "campaign-one"}}),
            (f"/voices/{voice['voiceMasterId']}/review", {"voiceId": voice["voiceMasterId"], "action": "approve", "actor": "tester"}),
            ("/heygen/assets/prepare", {"requestId": "assets-1"}),
            ("/heygen/videos/plan", {"requestId": "plan-1", "config": {}, "maxPaidRenders": 3}),
            ("/heygen/videos/submit", {"requestId": "submit-1", "config": {}, "maxPaidRenders": 3, "approvedBy": "tester"}),
            (f"/heygen/renders/{render['renderId']}/reconcile", {"renderId": render["renderId"], "requestId": "reconcile-1"}),
            ("/editing/plans", {"request": edit.model_dump(mode="json", by_alias=True), "persist": False}),
        ]
        for path, body in bodies:
            response = client.post(PREFIX + path, json={"campaignId": "campaign-one"} | body)
            assert response.status_code == 202, (path, response.text)
            job_id = response.json()["jobId"]
            assert client.get(PREFIX + "/operations/" + job_id).json()["status"] == "queued"
        response = client.post(PREFIX + "/voices/generate", json=_authorized_request("campaign-one").model_dump(mode="json", by_alias=True))
        assert response.status_code == 409  # An approved voice is not silently regenerated.


def test_profile_publication_is_metadata_only_and_resolution_still_checks_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = create_api_fixture(tmp_path)
    profile = profile_data()
    profile["defaults"]["music"] = {"enabled": True, "asset": {"path": "missing.wav", "sha256": "c" * 64}}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("profile request hashed or probed media")

    with client_for(settings) as client:
        monkeypatch.setattr(EditingService, "_audio_duration", forbidden)
        response = client.post("/api/v1/editing/profiles", json=profile)
        assert response.status_code == 201, response.text
    monkeypatch.undo()
    from auraly_pipeline.editing.domain import EditingError, EditResolveRequest
    from auraly_pipeline.editing.resolver import profile_hash
    from tests.editing_helpers import request_data, make_source, file_sha
    editor = EditingService(project_root=tmp_path, work_root=settings.work_root)
    make_source(tmp_path)
    data = request_data(profile_hash(editor.get_profile("plain", 1)))
    data["source"].update(sha256=file_sha(tmp_path / "source.mp4"), durationSec=1)
    data["musicAccepted"] = True
    with pytest.raises(EditingError):
        editor.resolve(EditResolveRequest.model_validate(data))


def test_writable_startup_failure_disposes_reader_without_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import auraly_pipeline.api.app as module
    settings = create_api_fixture(tmp_path)
    disposed: list[Engine] = []
    original = Engine.dispose

    def dispose(engine: Engine, *args: Any, **kwargs: Any) -> None:
        disposed.append(engine)
        original(engine, *args, **kwargs)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("private database")

    monkeypatch.setattr(Engine, "dispose", dispose)
    monkeypatch.setattr(module, "create_existing_sqlite_engine", unavailable, raising=False)
    with pytest.raises(QueryError):
        with client_for(settings):
            pass
    assert len(disposed) == 1


def test_image_review_is_scoped_and_preserves_native_transitions(tmp_path: Path, mp4: bytes) -> None:
    settings, _ = create_ready_api_fixture(tmp_path, mp4)
    with client_for(settings) as client:
        candidate = client.get(PREFIX + "/images").json()["items"][0]["items"][0]
        path = PREFIX + f"/images/{candidate['imageCandidateId']}/review"
        data = valid_campaign_data()
        data["campaignId"] = "campaign-two"
        client.post("/api/v1/campaigns", json=data)
        before = database_dump(settings.database)
        foreign = client.post(path.replace("campaign-one", "campaign-two"),
                              json={"campaignId": "campaign-two", "actor": "tester", "action": "approve"})
        assert foreign.status_code == 404
        assert database_dump(settings.database) == before
        assert client.post(path, json={"campaignId": "campaign-one", "actor": "tester", "action": "approve"}).status_code == 409
        with sqlite3.connect(settings.database) as connection:
            connection.execute("UPDATE image_candidates SET review_status='pending_review', approved_at=NULL, approved_by=NULL WHERE id=?",
                               (candidate["imageCandidateId"],))
        response = client.post(path, json={"campaignId": "campaign-one", "actor": "tester", "action": "approve"})
        assert response.status_code == 200 and response.json()["reviewStatus"] == "approved"


def test_busy_worker_is_409_and_shutdown_drains_before_both_engines_dispose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = create_api_fixture(tmp_path)
    client = client_for(settings)
    entered, release, closing, closed = Event(), Event(), Event(), Event()
    disposed: list[Engine] = []
    dispose = Engine.dispose

    def observe_dispose(engine: Engine, *args: Any, **kwargs: Any) -> None:
        assert release.is_set(), "engine disposed while handler was active"
        disposed.append(engine)
        dispose(engine, *args, **kwargs)

    client.__enter__()
    app = cast(FastAPI, client.app)
    original = app.state.commands.execute_operation

    def holding(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.commands, "execute_operation", holding)
    monkeypatch.setattr(Engine, "dispose", observe_dispose)
    client.post(PREFIX + "/images/import/prepare", json={"campaignId": "campaign-one", "outputPath": "inbox"})
    assert client.post(PREFIX + "/worker/start", json={"campaignId": "campaign-one", "kind": "local_operations"}).status_code == 202
    assert entered.wait(10)
    assert client.post(PREFIX + "/worker/start", json={"campaignId": "campaign-one", "kind": "local_operations"}).status_code == 409

    def close() -> None:
        closing.set()
        client.__exit__(None, None, None)
        closed.set()

    thread = Thread(target=close)
    thread.start()
    try:
        assert closing.wait(10)
        assert not disposed and not closed.is_set()
    finally:
        release.set()
        thread.join(timeout=10)
    assert closed.is_set() and len(disposed) == 2

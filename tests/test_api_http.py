from __future__ import annotations

import importlib
from pathlib import Path
import sqlite3
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine

from auraly_pipeline.api.contracts import ApiSettings, QueryError
from auraly_pipeline.api.queries import ApiQueries
from auraly_pipeline.campaigns.persistence import migrate_database
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.service import JobService
from tests.api_helpers import create_api_fixture, create_ready_api_fixture, database_dump

pytest_plugins = ["tests.test_heygen_video_media"]


def app_for(settings: ApiSettings) -> Any:
    assert importlib.util.find_spec("auraly_pipeline.api.app"), "HTTP app missing"
    return importlib.import_module("auraly_pipeline.api.app").create_app(settings)


def client_for(settings: ApiSettings) -> TestClient:
    return TestClient(app_for(settings), base_url="http://127.0.0.1")


def test_all_spec_get_routes_and_openapi(tmp_path: Path, mp4: bytes) -> None:
    settings, request = create_ready_api_fixture(tmp_path, mp4)
    plan = EditBatchService(project_root=tmp_path, work_root=settings.work_root).plan(
        request, database_path=settings.database)
    with client_for(settings) as client:
        assert client.get("/health").json() == {"status": "ok", "apiVersion": 1}
        campaigns = client.get("/api/v1/campaigns").json()["items"]
        assert campaigns[0]["campaignId"] == "campaign-one"
        jobs = client.get("/api/v1/campaigns/campaign-one/jobs").json()["items"]
        for suffix in ["", "/status", "/images", "/voices", "/heygen/renders", "/jobs",
                       "/jobs/" + jobs[0]["jobId"], "/editing/plans",
                       f"/editing/plans/{request.video_id}/{plan.plan_hash}"]:
            response = client.get("/api/v1/campaigns/campaign-one" + suffix)
            assert response.status_code == 200, (suffix, response.text)
        assert client.get("/api/v1/editing/profiles").json()["items"][0]["profile"]["profileId"] == "plain"
        assert client.get("/api/v1/editing/profiles/plain/1").status_code == 200
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
        assert len(schema["paths"]) == 13
        for path in schema["paths"].values():
            assert set(path) == {"get"}
            assert "422" in path["get"]["responses"]
            assert path["get"]["responses"]["200"]["content"]["application/json"]["schema"]


@pytest.mark.parametrize("path, status", [
    ("/api/v1/campaigns?unknown=private-request-value", 422),
    ("/api/v1/campaigns/INVALID-private-request-value", 422),
    ("/api/v1/campaigns/absent", 404),
    ("/api/v1/editing/profiles/con/1", 422),
    ("/api/v1/editing/profiles/plain/0", 422),
    ("/api/v1/campaigns/campaign-one/jobs/not-a-uuid", 422),
    ("/api/v1/campaigns/campaign-one/editing/plans/video-one/bad-hash", 422),
    ("/api/v1/campaigns/campaign-one/render", 404),
])
def test_http_unknown_and_encoded_inputs_are_sanitized(tmp_path: Path, path: str, status: int) -> None:
    with client_for(create_api_fixture(tmp_path)) as client:
        response = client.get(path)
        assert response.status_code == status
        assert set(response.json()["error"]) == {"code", "message", "field"}
        assert "private-request-value" not in response.text
        assert str(tmp_path) not in response.text
        traversal = client.get("/api/v1/editing/profiles/%2e%2e%2fprivate-request-value/1")
        assert traversal.status_code in {404, 422}
        assert "private-request-value" not in traversal.text


def test_http_foreign_job_is_404(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = JobService.for_database(settings.database)
    try:
        job = service.submit_job(JobSubmit(job_type="fake.success", idempotency_key="foreign-job"))
    finally:
        service.close()
    with client_for(settings) as client:
        assert client.get(f"/api/v1/campaigns/campaign-one/jobs/{job.job_id}").status_code == 404


def test_http_errors_follow_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    settings = create_api_fixture(tmp_path)
    with client_for(settings) as client:
        def unexpected(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("private-request-value")

        monkeypatch.setattr(ApiQueries, "list_campaigns", unexpected)
        response = client.get("/api/v1/campaigns")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "private-request-value" not in response.text + caplog.text
        with sqlite3.connect(settings.database) as connection:
            connection.execute("UPDATE alembic_version SET version_num='invalid'")
        response = client.get("/api/v1/campaigns/campaign-one/jobs")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"


def test_http_mutations_are_405(tmp_path: Path) -> None:
    with client_for(create_api_fixture(tmp_path)) as client:
        for method in ["POST", "PUT", "PATCH", "DELETE"]:
            response = client.request(method, "/api/v1/campaigns")
            assert response.status_code == 405
            assert response.json()["error"]["code"] == "method_not_allowed"


def test_http_host_restricted(tmp_path: Path) -> None:
    with client_for(create_api_fixture(tmp_path)) as client:
        for host in ["evil.example", "www.localhost", "localhost.evil.example"]:
            response = client.get("/health", headers={"host": host})
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "invalid_request"
        assert client.get("/health", headers={"host": "localhost:8000"}).status_code == 200


def test_http_startup_and_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = create_api_fixture(tmp_path)
    disposed: list[Engine] = []
    original = Engine.dispose

    def dispose(engine: Engine, *args: Any, **kwargs: Any) -> None:
        disposed.append(engine)
        original(engine, *args, **kwargs)

    monkeypatch.setattr(Engine, "dispose", dispose)
    with client_for(settings) as client:
        assert client.get("/health").status_code == 200
    assert len(disposed) == 1
    with sqlite3.connect(settings.database) as connection:
        connection.execute("UPDATE alembic_version SET version_num='old'")
    with pytest.raises(QueryError, match="Local storage"):
        with client_for(settings):
            pass
    assert len(disposed) == 2
    missing = tmp_path / "missing" / "private.db"
    with pytest.raises(QueryError) as error:
        with client_for(ApiSettings(tmp_path, settings.work_root, missing)):
            pass
    assert str(tmp_path) not in str(error.value)
    assert not missing.parent.exists()


def test_http_reads_do_not_operate_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command
    from auraly_pipeline.voices.handler import FasterWhisperTranscriber

    settings = create_api_fixture(tmp_path)
    dump = database_dump(settings.database)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("GET operated the pipeline")

    monkeypatch.setattr(command, "upgrade", forbidden)
    monkeypatch.setattr(JobService, "worker_once", forbidden)
    monkeypatch.setattr(FasterWhisperTranscriber, "transcribe", forbidden)
    with client_for(settings) as client:
        for suffix in ["", "/status", "/images", "/voices", "/heygen/renders", "/jobs", "/editing/plans"]:
            assert client.get("/api/v1/campaigns/campaign-one" + suffix).status_code == 200
    assert database_dump(settings.database) == dump


def test_empty_existing_database(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    migrate_database(database)
    with client_for(ApiSettings(tmp_path, tmp_path / "work", database)) as client:
        assert client.get("/api/v1/campaigns").json() == {"items": []}
        assert client.get("/api/v1/editing/profiles").json() == {"items": []}

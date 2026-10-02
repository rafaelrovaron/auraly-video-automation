from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.campaigns.persistence import create_readonly_sqlite_engine
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditProfile
from auraly_pipeline.editing.service import EditingService
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.service import JobService
from tests.api_helpers import create_api_fixture, create_ready_api_fixture, database_dump
from tests.editing_helpers import profile_data

pytest_plugins = ["tests.test_heygen_video_media"]


def queries(settings: Any) -> Any:
    assert importlib.util.find_spec("auraly_pipeline.api.queries"), "query service missing"
    module = importlib.import_module("auraly_pipeline.api.queries")
    return module.ApiQueries(settings, create_readonly_sqlite_engine(settings.database))


def test_query_projections_are_allowlisted(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    payload = queries(settings).get_campaign("campaign-one").model_dump(by_alias=True)
    assert payload["campaignId"] == "campaign-one"
    assert {"budget", "config"}.isdisjoint(payload)
    assert payload["copyMasters"][0]["spokenText"] == (
        "You stopped chasing him.\n\nThat changed the energy between you.\n\n"
        "Take the one-minute reading."
    )


def test_query_reads_live_updates(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = queries(settings)
    assert service.get_campaign("campaign-one").proof_object == "Eight of Cups tarot card"
    with sqlite3.connect(settings.database) as connection:
        connection.execute("UPDATE campaigns SET proof_object='Updated card'")
    assert service.get_campaign("campaign-one").proof_object == "Updated card"


def test_job_ownership_is_enforced(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    jobs = JobService.for_database(settings.database)
    try:
        job = jobs.submit_job(JobSubmit(job_type="fake.success", idempotency_key="api-job"))
    finally:
        jobs.close()
    service = queries(settings)
    with pytest.raises(QueryError) as error:
        service.get_job("campaign-one", job.job_id)
    assert error.value.code == "not_found"
    with pytest.raises(QueryError) as error:
        service.list_jobs("absent")
    assert error.value.code == "not_found"


def test_query_constructors_are_passive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.voices.handler import FasterWhisperTranscriber, VoiceGenerateHandler

    settings = create_api_fixture(tmp_path)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("read executed model, worker or migration")

    monkeypatch.setattr(FasterWhisperTranscriber, "transcribe", forbidden)
    monkeypatch.setattr(VoiceGenerateHandler, "execute", forbidden)
    monkeypatch.setattr(JobService, "worker_once", forbidden)
    service = queries(settings)
    assert service.list_voices("campaign-one") == []
    assert service.list_renders("campaign-one") == []
    assert service.list_jobs("campaign-one") == []
    assert service.list_images("campaign-one")[0].items == []


def test_missing_artifact_not_corruption(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = queries(settings)
    assert service.list_profiles() == []
    assert service.list_plans("campaign-one") == []
    with pytest.raises(QueryError) as error:
        service.get_profile("plain", 1)
    assert error.value.code == "not_found"
    editing = EditingService(project_root=tmp_path, work_root=settings.work_root)
    path = editing.create_profile(EditProfile.model_validate(profile_data()))
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(QueryError) as error:
        service.list_profiles()
    assert error.value.code == "artifact_invalid"
    assert path.read_text() == "{}"


def test_queries_preserve_sql_and_files(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    editing = EditingService(project_root=tmp_path, work_root=settings.work_root)
    path = editing.create_profile(EditProfile.model_validate(profile_data()))
    content, mtime, dump = path.read_bytes(), path.stat().st_mtime_ns, database_dump(settings.database)
    service = queries(settings)
    for _ in range(2):
        service.get_campaign("campaign-one")
        assert service.list_profiles()[0].profile.profile_id == "plain"
        service.list_jobs("campaign-one")
        service.list_images("campaign-one")
        service.list_voices("campaign-one")
        service.list_renders("campaign-one")
    assert database_dump(settings.database) == dump
    assert (path.read_bytes(), path.stat().st_mtime_ns) == (content, mtime)


def test_plan_discovery_is_canonical_and_verified(tmp_path: Path) -> None:
    from auraly_pipeline.editing.domain import EditingError

    service = EditBatchService(project_root=tmp_path, work_root=tmp_path / "work")
    assert callable(getattr(service, "list_plans", None)), "plan discovery missing"
    assert service.list_plans("campaign-one") == []
    path = tmp_path / "work/campaigns/campaign-one/editing/plans/video-one" / ("a" * 64) / "plan.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    with pytest.raises(EditingError):
        service.list_plans("campaign-one")


def test_ready_asset_and_plan_projections(tmp_path: Path, mp4: bytes) -> None:
    settings, request = create_ready_api_fixture(tmp_path, mp4)
    batch = EditBatchService(project_root=tmp_path, work_root=settings.work_root)
    plan = batch.plan(request, database_path=settings.database)
    service = queries(settings)
    dump = database_dump(settings.database)
    render = service.list_renders("campaign-one")[0].model_dump(by_alias=True)
    assert {"item", "accountRef", "imageAssetId", "audioAssetId"}.isdisjoint(render)
    assert render["status"] == "ready"
    voice = service.list_voices("campaign-one")[0].model_dump(by_alias=True)
    assert {"rawAudioPath", "providerRequestId", "transcriptPath", "settingsFingerprint"}.isdisjoint(voice)
    image = service.list_images("campaign-one")[0].items[0].model_dump(by_alias=True)
    assert "importSourcePath" not in image
    job = service.list_jobs("campaign-one")[0].model_dump(by_alias=True)
    assert {"input", "output", "events", "attempts", "workerId", "leaseExpiresAt"}.isdisjoint(job)
    summary = service.list_plans("campaign-one")[0].model_dump(by_alias=True)
    assert "manifest" not in summary["outputs"][0]
    assert service.get_plan("campaign-one", request.video_id, plan.plan_hash) == plan
    assert batch.list_plans("campaign-one") == [plan]
    assert database_dump(settings.database) == dump

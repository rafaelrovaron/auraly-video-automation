from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from typing import Any

import pytest
from pydantic import ValidationError

from auraly_pipeline.api.contracts import ApiSettings, QueryError
from auraly_pipeline.campaigns.persistence import create_existing_sqlite_engine
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.provider import HeyGenProviderFailure
from auraly_pipeline.jobs.handlers import SimulatedWorkerCrash
from auraly_pipeline.jobs.domain import JobSubmit
from tests.heygen_video_support import ready_video_campaign
from tests.heygen_support import create_ready_campaign
from tests.test_api_operations import request


def heygen_fixture(tmp_path: Path, *, uploaded: bool = True) -> tuple[Any, FakeHeyGenProvider]:
    from auraly_pipeline.api.commands import ApiCommands
    provider = FakeHeyGenProvider()
    db, root = tmp_path / "api.db", tmp_path / "work"
    if uploaded:
        ready_video_campaign(db, root, provider)
    else:
        create_ready_campaign(db, root, duplicate_first_two_images=True)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE campaigns SET character='soul-constellation'")
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT * FROM jobs").fetchall():
            native = JobSubmit(
                job_type=row["job_type"], campaign_id=row["campaign_id"],
                scene_variant_id=row["scene_variant_id"], idempotency_key=row["idempotency_key"],
                input=json.loads(row["input_json"]), priority=row["priority"],
                max_attempts=row["max_attempts"], retry_safety=row["retry_safety"],
            )
            connection.execute("UPDATE jobs SET request_fingerprint=? WHERE id=?",
                               (native.request_fingerprint, row["id"]))
    provider.events.clear()
    return ApiCommands(ApiSettings(tmp_path, root, db), create_existing_sqlite_engine(db),
                       heygen_provider=provider), provider


def video_request(operation: str = "heygen_video_submit", **extra: Any) -> Any:
    data = dict(operation=operation, campaignId="campaign-one", requestId="video-test-1",
                config={}, maxPaidRenders=3)
    if operation == "heygen_video_submit":
        data["approvedBy"] = "tester"
    return request(**(data | extra))


def run_operation(service: Any, operation: Any) -> Any:
    submitted = service.submit_operation(operation)
    service.jobs.worker_once("local-worker", campaign_id="campaign-one",
                             job_type="api.local.operation")
    return service.get_operation("campaign-one", submitted.job_id)


def test_heygen_plan_and_reservation_require_explicit_workers_without_dispatch(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path)
    preview = service.submit_operation(video_request("heygen_video_plan"))
    assert provider.events == [] and service.jobs.get_job(preview.job_id).status == "queued"
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    result = service.get_operation("campaign-one", preview.job_id).result
    assert result.new_count == 3 and result.reserved_count == 0
    assert service.heygen_videos.list_videos("campaign-one") == []
    submitted = run_operation(service, video_request())
    assert submitted.status == "completed" and len(submitted.result.renders) == 3
    assert {render.status for render in submitted.result.renders} == {"planned"}
    assert "create_video" not in provider.events
    serialized = submitted.model_dump_json(by_alias=True)
    for private in ("account-fake", "image_asset_id", "audio_asset_id", "https://", str(tmp_path)):
        assert private not in serialized


def test_heygen_submission_approval_and_exact_cap_are_preserved(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path)
    with pytest.raises(ValidationError):
        request(operation="heygen_video_submit", campaignId="campaign-one", requestId="test",
                config={}, maxPaidRenders=3)
    view = run_operation(service, video_request(maxPaidRenders=2))
    assert view.status == "failed" and view.error_code == "operation_not_allowed"
    assert service.heygen_videos.list_videos("campaign-one") == []
    assert run_operation(service, video_request()).status == "completed"
    changed = run_operation(service, video_request(requestId="changed", config={"fit": "contain"}))
    assert changed.status == "failed" and changed.error_code == "operation_not_allowed"
    assert len(service.heygen_videos.list_videos("campaign-one")) == 3
    assert "create_video" not in provider.events


def test_heygen_reservation_checkpoint_replays_without_new_prerequisites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, provider = heygen_fixture(tmp_path)
    submitted = service.submit_operation(video_request())
    videos = service.heygen_videos
    original = videos.submit_videos

    def crash(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise SimulatedWorkerCrash("after reservation before wrapper completion")

    monkeypatch.setattr(videos, "submit_videos", crash)
    with pytest.raises(SimulatedWorkerCrash):
        service.jobs.worker_once("crashing-worker", campaign_id="campaign-one",
                                 job_type="api.local.operation")
    wrapper = service.jobs.get_job(submitted.job_id)
    ids = {r.render_id for r in videos.list_videos("campaign-one")}
    assert len(ids) == 3 and len(wrapper.output["renders"]) == 3
    provider.account_ref = "changed-account"
    result = service.execute_operation(request(**wrapper.input), job_id=submitted.job_id)
    assert {r.render_id for r in result.renders} == ids
    assert len(videos.list_videos("campaign-one")) == 3
    assert "create_video" not in provider.events


def test_asset_wrapper_reuses_native_upload_and_safe_checkpoint(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path, uploaded=False)
    operation = request(operation="heygen_assets", campaignId="campaign-one", requestId="asset-1")
    submitted = service.submit_operation(operation)
    assert provider.events == []
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    result = service.get_operation("campaign-one", submitted.job_id).result
    assert result.upload_count == 3 and result.reused_count == 0 and result.job_id
    assert "allocate" not in provider.events
    assert service.heygen_jobs.get_job(result.job_id).status == "queued"
    service.heygen_jobs.worker_once("assets-worker", campaign_id="campaign-one",
                                    job_type="heygen.asset.upload")
    replay = service.execute_operation(operation, job_id=submitted.job_id)
    assert replay == result and provider.events.count("allocate") == 1
    fresh = run_operation(service, request(operation="heygen_assets", campaignId="campaign-one",
                                           requestId="asset-2"))
    assert fresh.result.upload_count == 0 and fresh.result.reused_count == 3
    assert fresh.result.job_id is None


def test_ambiguous_dispatch_stays_blocked_and_manual_binding_is_explicit(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path)
    reserved = run_operation(service, video_request()).result.renders
    provider.scenario = "ambiguous"
    assert service.heygen_videos.run_videos("campaign-one").blocked_count == 3
    dispatch_count = provider.events.count("create_video")
    assert service.heygen_videos.run_videos("campaign-one").blocked_count == 3
    assert provider.events.count("create_video") == dispatch_count == 3
    view = run_operation(service, request(operation="heygen_reconcile", campaignId="campaign-one",
                                          renderId=reserved[0].render_id, requestId="reconcile-1"))
    assert view.error_code == "operation_not_allowed"
    assert service.heygen_jobs.get_job(reserved[0].job_id).status == "blocked"
    with pytest.raises(QueryError) as absent:
        service.submit_operation(request(operation="heygen_reconcile", campaignId="campaign-one",
                                         renderId="missing", requestId="reconcile-2"))
    assert absent.value.code == "not_found"


def test_oauth_failure_is_blocked_and_sanitized(tmp_path: Path) -> None:
    class DisconnectedProvider(FakeHeyGenProvider):
        def preflight_video(self, config: Any) -> Any:
            raise HeyGenProviderFailure("configuration", "token secret https://private.invalid")

    service, _ = heygen_fixture(tmp_path)
    from auraly_pipeline.api.commands import ApiCommands
    service = ApiCommands(service.settings, create_existing_sqlite_engine(service.settings.database),
                          heygen_provider=DisconnectedProvider())
    view = run_operation(service, video_request("heygen_video_plan"))
    assert view.status == "blocked" and view.error_code == "operation_not_allowed"
    assert "secret" not in view.model_dump_json() and "https://" not in view.model_dump_json()


def test_changed_approved_audio_after_enqueue_fails_without_reservations(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path)
    submitted = service.submit_operation(video_request())
    (service.settings.work_root / "campaigns/campaign-one/voice.wav").write_bytes(b"changed")
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    assert service.get_operation("campaign-one", submitted.job_id).error_code == "operation_not_allowed"
    assert service.heygen_videos.list_videos("campaign-one") == []
    assert "create_video" not in provider.events


def test_asset_checkpoint_survives_crash_before_wrapper_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, provider = heygen_fixture(tmp_path, uploaded=False)
    operation = request(operation="heygen_assets", campaignId="campaign-one", requestId="asset-1")
    submission = service.submit_operation(operation)
    assets = service.heygen_assets
    original = assets.submit_assets

    def crash(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise SimulatedWorkerCrash("after child creation")

    monkeypatch.setattr(assets, "submit_assets", crash)
    with pytest.raises(SimulatedWorkerCrash):
        service.jobs.worker_once("crash-worker", campaign_id="campaign-one", job_type="api.local.operation")
    wrapper = service.jobs.get_job(submission.job_id)
    assert wrapper.output["jobId"]
    (service.settings.work_root / "campaigns/campaign-one/voice.wav").write_bytes(b"changed")
    replay = service.execute_operation(operation, job_id=submission.job_id)
    assert replay.job_id == wrapper.output["jobId"]
    assert len([j for j in service.jobs.list_jobs(campaign_id="campaign-one")
                if j.job_type == "heygen.asset.upload"]) == 1
    assert "allocate" not in provider.events


def test_manual_binding_requires_confirmation_before_native_resume(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path)
    renders = run_operation(service, video_request()).result.renders
    provider.scenario = "ambiguous"
    service.heygen_videos.run_videos("campaign-one")
    provider.scenario = "success"
    from auraly_pipeline.heygen.video_repository import HeyGenVideoRepository
    from sqlalchemy.orm import Session, sessionmaker
    repo = HeyGenVideoRepository(sessionmaker(create_existing_sqlite_engine(service.settings.database),
                                            class_=Session, expire_on_commit=False))
    render = repo.get(renders[0].render_id)
    remote_id = provider.create_video(render.item, callback_id=render.logical_key)
    unconfirmed = run_operation(service, request(
        operation="heygen_reconcile", campaignId="campaign-one", renderId=render.render_id,
        requestId="binding-1", videoId=remote_id,
    ))
    assert unconfirmed.error_code == "operation_not_allowed"
    assert service.heygen_jobs.get_job(render.job_id).status == "blocked"
    confirmed = run_operation(service, request(
        operation="heygen_reconcile", campaignId="campaign-one", renderId=render.render_id,
        requestId="binding-2", videoId=remote_id, confirmManualBinding=True,
    ))
    assert confirmed.status == "completed"
    assert confirmed.result.render.manual_binding is True
    assert confirmed.result.render.remote_video_id == remote_id
    assert service.heygen_jobs.get_job(render.job_id).status == "queued"
    assert provider.events.count("create_video") == 4  # 3 ambiguous + fixture's manual provider render


def test_reconciliation_checkpoint_survives_native_resume_then_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, provider = heygen_fixture(tmp_path)
    renders = run_operation(service, video_request()).result.renders
    provider.account_ref = "different-account"
    assert service.heygen_videos.run_videos("campaign-one").blocked_count == 3
    provider.account_ref = "account-fake"
    operation = request(operation="heygen_reconcile", campaignId="campaign-one",
                        renderId=renders[0].render_id, requestId="reconcile-crash")
    submitted = service.submit_operation(operation)
    original = service.heygen_videos.reconcile_video

    def crash(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise SimulatedWorkerCrash("after native resume")

    monkeypatch.setattr(service.heygen_videos, "reconcile_video", crash)
    with pytest.raises(SimulatedWorkerCrash):
        service.jobs.worker_once("crash-worker", campaign_id="campaign-one", job_type="api.local.operation")
    wrapper = service.jobs.get_job(submitted.job_id)
    assert wrapper.output["render"]["renderId"] == renders[0].render_id
    result = service.execute_operation(operation, job_id=submitted.job_id)
    assert result.render.render_id == renders[0].render_id
    assert service.heygen_jobs.get_job(result.render.job_id).status == "queued"
    assert "create_video" not in provider.events


def test_reservation_rolls_back_if_wrapper_has_no_active_lease(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path)
    operation = video_request()
    submitted = service.submit_operation(operation)
    with pytest.raises(QueryError) as inactive:
        service.execute_operation(operation, job_id=submitted.job_id)
    assert inactive.value.code == "operation_not_allowed"
    assert service.heygen_videos.list_videos("campaign-one") == []
    assert not [j for j in service.jobs.list_jobs(campaign_id="campaign-one")
                if j.job_type == "heygen.video.generate"]
    assert "create_video" not in provider.events


def test_default_adapter_is_not_constructed_for_startup_or_enqueue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = heygen_fixture(tmp_path)
    import auraly_pipeline.api.commands as module

    def forbidden() -> Any:
        raise AssertionError("OAuth adapter constructed before explicit worker execution")

    monkeypatch.setattr(module, "HeyGenMcpAdapter", forbidden)
    commands = module.ApiCommands(service.settings, create_existing_sqlite_engine(service.settings.database))
    submission = commands.submit_operation(video_request("heygen_video_plan"))
    assert commands.jobs.get_job(submission.job_id).status == "queued"

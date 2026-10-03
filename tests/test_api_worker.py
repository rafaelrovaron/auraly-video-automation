from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import Barrier, Event, Thread
from typing import Any

import pytest

from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.jobs.domain import JobSubmit, RetrySafety
from tests.api_helpers import create_api_fixture
from tests.test_api_operations import commands, request
from tests.test_campaign_domain import valid_campaign_data
from tests.test_api_heygen_actions import heygen_fixture, run_operation, video_request


def runner(service: Any) -> Any:
    from auraly_pipeline.api.worker import LocalApiWorker
    return LocalApiWorker(service)


def enqueue(service: Any, name: str, campaign: str = "campaign-one") -> Any:
    return service.submit_operation(request(operation="image_prepare", campaignId=campaign,
                                             outputPath=name))


def test_concurrent_starts_admit_one_and_stop_preserves_active_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = commands(create_api_fixture(tmp_path))
    first, second = enqueue(service, "first"), enqueue(service, "second")
    entered, release, heartbeat = Event(), Event(), Event()
    original = service.execute_operation
    renew = service.jobs.renew_lease
    work = service.jobs.worker_once

    def holding(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    def observe_renew(*args: Any, **kwargs: Any) -> Any:
        result = renew(*args, **kwargs)
        heartbeat.set()
        return result

    def short_lease(*args: Any, **kwargs: Any) -> Any:
        return work(*args, lease_seconds=2, heartbeat_interval_seconds=0.1, **kwargs)

    monkeypatch.setattr(service, "execute_operation", holding)
    monkeypatch.setattr(service.jobs, "renew_lease", observe_renew)
    monkeypatch.setattr(service.jobs, "worker_once", short_lease)
    local = runner(service)
    barrier = Barrier(2)

    def start() -> str:
        barrier.wait()
        try:
            return local.start("campaign-one", "local_operations").state
        except QueryError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            states = list(executor.map(lambda _: start(), range(2)))
        assert sorted(states) == ["operation_conflict", "running"]
        assert entered.wait(10)
        assert local.stop("campaign-one").state == "stopping"
        assert heartbeat.wait(10)
        assert service.jobs.get_job(first.job_id).status == "running"
        assert service.jobs.get_job(second.job_id).attempt_count == 0
    finally:
        release.set()
        local.shutdown()
    assert service.jobs.get_job(first.job_id).status == "completed"
    assert service.jobs.get_job(second.job_id).status == "queued"
    assert local.status("campaign-one").state == "idle"
    restarted = runner(service)
    try:
        assert restarted.status("campaign-one").state == "idle"
        assert service.jobs.get_job(second.job_id).attempt_count == 0
    finally:
        restarted.shutdown()


def test_selected_campaign_and_kind_leave_other_jobs_unchanged(tmp_path: Path) -> None:
    service = commands(create_api_fixture(tmp_path))
    data = valid_campaign_data()
    data["campaignId"] = "campaign-two"
    service.campaigns.create_campaign(CampaignCreate.model_validate(data))
    selected = enqueue(service, "selected")
    other = enqueue(service, "other", "campaign-two")
    voice = service.jobs.submit_job(JobSubmit(
        campaign_id="campaign-one", job_type="voice.import", input={"voiceMasterId": "not-run"},
        idempotency_key="untouched-voice", retry_safety=RetrySafety.MANUAL_ONLY,
    ))
    before_other, before_voice = service.jobs.get_job(other.job_id), service.jobs.get_job(voice.job_id)
    local = runner(service)
    local.start("campaign-one", "local_operations")
    # Wait for natural drain, not stop: completion is a test-only Future observation.
    local._future.result(timeout=10)
    assert local.status("campaign-one").state == "idle"
    assert service.jobs.get_job(selected.job_id).status == "completed"
    assert service.jobs.get_job(other.job_id) == before_other
    assert service.jobs.get_job(voice.job_id) == before_voice
    local.shutdown()


def test_future_retry_exits_without_busy_polling(tmp_path: Path) -> None:
    service = commands(create_api_fixture(tmp_path))
    future = enqueue(service, "future")
    with sqlite3.connect(service.settings.database) as connection:
        connection.execute("UPDATE jobs SET status='retry_scheduled', next_retry_at=? WHERE id=?",
                           ("2099-01-01T00:00:00+00:00", future.job_id))
    local = runner(service)
    local.start("campaign-one", "local_operations")
    local._future.result(timeout=10)
    assert local.status("campaign-one").state == "idle"
    assert service.jobs.get_job(future.job_id).attempt_count == 0
    assert not service.settings.work_root.exists()
    local.shutdown()


def test_foreign_scope_invalid_kind_and_shutdown_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = commands(create_api_fixture(tmp_path))
    data = valid_campaign_data()
    data["campaignId"] = "campaign-two"
    service.campaigns.create_campaign(CampaignCreate.model_validate(data))
    enqueue(service, "hold")
    entered, release = Event(), Event()
    original = service.execute_operation

    def holding(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "execute_operation", holding)
    local = runner(service)
    try:
        with pytest.raises(QueryError) as invalid:
            local.start("campaign-one", "arbitrary-handler")
        assert invalid.value.code == "invalid_request"
        local.start("campaign-one", "local_operations")
        assert entered.wait(10)
        for action in (local.status, local.stop):
            with pytest.raises(QueryError) as foreign:
                action("campaign-two")
            assert foreign.value.code == "not_found"
    finally:
        release.set()
        local.shutdown()
    with pytest.raises(QueryError) as closed:
        local.start("campaign-one", "local_operations")
    assert closed.value.code == "operation_conflict"


def test_shutdown_waits_for_handler_before_returning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = commands(create_api_fixture(tmp_path))
    job = enqueue(service, "hold")
    entered, release, shutdown_started, shutdown_done = Event(), Event(), Event(), Event()
    original = service.execute_operation

    def holding(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        assert not shutdown_done.is_set()
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "execute_operation", holding)
    local = runner(service)
    local.start("campaign-one", "local_operations")
    assert entered.wait(10)

    def shutdown() -> None:
        shutdown_started.set()
        local.shutdown()
        shutdown_done.set()

    thread = Thread(target=shutdown)
    thread.start()
    try:
        assert shutdown_started.wait(10)
        assert not shutdown_done.is_set()
        assert service.jobs.get_job(job.job_id).status == "running"
    finally:
        release.set()
        thread.join(timeout=10)
    assert shutdown_done.is_set()
    assert service.jobs.get_job(job.job_id).status == "completed"


def test_stop_during_heygen_polling_keeps_active_checkpoint_and_skips_next_claim(tmp_path: Path) -> None:
    service, _ = heygen_fixture(tmp_path)
    entered, release = Event(), Event()

    class PollingProvider(FakeHeyGenProvider):
        def get_video(self, video_id: str) -> Any:
            entered.set()
            assert release.wait(10)
            return super().get_video(video_id)

    from auraly_pipeline.api.commands import ApiCommands
    from auraly_pipeline.campaigns.persistence import create_existing_sqlite_engine
    provider = PollingProvider(scenario="terminal")
    service = ApiCommands(service.settings, create_existing_sqlite_engine(service.settings.database),
                          heygen_provider=provider)
    renders = run_operation(service, video_request(config={"concurrency": 1})).result.renders
    local = runner(service)
    try:
        local.start("campaign-one", "heygen_videos")
        assert entered.wait(10)
        assert local.stop("campaign-one").state == "stopping"
        active = service.heygen_videos.list_videos("campaign-one")
        assert sum(r.remote_video_id is not None for r in active) == 1
        assert provider.events.count("create_video") == 1
    finally:
        release.set()
        local.shutdown()
    assert sum(service.heygen_jobs.get_job(r.job_id).attempt_count == 0 for r in renders) == 2
    assert provider.events.count("create_video") == 1


def test_runner_failure_has_static_error_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = commands(create_api_fixture(tmp_path))
    enqueue(service, "not-run")

    def failure(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private token https://private.invalid")

    monkeypatch.setattr(service.jobs, "worker_once", failure)
    local = runner(service)
    local.start("campaign-one", "local_operations")
    local._future.result(timeout=10)
    state = local.status("campaign-one")
    assert state.state == "idle" and state.error_code == "internal_error"
    assert "private" not in state.model_dump_json()
    local.shutdown()


def test_restarting_video_runner_does_not_repeat_ambiguous_dispatch(tmp_path: Path) -> None:
    service, provider = heygen_fixture(tmp_path)
    run_operation(service, video_request())
    provider.scenario = "ambiguous"
    local = runner(service)
    try:
        local.start("campaign-one", "heygen_videos")
        local._future.result(timeout=10)
        assert provider.events.count("create_video") == 3
        assert {r.status for r in service.heygen_videos.list_videos("campaign-one")} == {"reconciliation_required"}
        local.start("campaign-one", "heygen_videos")
        local._future.result(timeout=10)
        assert provider.events.count("create_video") == 3
        assert local.status("campaign-one").error_code is None
    finally:
        local.shutdown()

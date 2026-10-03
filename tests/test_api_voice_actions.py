from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import pytest
from pydantic import ValidationError

from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import create_existing_sqlite_engine
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.jobs.handlers import SimulatedWorkerCrash
from tests.api_helpers import create_api_fixture
from tests.test_api_operations import commands, request
from tests.test_campaign_domain import valid_campaign_data
from tests.test_voice_external_import import make_audio, Transcript
from tests.test_voice_service import _authorized_request, FakeElevenLabs


def voice_fixture(tmp_path: Path) -> tuple[Any, Any, Path]:
    settings = create_api_fixture(tmp_path)
    source = tmp_path / "voice.wav"
    make_audio(source)
    campaign_service = CampaignService(create_existing_sqlite_engine(settings.database))
    campaign = campaign_service.get_campaign("campaign-one")
    campaign_service.close()
    from auraly_pipeline.api.commands import ApiCommands
    service = ApiCommands(settings, create_existing_sqlite_engine(settings.database),
                          transcriber=Transcript(campaign.copy_masters[0].spoken_text))
    return settings, service, source


def import_request(source: Path) -> Any:
    return request(operation="voice_import", campaignId="campaign-one",
                   sourcePath=str(source), requestId="manual-test-1",
                   request={"campaignId": "campaign-one"})


def test_voice_generation_submission_does_not_execute_provider(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with sqlite3.connect(settings.database) as connection:
        connection.execute(
            "UPDATE campaigns SET budget_json=?",
            ('{"currency":"USD","limitCents":1000}',),
        )
    from auraly_pipeline.api.commands import ApiCommands
    provider = FakeElevenLabs(b"not-used", "not-used")
    service = ApiCommands(settings, create_existing_sqlite_engine(settings.database),
                          speech_provider=provider)
    submission = service.submit_voice(_authorized_request("campaign-one"))
    assert submission.operation == "voice_generate"
    assert submission.voice_master_id is not None
    assert service.jobs.get_job(submission.job_id).status == "queued"
    assert provider.calls == []
    assert not settings.work_root.exists()


def test_voice_import_queues_copy_then_processes_and_reviews_in_workers(tmp_path: Path) -> None:
    settings, service, source = voice_fixture(tmp_path)
    submission = service.submit_operation(import_request(source))
    assert not list(settings.work_root.glob("campaigns/*/voice/*/raw/*"))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    view = service.get_operation("campaign-one", submission.job_id)
    assert view.status == "completed"
    child = view.result
    assert child.operation == "voice_import"
    assert service.jobs.get_job(child.job_id).status == "queued"
    assert len(list(settings.work_root.glob("campaigns/*/voice/*/raw/*"))) == 1
    service.jobs.worker_once("voice-worker", campaign_id="campaign-one", job_type="voice.import")
    voice = service.voices.get(child.voice_master_id)
    assert voice.status == "review_required" and voice.sample_rate == 48000
    assert voice.channels == 1 and voice.provider == "imported"
    review = service.submit_operation(request(
        operation="voice_review", campaignId="campaign-one", voiceId=child.voice_master_id,
        action="approve", actor="tester",
    ))
    assert service.voices.get(child.voice_master_id).status == "review_required"
    service.jobs.worker_once("review-worker", campaign_id="campaign-one", job_type="api.local.operation")
    assert service.get_operation("campaign-one", review.job_id).status == "completed"
    assert service.voices.get(child.voice_master_id).status == "approved"
    source.write_bytes(b"changed original source")
    assert service.submit_operation(import_request(source)).job_id == submission.job_id
    assert service.get_operation("campaign-one", submission.job_id).result == child


def test_new_import_wrapper_reuses_existing_domain_child_atomically(tmp_path: Path) -> None:
    settings, service, source = voice_fixture(tmp_path)
    first = service.submit_operation(import_request(source))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    original = service.get_operation("campaign-one", first.job_id).result
    second = service.submit_operation(request(
        operation="voice_import", campaignId="campaign-one", sourcePath=str(source),
        requestId="manual-test-2", request={"campaignId": "campaign-one"},
    ))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    replay = service.get_operation("campaign-one", second.job_id)
    assert replay.status == "completed" and replay.result == original
    with sqlite3.connect(settings.database) as connection:
        assert connection.execute("SELECT count(*) FROM voice_masters").fetchone()[0] == 1
    assert len(list(settings.work_root.glob("campaigns/*/voice/*/raw/*"))) == 1


def test_voice_approval_wrapper_rejects_changed_processed_artifact(tmp_path: Path) -> None:
    settings, service, source = voice_fixture(tmp_path)
    submitted = service.submit_operation(import_request(source))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    child = service.get_operation("campaign-one", submitted.job_id).result
    service.jobs.worker_once("voice-worker", campaign_id="campaign-one", job_type="voice.import")
    voice = service.voices.get(child.voice_master_id)
    (settings.work_root / voice.processed_audio_path).write_bytes(b"changed processed artifact")
    review = service.submit_operation(request(
        operation="voice_review", campaignId="campaign-one", voiceId=child.voice_master_id,
        action="approve", actor="tester",
    ))
    service.jobs.worker_once("review-worker", campaign_id="campaign-one", job_type="api.local.operation")
    assert service.get_operation("campaign-one", review.job_id).error_code == "operation_not_allowed"
    assert service.voices.get(child.voice_master_id).status == "review_required"


def test_import_replay_after_checkpoint_ignores_changed_original_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, source = voice_fixture(tmp_path)
    submission = service.submit_operation(import_request(source))
    original_import = service.voice_import.import_audio

    def crash_after_submission(*args: Any, **kwargs: Any) -> Any:
        original_import(*args, **kwargs)
        raise SimulatedWorkerCrash("crash after durable child")

    monkeypatch.setattr(service.voice_import, "import_audio", crash_after_submission)
    with pytest.raises(SimulatedWorkerCrash):
        service.jobs.worker_once("crashing-worker", campaign_id="campaign-one",
                                 job_type="api.local.operation")
    wrapper = service.jobs.get_job(submission.job_id)
    assert wrapper.output["jobId"]
    source.write_bytes(b"changed original source")
    # Simulate resuming the handler after its durable submission checkpoint.
    result = service.execute_operation(
        request(**wrapper.input), job_id=submission.job_id,
    )
    assert result.job_id == wrapper.output["jobId"]
    assert result.voice_master_id == wrapper.output["voiceMasterId"]
    with sqlite3.connect(settings.database) as connection:
        assert connection.execute("SELECT count(*) FROM voice_masters").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM jobs WHERE job_type='voice.import'").fetchone()[0] == 1
    assert len(list(settings.work_root.glob("campaigns/*/voice/*/raw/*"))) == 1


def test_voice_review_validation_and_qc_gate_remain_enforced(tmp_path: Path) -> None:
    settings, service, source = voice_fixture(tmp_path)
    with pytest.raises(ValidationError):
        request(operation="voice_review", campaignId="campaign-one",
                voiceId="missing", action="approve")
    imported = service.submit_operation(import_request(source))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    child = service.get_operation("campaign-one", imported.job_id).result
    data = valid_campaign_data()
    data["campaignId"] = "campaign-two"
    service.campaigns.create_campaign(CampaignCreate.model_validate(data))
    with pytest.raises(QueryError) as foreign:
        service.submit_operation(request(
            operation="voice_review", campaignId="campaign-two",
            voiceId=child.voice_master_id, action="approve", actor="tester",
        ))
    assert foreign.value.code == "not_found"
    with pytest.raises(QueryError) as missing:
        service.submit_operation(request(
            operation="voice_review", campaignId="campaign-one",
            voiceId="missing", action="approve", actor="tester",
        ))
    assert missing.value.code == "not_found"
    review = service.submit_operation(request(
        operation="voice_review", campaignId="campaign-one", voiceId=child.voice_master_id,
        action="approve", actor="tester",
    ))
    service.jobs.worker_once("review-worker", campaign_id="campaign-one", job_type="api.local.operation")
    view = service.get_operation("campaign-one", review.job_id)
    assert view.status == "failed" and view.error_code == "operation_not_allowed"
    assert service.voices.get(child.voice_master_id).status == "pending"


def test_voice_import_outside_root_and_body_campaign_mismatch_rejected(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    with pytest.raises(QueryError):
        service.submit_operation(import_request(tmp_path.parent / "foreign.wav"))
    with pytest.raises(QueryError):
        service.submit_operation(request(
            operation="voice_import", campaignId="campaign-one", sourcePath="voice.wav",
            requestId="manual-test-1", request={"campaignId": "campaign-two"},
        ))
    assert service.jobs.list_jobs(campaign_id="campaign-one") == []

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.jobs.domain import (
    JobExecutionOutcome,
    JobExecutionResult,
    JobStatus,
    RetrySafety,
)
from auraly_pipeline.voices.db_models import VoiceMasterRow
from auraly_pipeline.voices.domain import VoiceGenerateRequest, VoiceMasterStatus
from auraly_pipeline.voices.provider import SpeechGeneration
from auraly_pipeline.voices.service import VoiceMasterConflictError, VoiceMasterService
from tests.test_campaign_domain import valid_campaign_data


class FakeElevenLabs:
    def __init__(self, audio: bytes, transcript: str) -> None:
        self.audio = audio
        self.transcript = transcript
        self.calls: list[dict[str, object]] = []

    def generate_speech(self, **kwargs) -> SpeechGeneration:
        self.calls.append(kwargs)
        duration = 0.9
        step = duration / max(len(self.transcript), 1)
        return SpeechGeneration(
            audio=self.audio,
            aligned_text=self.transcript,
            alignment={
                "characters": list(self.transcript),
                "character_start_times_seconds": [
                    index * step for index in range(len(self.transcript))
                ],
                "character_end_times_seconds": [
                    (index + 1) * step for index in range(len(self.transcript))
                ],
            },
            request_id="request-123",
            output_format="mp3_44100_128",
        )


class FakeTranscriber:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path) -> str:
        self.calls.append(audio_path)
        return self.transcript


class OutOfBoundsAlignmentElevenLabs(FakeElevenLabs):
    def generate_speech(self, **kwargs) -> SpeechGeneration:
        generated = super().generate_speech(**kwargs)
        assert generated.alignment is not None
        generated.alignment["character_start_times_seconds"] = [999.0] * len(self.transcript)
        generated.alignment["character_end_times_seconds"] = [1000.0] * len(self.transcript)
        return generated


def _mp3(tmp_path: Path) -> bytes:
    path = tmp_path / "source.mp3"
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1.2",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    return path.read_bytes()


def _campaign(database: Path) -> tuple[str, str, str]:
    data = valid_campaign_data()
    data["campaignId"] = "voice-pilot"
    data["voicePreset"] = "soul-constellation"
    data["budget"]["limitCents"] = 1000
    service = CampaignService.for_database(database)
    campaign = service.create_campaign(CampaignCreate.model_validate(data))
    service.close()
    copy = campaign.copy_masters[0]
    return campaign.campaign_id, copy.copy_master_id, copy.spoken_text


def test_paid_voice_request_requires_explicit_authorization_and_positive_budget(
    tmp_path: Path,
) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    with pytest.raises(VoiceMasterConflictError):
        service.generate(
            VoiceGenerateRequest(
                campaign_id=campaign_id,
                voice_id="voice-explicit",
                model_id="eleven_multilingual_v2",
            )
        )
    with pytest.raises(VoiceMasterConflictError):
        service.generate(
            VoiceGenerateRequest(
                campaign_id=campaign_id,
                voice_id="voice-explicit",
                model_id="eleven_multilingual_v2",
                paid_request_approved=True,
                paid_request_approved_by="rafael",
                approved_budget_cents=1001,
            )
        )
    service.close()


def test_paid_authorization_uses_campaign_currency(tmp_path: Path) -> None:
    database = tmp_path / "eur.db"
    data = valid_campaign_data()
    data["campaignId"] = "voice-eur"
    data["budget"]["limitCents"] = 1000
    data["budget"]["currency"] = "EUR"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(data))
    campaigns.close()
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    submitted = service.generate(
        VoiceGenerateRequest(
            campaign_id=campaign.campaign_id,
            voice_id="voice-explicit",
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )
    )
    event = next(
        item
        for item in service._jobs.get_job(submitted.job.job_id).events
        if item.event_type == "job.paid_authorized"
    )
    assert event.metadata["currency"] == "EUR"
    service.close()


def test_voice_master_and_paid_job_creation_roll_back_atomically_on_interruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-explicit",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    original_create = service._jobs._repository.create_in_session

    def fail_creation(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected transaction interruption")

    service._jobs._repository.create_in_session = fail_creation  # type: ignore[assignment,method-assign]
    with pytest.raises(RuntimeError):
        service.generate(request)
    service._jobs._repository.create_in_session = original_create  # type: ignore[method-assign]
    assert service.list(campaign_id=campaign_id) == []
    assert service._jobs.list_jobs(campaign_id=campaign_id) == []

    repaired = service.generate(request)
    persisted_job = service._jobs.get_job(repaired.job.job_id)
    paid_events = [
        event for event in persisted_job.events if event.event_type == "job.paid_authorized"
    ]
    assert len(paid_events) == 1
    assert paid_events[0].metadata["campaignBudgetLimitCents"] == 1000
    assert paid_events[0].metadata["currency"] == "USD"
    assert persisted_job.input == {"voiceMasterId": repaired.voice_master.voice_master_id}
    assert persisted_job.campaign_id == repaired.voice_master.campaign_id
    assert persisted_job.status is JobStatus.QUEUED
    worked = service.worker_once("voice-worker")
    assert worked is not None
    assert worked.last_error_code != "voice_job_integrity_failed"
    assert (
        service.get(repaired.voice_master.voice_master_id).voice_master_id
        == repaired.voice_master.voice_master_id
    )
    service.close()


def test_ambiguous_no_artifact_reconciliation_resumes_same_job(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-explicit",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    submission = service.generate(request)
    with service._sessions() as session:
        row = session.get(VoiceMasterRow, submission.voice_master.voice_master_id)
        assert row is not None
        row.status = "generating"
        row.provider_state = "ambiguous"
        session.commit()
    claimed = service._jobs.claim_next_job("worker")
    assert claimed is not None
    blocked = service._jobs._repository.finish_claim(
        claimed.job_id,
        "worker",
        claimed.attempt_count,
        JobExecutionResult(
            outcome=JobExecutionOutcome.BLOCKED,
            error_code="voice_reconciliation_required",
            error_message="Voice Master provider reconciliation is required.",
        ),
        datetime.now(UTC),
        30,
    )
    assert blocked.status == JobStatus.BLOCKED.value
    resolved = service.resolve_ambiguous_without_artifact(
        submission.voice_master.voice_master_id,
        resolved_by="rafael",
        reason="Provider dashboard confirms no artifact.",
    )
    assert resolved.status is VoiceMasterStatus.PENDING
    assert service._jobs.get_job(submission.job.job_id).status is JobStatus.QUEUED
    service.close()


def test_dispatching_raw_without_persisted_digest_is_not_adopted(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    submitted = service.generate(
        VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id="voice-explicit",
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )
    )
    voice_id = submitted.voice_master.voice_master_id
    raw = (
        tmp_path / "work" / "campaigns" / campaign_id / "voice" / voice_id / "raw" / "provider.mp3"
    )
    raw.parent.mkdir(parents=True)
    raw.write_bytes(_mp3(tmp_path))
    with service._sessions() as session:
        row = session.get(VoiceMasterRow, voice_id)
        assert row is not None
        row.status = "generating"
        row.provider_state = "dispatching"
        row.raw_sha256 = None
        session.commit()
    job = service.worker_once("voice-worker")
    assert job is not None
    assert job.status is JobStatus.BLOCKED
    assert provider.calls == []
    voice = service.get(voice_id)
    assert voice.raw_sha256 is None
    assert voice.status is VoiceMasterStatus.GENERATING
    service.close()


def test_database_rejects_cross_logical_voice_job_link(tmp_path: Path) -> None:
    database = tmp_path / "cross-link.db"
    campaign_a, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    request = VoiceGenerateRequest(
        campaign_id=campaign_a,
        voice_id="voice-a",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    voice_a = service.generate(request)
    voice_b = service.generate(request.model_copy(update={"voice_id": "voice-b"}))
    engine = service._engine
    with pytest.raises(IntegrityError, match="matching voice generation Job"):
        with engine.begin() as connection:
            connection.execute(text("PRAGMA recursive_triggers=OFF"))
            connection.execute(
                text("UPDATE voice_masters SET job_id=:job WHERE id=:voice"),
                {
                    "job": voice_b.job.job_id,
                    "voice": voice_a.voice_master.voice_master_id,
                },
            )
    service.close()


def test_database_rejects_mutating_linked_job_semantics(tmp_path: Path) -> None:
    database = tmp_path / "job-mutation.db"
    campaign_id, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    submitted = service.generate(
        VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id="voice-a",
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )
    )
    with pytest.raises(IntegrityError, match="linked VoiceMaster"):
        with service._engine.begin() as connection:
            connection.execute(text("PRAGMA recursive_triggers=OFF"))
            connection.execute(
                text(
                    "UPDATE jobs SET input_json=json_object('voiceMasterId','different-voice') "
                    "WHERE id=:job"
                ),
                {"job": submitted.job.job_id},
            )
    service.close()


def test_reconciliation_is_idempotently_resumable_after_split_commit(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-explicit",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    submission = service.generate(request)
    with service._sessions() as session:
        row = session.get(VoiceMasterRow, submission.voice_master.voice_master_id)
        assert row is not None
        row.status = "generating"
        row.provider_state = "ambiguous"
        session.commit()
    claimed = service._jobs.claim_next_job("worker")
    assert claimed is not None
    service._jobs._repository.finish_claim(
        claimed.job_id,
        "worker",
        claimed.attempt_count,
        JobExecutionResult(
            outcome=JobExecutionOutcome.BLOCKED,
            error_code="voice_reconciliation_required",
            error_message="Voice Master provider reconciliation is required.",
        ),
        datetime.now(UTC),
        30,
    )
    original_resume = service._jobs.resume_reconciled_job

    def interrupted_resume(job_id: str) -> object:
        raise RuntimeError("injected split-commit interruption")

    service._jobs.resume_reconciled_job = interrupted_resume  # type: ignore[assignment,method-assign]
    with pytest.raises(RuntimeError):
        service.resolve_ambiguous_without_artifact(
            submission.voice_master.voice_master_id,
            resolved_by="rafael",
            reason="Provider dashboard confirms no artifact.",
        )
    service._jobs.resume_reconciled_job = original_resume  # type: ignore[method-assign]
    persisted = service.get(submission.voice_master.voice_master_id)
    assert persisted.status is VoiceMasterStatus.PENDING
    assert service._jobs.get_job(submission.job.job_id).status is JobStatus.BLOCKED
    resumed = service.resolve_ambiguous_without_artifact(
        submission.voice_master.voice_master_id,
        resolved_by="rafael",
        reason="Retry after local interruption.",
    )
    assert resumed.status is VoiceMasterStatus.PENDING
    job = service._jobs.get_job(submission.job.job_id)
    assert job.status is JobStatus.QUEUED
    audit_events = [event for event in job.events if event.event_type == "job.provider_reconciled"]
    assert len(audit_events) == 1
    assert audit_events[0].metadata["resolvedBy"] == "rafael"
    service.close()


def test_regeneration_is_blocked_until_prior_generation_is_final(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-explicit",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    service.generate(request)
    with pytest.raises(VoiceMasterConflictError):
        service.regenerate(request)
    service.close()


def test_voice_generate_is_idempotent_campaign_level_and_uses_exact_spoken_text(
    tmp_path: Path,
) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, copy_id, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-explicit",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )

    first = service.generate(request)
    duplicate = service.generate(request)

    assert first.voice_master.voice_master_id == duplicate.voice_master.voice_master_id
    assert first.job.job_id == duplicate.job.job_id
    assert first.job.retry_safety is RetrySafety.RECONCILE_BEFORE_RETRY
    assert first.job.scene_variant_id is None
    assert first.voice_master.copy_master_id == copy_id
    persisted_job = service._jobs.get_job(first.job.job_id)
    paid_events = [
        event for event in persisted_job.events if event.event_type == "job.paid_authorized"
    ]
    assert len(paid_events) == 1
    assert paid_events[0].metadata["approvedBy"] == "rafael"
    assert paid_events[0].metadata["approvedBudgetCents"] == 1000
    assert provider.calls == []

    completed = service.worker_once("voice-worker")
    assert completed is not None and completed.status.value == "completed"
    assert len(provider.calls) == 1
    assert provider.calls[0]["text"] == spoken_text
    assert valid_campaign_data()["copyMaster"]["headline"] not in str(provider.calls[0]["text"])
    voice = service.get(first.voice_master.voice_master_id)
    assert voice.status is VoiceMasterStatus.REVIEW_REQUIRED
    assert voice.raw_audio_path != voice.processed_audio_path
    assert voice.raw_sha256 and voice.processed_sha256
    assert voice.sample_rate == 48000
    assert voice.channels == 1
    assert voice.loudness_lufs is not None
    assert voice.true_peak_dbfs is not None
    assert voice.wpm is not None
    assert voice.transcript_match_status is not None
    assert voice.transcript_match_status.value == "matched"
    assert voice.headline_spoken is False
    assert voice.raw_audio_path is not None
    assert voice.processed_audio_path is not None
    raw = tmp_path / "work" / voice.raw_audio_path
    processed = tmp_path / "work" / voice.processed_audio_path
    assert raw.read_bytes() == provider.audio
    assert processed.is_file()
    service.close()


def test_out_of_bounds_alignment_falls_back_to_local_transcript(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = OutOfBoundsAlignmentElevenLabs(_mp3(tmp_path), "WRONG")
    transcriber = FakeTranscriber(spoken_text)
    service = VoiceMasterService.for_database(
        database,
        work_root=tmp_path / "work",
        provider=provider,
        transcriber=transcriber,
    )
    submitted = service.generate(
        VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id="voice-explicit",
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )
    )
    service.worker_once("voice-worker")
    voice = service.get(submitted.voice_master.voice_master_id)
    assert transcriber.calls
    assert voice.transcript_source is not None
    assert voice.transcript_source.value == "faster_whisper"
    assert voice.transcript_match_status is not None
    assert voice.transcript_match_status.value == "matched"
    service.close()


def test_restart_persists_review_and_human_approval_is_campaign_retrievable(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    first = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    submitted = first.generate(
        VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id="voice-explicit",
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )
    )
    first.worker_once("voice-worker")
    first.close()

    restarted = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    approved = restarted.approve(submitted.voice_master.voice_master_id, approved_by="rafael")
    assert approved.status is VoiceMasterStatus.APPROVED
    assert approved.approved_at and approved.approved_by == "rafael"
    assert restarted.approved_for_campaign(campaign_id).voice_master_id == approved.voice_master_id
    restarted.close()


def test_approval_rejects_tampered_processed_artifact(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    submitted = service.generate(
        VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id="voice-explicit",
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )
    )
    service.worker_once("voice-worker")
    voice = service.get(submitted.voice_master.voice_master_id)
    assert voice.processed_audio_path is not None
    (tmp_path / "work" / voice.processed_audio_path).write_bytes(b"tampered")
    try:
        service.approve(voice.voice_master_id, approved_by="rafael")
    except Exception as exc:
        assert getattr(exc, "public_message", "") == "Voice Master QC does not permit approval."
    else:
        raise AssertionError("tampered VoiceMaster was approved")
    service.close()


def test_approval_rejects_tampered_transcript_or_manifest_artifact(tmp_path: Path) -> None:
    for artifact_field in ("transcript_path", "manifest_path"):
        case = tmp_path / artifact_field
        database = case / "auraly.db"
        campaign_id, _, spoken_text = _campaign(database)
        provider = FakeElevenLabs(_mp3(case), spoken_text)
        service = VoiceMasterService.for_database(
            database, work_root=case / "work", provider=provider
        )
        submitted = service.generate(
            VoiceGenerateRequest(
                campaign_id=campaign_id,
                voice_id="voice-explicit",
                model_id="eleven_multilingual_v2",
                paid_request_approved=True,
                paid_request_approved_by="rafael",
                approved_budget_cents=1000,
            )
        )
        service.worker_once("voice-worker")
        voice = service.get(submitted.voice_master.voice_master_id)
        relative = getattr(voice, artifact_field)
        assert relative is not None
        (case / "work" / relative).write_text("{}", encoding="utf-8")
        with pytest.raises(Exception) as captured:
            service.approve(voice.voice_master_id, approved_by="rafael")
        assert getattr(captured.value, "public_message", "") == (
            "Voice Master QC does not permit approval."
        )
        service.close()


def test_approved_voice_blocks_new_paid_logical_generation_but_exact_replay_reuses(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approved-guard.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-approved",
        model_id="eleven_multilingual_v2",
        voice_settings={"stability": 0.4},
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    submitted = service.generate(request)
    service.worker_once("voice-worker")
    approved = service.approve(submitted.voice_master.voice_master_id, approved_by="rafael")
    assert approved.status is VoiceMasterStatus.APPROVED
    provider_calls = len(provider.calls)
    voice_count = len(service.list(campaign_id=campaign_id))
    job_count = len(service._jobs.list_jobs(campaign_id=campaign_id))
    paid_event_count = sum(
        event.event_type == "job.paid_authorized"
        for job in service._jobs.list_jobs(campaign_id=campaign_id)
        for event in job.events
    )

    replayed = service.generate(request)
    assert replayed.voice_master.voice_master_id == approved.voice_master_id
    assert replayed.job.job_id == submitted.job.job_id

    alternatives = [
        request.model_copy(update={"voice_id": "voice-different"}),
        request.model_copy(update={"model_id": "eleven_turbo_v2_5"}),
        request.model_copy(update={"voice_settings": {"stability": 0.8}}),
    ]
    for different in alternatives:
        with pytest.raises(VoiceMasterConflictError):
            service.generate(different)
    with pytest.raises(VoiceMasterConflictError):
        service.regenerate(request)

    jobs = service._jobs.list_jobs(campaign_id=campaign_id)
    assert len(service.list(campaign_id=campaign_id)) == voice_count
    assert len(jobs) == job_count
    assert (
        sum(event.event_type == "job.paid_authorized" for job in jobs for event in job.events)
        == paid_event_count
    )
    assert len(provider.calls) == provider_calls
    service.close()


def test_approval_and_different_paid_generation_are_serialized(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approval-generation-race.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    setup = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    original_request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-original",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    original = setup.generate(original_request)
    setup.worker_once("voice-worker")
    provider_calls = len(provider.calls)
    setup.close()
    different_request = original_request.model_copy(update={"voice_id": "voice-different"})

    def approve_original() -> str:
        service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
        try:
            service.approve(original.voice_master.voice_master_id, approved_by="rafael")
            return "approved"
        except VoiceMasterConflictError:
            return "conflict"
        finally:
            service.close()

    def generate_different() -> str:
        service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
        try:
            service.generate(different_request)
            return "generated"
        except VoiceMasterConflictError:
            return "conflict"
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        approval_future = executor.submit(approve_original)
        generation_future = executor.submit(generate_different)
        outcomes = {approval_future.result(), generation_future.result()}

    inspected = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    voices = inspected.list(campaign_id=campaign_id)
    jobs = inspected._jobs.list_jobs(campaign_id=campaign_id)
    assert outcomes in ({"approved", "conflict"}, {"generated", "conflict"})
    assert not (
        any(voice.status is VoiceMasterStatus.APPROVED for voice in voices)
        and len(voices) > 1
    )
    assert len(jobs) == len(voices)
    assert len(provider.calls) == provider_calls
    inspected.close()


def test_processing_voice_blocks_other_approval_until_it_is_final(
    tmp_path: Path,
) -> None:
    database = tmp_path / "processing-blocks-approval.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )

    def request(voice_id: str) -> VoiceGenerateRequest:
        return VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id=voice_id,
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )

    voice_a = service.generate(request("voice-a"))
    service.worker_once("voice-worker")
    assert service.get(voice_a.voice_master.voice_master_id).status is VoiceMasterStatus.REVIEW_REQUIRED

    voice_b = service.generate(request("voice-b"))
    with service._sessions() as session:
        row_b = session.get(VoiceMasterRow, voice_b.voice_master.voice_master_id)
        assert row_b is not None
        row_b.status = "processing"
        row_b.provider_state = "response_received"
        row_b.updated_at = datetime.now(UTC)
        session.commit()

    voices_before = [
        voice.model_dump(mode="json") for voice in service.list(campaign_id=campaign_id)
    ]
    jobs_before = [
        job.model_dump(mode="json")
        for job in service._jobs.list_jobs(campaign_id=campaign_id)
    ]

    with pytest.raises(VoiceMasterConflictError) as raised:
        service.approve(voice_a.voice_master.voice_master_id, approved_by="rafael")
    assert raised.value.public_message == (
        "The Voice Master request conflicts with persisted state."
    )
    assert service.get(voice_a.voice_master.voice_master_id).status is VoiceMasterStatus.REVIEW_REQUIRED
    assert [
        voice.model_dump(mode="json") for voice in service.list(campaign_id=campaign_id)
    ] == voices_before
    assert [
        job.model_dump(mode="json")
        for job in service._jobs.list_jobs(campaign_id=campaign_id)
    ] == jobs_before

    with service._sessions() as session:
        row_b = session.get(VoiceMasterRow, voice_b.voice_master.voice_master_id)
        assert row_b is not None
        row_b.status = "failed"
        row_b.failure_code = "processing_failed_safely"
        row_b.updated_at = datetime.now(UTC)
        session.commit()

    approved = service.approve(voice_a.voice_master.voice_master_id, approved_by="rafael")
    assert approved.status is VoiceMasterStatus.APPROVED
    service.close()


def test_approval_with_multiple_other_active_voices_fails_with_stable_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "multiple-active.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )

    def request(voice_id: str) -> VoiceGenerateRequest:
        return VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id=voice_id,
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )

    first = service.generate(request("voice-first"))
    service.generate(request("voice-second"))
    service.generate(request("voice-third"))
    service.worker_once("voice-worker")

    with pytest.raises(VoiceMasterConflictError) as raised:
        service.approve(first.voice_master.voice_master_id, approved_by="rafael")
    assert raised.value.public_message == (
        "The Voice Master request conflicts with persisted state."
    )
    service.close()


def test_failed_previous_voice_master_permits_regeneration(tmp_path: Path) -> None:
    database = tmp_path / "failed-regeneration.db"
    campaign_id, _, _ = _campaign(database)
    service = VoiceMasterService.for_database(database, work_root=tmp_path / "work")
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-explicit",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    original = service.generate(request)
    with service._sessions() as session:
        row = session.get(VoiceMasterRow, original.voice_master.voice_master_id)
        assert row is not None
        row.status = "failed"
        row.failure_code = "provider_failed_safely"
        row.updated_at = datetime.now(UTC)
        session.commit()

    regenerated = service.regenerate(request)
    assert regenerated.voice_master.voice_master_id != original.voice_master.voice_master_id
    assert regenerated.voice_master.generation == 2
    assert len(service.list(campaign_id=campaign_id)) == 2
    service.close()


def test_rejection_preserves_artifacts_and_regeneration_uses_new_record(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, spoken_text = _campaign(database)
    provider = FakeElevenLabs(_mp3(tmp_path), spoken_text)
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    request = VoiceGenerateRequest(
        campaign_id=campaign_id,
        voice_id="voice-explicit",
        model_id="eleven_multilingual_v2",
        paid_request_approved=True,
        paid_request_approved_by="rafael",
        approved_budget_cents=1000,
    )
    first = service.generate(request)
    service.worker_once("worker-one")
    rejected = service.reject(
        first.voice_master.voice_master_id, rejected_by="rafael", reason="Pacing needs revision."
    )
    assert rejected.raw_audio_path is not None
    raw = tmp_path / "work" / rejected.raw_audio_path
    assert raw.is_file()

    regenerated = service.regenerate(request)
    assert regenerated.voice_master.voice_master_id != rejected.voice_master_id
    assert regenerated.voice_master.generation == 2
    assert raw.is_file()
    service.close()


def test_headline_in_alignment_prevents_approval(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, _, spoken_text = _campaign(database)
    headline = valid_campaign_data()["copyMaster"]["headline"]
    provider = FakeElevenLabs(_mp3(tmp_path), f"{headline} {spoken_text}")
    service = VoiceMasterService.for_database(
        database, work_root=tmp_path / "work", provider=provider
    )
    submission = service.generate(
        VoiceGenerateRequest(
            campaign_id=campaign_id,
            voice_id="voice-explicit",
            model_id="eleven_multilingual_v2",
            paid_request_approved=True,
            paid_request_approved_by="rafael",
            approved_budget_cents=1000,
        )
    )
    service.worker_once("worker")
    voice = service.get(submission.voice_master.voice_master_id)
    assert voice.headline_spoken is True
    assert voice.transcript_match_status is not None
    assert voice.transcript_match_status.value == "mismatched"
    try:
        service.approve(voice.voice_master_id, approved_by="rafael")
    except Exception as exc:
        assert getattr(exc, "public_message", "") == "Voice Master QC does not permit approval."
    else:
        raise AssertionError("headline-spoken VoiceMaster was approved")
    service.close()

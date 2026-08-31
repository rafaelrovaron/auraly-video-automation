from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy import select

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.flow.artifacts import inspect_flow_artifact, resolve_flow_final_path
from auraly_pipeline.flow.generation_domain import (
    FlowDispatchAmbiguousError,
    FlowGenerationRuntimeError,
)
from auraly_pipeline.images.db_models import (
    FlowCandidateSlotRow,
    FlowGenerationRunRow,
    ImageCandidateRow,
    ImageGenerationRow,
)
from auraly_pipeline.images.domain import ImageCandidate, ImageGenerateRequest
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.images.service import (
    ImageRecoveryBlockedError,
    ImageService,
    ImageTransitionError,
)
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow
from tests.test_campaign_domain import valid_campaign_data


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
WORKSPACE_PATH = "fx/tools/flow/recovery-workspace"
WORKSPACE_SHA = hashlib.sha256(WORKSPACE_PATH.encode()).hexdigest()


class ReadOnlyRecoveryRuntime:
    """A provider-observation double with no Generate-capable surface."""

    def __init__(self, *, confirms_dispatch: bool = True, failure: Exception | None = None) -> None:
        self.confirms_dispatch = confirms_dispatch
        self.failure = failure
        self.browser_opened = False
        self.generate_click_count = 0
        self.expected_fingerprints: tuple[str, ...] = ()

    def recover_dispatch(
        self,
        _workspace: object,
        *,
        expected_fingerprints: tuple[str, ...] = (),
    ) -> bool:
        self.browser_opened = True
        self.expected_fingerprints = expected_fingerprints
        if self.failure is not None:
            raise self.failure
        return self.confirms_dispatch


class DownloadRecoveryRuntime(ReadOnlyRecoveryRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.downloaded_slots: list[int] = []
        self.final_facts = None
        self.relative_staging: str | None = None

    def download_slot(self, slot_index: int, checkpoint_sink: object):
        assert self.final_facts is not None and self.relative_staging is not None
        fingerprint = checkpoint_sink.candidate_fingerprint(slot_index)
        checkpoint_sink.record_download_intent(slot_index, fingerprint)
        checkpoint_sink.record_downloaded(
            slot_index,
            relative_path=self.relative_staging,
            sha256=self.final_facts.sha256,
        )
        self.downloaded_slots.append(slot_index)
        return self.final_facts


def _flow_service(
    tmp_path: Path,
    *,
    runtime: ReadOnlyRecoveryRuntime | None = None,
) -> tuple[ImageService, Path, str, str]:
    database = tmp_path / "auraly.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"trusted-reference")
    runtime = runtime or ReadOnlyRecoveryRuntime()
    service = ImageService.for_database(
        database,
        clock=lambda: NOW,
        work_root=work_root,
        _recovery_runtime_factory=lambda _context: runtime,
    )
    submission = service.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key=f"flow-recovery-{uuid4()}",
            prompt_snapshot="A private but persisted recovery prompt",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="operator-1",
            provider_workspace_path=WORKSPACE_PATH,
            provider_workspace_fingerprint=WORKSPACE_SHA,
        )
    )
    with service._sessions() as session:
        job = session.get(JobRow, submission.job.job_id)
        assert job is not None
        job.status = "blocked"
        job.last_error_code = "flow_recovery_blocked"
        job.last_error_message = "The Flow generation requires reconciliation."
        session.commit()
    return service, work_root, submission.generation.image_generation_id, submission.job.job_id


def _run_and_slots(
    service: ImageService,
    generation_id: str,
) -> tuple[FlowGenerationRunRow, list[FlowCandidateSlotRow]]:
    with service._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation_id
            )
        )
        assert run is not None
        slots = list(
            session.scalars(
                select(FlowCandidateSlotRow)
                .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                .order_by(FlowCandidateSlotRow.slot_index)
            )
        )
        assert [slot.slot_index for slot in slots] == [0, 1]
        return run, slots


def _write_final(
    service: ImageService,
    work_root: Path,
    generation_id: str,
    index: int,
):
    with service._sessions() as session:
        generation = session.get(ImageGenerationRow, generation_id)
        assert generation is not None
        final = resolve_flow_final_path(
            work_root=work_root,
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            candidate_index=index,
            image_format="png",
        )
    final.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2048, 1), color=(index + 1, 2, 3)).save(final, format="PNG")
    return final, inspect_flow_artifact(final)


def _seed_ingested_slot(
    service: ImageService,
    work_root: Path,
    generation_id: str,
    index: int,
) -> None:
    final, facts = _write_final(service, work_root, generation_id, index)
    with service._sessions() as session:
        generation = session.get(ImageGenerationRow, generation_id)
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation_id
            )
        )
        slot = session.scalar(
            select(FlowCandidateSlotRow).where(
                FlowCandidateSlotRow.flow_generation_run_id == run.id,
                FlowCandidateSlotRow.slot_index == index,
            )
        )
        assert generation is not None and run is not None and slot is not None
        candidate = ImageCandidate(
            image_candidate_id=str(uuid4()),
            image_generation_id=generation_id,
            candidate_index=index,
            source_path=final.relative_to(work_root).as_posix(),
            sha256=facts.sha256,
            width=facts.width,
            height=facts.height,
            size_bytes=facts.size_bytes,
            format=facts.format,
            review_status="pending_review",
            created_at=NOW,
            updated_at=NOW,
        )
        ImageRepository.create_candidate_in_session(session, candidate)
        slot.provider_slot_fingerprint = hashlib.sha256(f"slot-{index}".encode()).hexdigest()
        slot.download_intent_at = NOW
        slot.staging_path = (
            final.parent / ".staging" / f"candidate-{index}.part"
        ).relative_to(work_root).as_posix()
        slot.staged_sha256 = facts.sha256
        slot.image_candidate_id = candidate.image_candidate_id
        slot.state = "ingested"
        session.commit()


@pytest.mark.parametrize(
    ("checkpoint", "expected_reason", "browser_opened"),
    [
        ("prepared", "no_dispatch_proven", False),
        ("dispatch_intent_with_generating_ui", "existing_dispatch_reconciled", True),
        ("dispatch_confirmed", "existing_dispatch_reconciled", True),
        ("downloaded_staging", "staged_artifact_reconciled", False),
        ("two_ingested", "completed_generation_reconciled", False),
    ],
)
def test_recover_generation_uses_evidence_without_redispatch(
    tmp_path: Path,
    checkpoint: str,
    expected_reason: str,
    browser_opened: bool,
) -> None:
    runtime = ReadOnlyRecoveryRuntime()
    service, work_root, generation_id, job_id = _flow_service(tmp_path, runtime=runtime)
    run, slots = _run_and_slots(service, generation_id)
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        assert persisted_run is not None and generation is not None
        if checkpoint == "dispatch_intent_with_generating_ui":
            persisted_run.stage = "ambiguous"
            persisted_run.dispatch_intent_at = NOW
            generation.provider_state = "blocked"
        elif checkpoint == "dispatch_confirmed":
            persisted_run.stage = "dispatch_confirmed"
            persisted_run.dispatch_intent_at = NOW
            persisted_run.dispatch_confirmed_at = NOW
            generation.provider_state = "blocked"
        session.commit()
    if checkpoint == "downloaded_staging":
        _seed_ingested_slot(service, work_root, generation_id, 1)
        final, facts = _write_final(service, work_root, generation_id, 0)
        with service._sessions() as session:
            persisted_run = session.get(FlowGenerationRunRow, run.id)
            generation = session.get(ImageGenerationRow, generation_id)
            slot = session.get(FlowCandidateSlotRow, slots[0].id)
            assert persisted_run is not None and generation is not None and slot is not None
            persisted_run.stage = "downloading"
            persisted_run.dispatch_intent_at = NOW
            persisted_run.dispatch_confirmed_at = NOW
            generation.provider_state = "blocked"
            slot.provider_slot_fingerprint = "a" * 64
            slot.state = "downloaded"
            slot.download_intent_at = NOW
            slot.staging_path = (
                final.parent / ".staging" / "candidate-0.part"
            ).relative_to(work_root).as_posix()
            slot.staged_sha256 = facts.sha256
            session.commit()
    elif checkpoint == "two_ingested":
        _seed_ingested_slot(service, work_root, generation_id, 0)
        _seed_ingested_slot(service, work_root, generation_id, 1)
        with service._sessions() as session:
            persisted_run = session.get(FlowGenerationRunRow, run.id)
            generation = session.get(ImageGenerationRow, generation_id)
            assert persisted_run is not None and generation is not None
            persisted_run.stage = "completed"
            persisted_run.dispatch_intent_at = NOW
            persisted_run.dispatch_confirmed_at = NOW
            generation.provider_state = "completed"
            generation.dispatched_at = NOW
            generation.completed_at = NOW
            session.commit()

    result = service.recover_generation(generation_id, reconciled_by="operator-1")

    assert result.reason == expected_reason
    assert result.job.status == "queued"
    assert result.generation.image_generation_id == generation_id
    assert result.flow_run.image_generation_id == generation_id
    assert [slot.slot_index for slot in result.slots] == [0, 1]
    assert runtime.browser_opened is browser_opened
    assert runtime.generate_click_count == 0
    reconciled = [event for event in result.job.events if event.event_type == "job.reconciled"]
    assert reconciled[-1].metadata["reason"] == expected_reason
    audited = [
        event
        for event in result.job.events
        if event.event_type == "job.flow_recovery_reconciled"
    ]
    assert audited[-1].metadata == {
        "reconciledBy": "operator-1",
        "reason": expected_reason,
    }
    service.close()


@pytest.mark.parametrize(
    "failure",
    [
        "empty_grid",
        "changed_workspace",
        "unsafe_route",
        "reference_mismatch",
        "job_fingerprint",
        "authorization_event",
        "duplicate_slot_fingerprint",
        "artifact_conflict",
        "ingested_db_conflict",
        "missing_completed_artifact",
        "sanitizer_failure",
        "browser_close_failure",
    ],
)
def test_recover_generation_blocks_unsafe_or_ambiguous_evidence(
    tmp_path: Path,
    failure: str,
) -> None:
    runtime_failure: Exception | None = None
    confirms = True
    if failure == "empty_grid":
        confirms = False
    elif failure == "changed_workspace":
        runtime_failure = FlowDispatchAmbiguousError()
    elif failure in {"sanitizer_failure", "browser_close_failure"}:
        runtime_failure = FlowGenerationRuntimeError(
            failed_step=(
                "capture_grid_evidence" if failure == "sanitizer_failure" else "close_browser"
            )
        )
    runtime = ReadOnlyRecoveryRuntime(
        confirms_dispatch=confirms,
        failure=runtime_failure,
    )
    service, work_root, generation_id, job_id = _flow_service(tmp_path, runtime=runtime)
    run, slots = _run_and_slots(service, generation_id)
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        assert persisted_run is not None and generation is not None
        persisted_run.stage = "ambiguous"
        persisted_run.dispatch_intent_at = NOW
        generation.provider_state = "blocked"
        if failure == "unsafe_route":
            persisted_run.provider_workspace_path = "../private-workspace"
        elif failure == "reference_mismatch":
            (work_root / (generation.reference_image_path or "")).write_bytes(b"tampered")
        elif failure == "job_fingerprint":
            job = session.get(JobRow, job_id)
            assert job is not None
            job.request_fingerprint = "0" * 64
        elif failure == "authorization_event":
            event = session.scalar(
                select(JobEventRow).where(
                    JobEventRow.job_id == job_id,
                    JobEventRow.event_type == "job.authorized",
                )
            )
            assert event is not None
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=job_id,
                    event_type="job.authorized",
                    timestamp=event.timestamp,
                    metadata_json={"executor": "local_fake"},
                )
            )
        elif failure == "duplicate_slot_fingerprint":
            persisted_run.stage = "candidates_observed"
            persisted_run.dispatch_confirmed_at = NOW
            for slot in slots:
                persisted_slot = session.get(FlowCandidateSlotRow, slot.id)
                assert persisted_slot is not None
                persisted_slot.state = "observed"
                persisted_slot.provider_slot_fingerprint = "a" * 64
        session.commit()
    if failure == "artifact_conflict":
        final, _facts = _write_final(service, work_root, generation_id, 0)
        with service._sessions() as session:
            persisted_run = session.get(FlowGenerationRunRow, run.id)
            persisted_slot = session.get(FlowCandidateSlotRow, slots[0].id)
            assert persisted_run is not None and persisted_slot is not None
            persisted_run.stage = "downloading"
            persisted_run.dispatch_confirmed_at = NOW
            persisted_slot.provider_slot_fingerprint = "a" * 64
            persisted_slot.state = "downloaded"
            persisted_slot.download_intent_at = NOW
            persisted_slot.staging_path = (
                final.parent / ".staging" / "candidate-0.part"
            ).relative_to(work_root).as_posix()
            persisted_slot.staged_sha256 = "0" * 64
            session.commit()
    elif failure in {"ingested_db_conflict", "missing_completed_artifact"}:
        _seed_ingested_slot(service, work_root, generation_id, 0)
        _seed_ingested_slot(service, work_root, generation_id, 1)
        with service._sessions() as session:
            persisted_run = session.get(FlowGenerationRunRow, run.id)
            generation = session.get(ImageGenerationRow, generation_id)
            candidate = session.scalar(
                select(ImageCandidateRow).where(ImageCandidateRow.candidate_index == 0)
            )
            assert persisted_run is not None and generation is not None and candidate is not None
            persisted_run.stage = "completed"
            persisted_run.dispatch_confirmed_at = NOW
            generation.provider_state = "completed"
            generation.completed_at = NOW
            if failure == "missing_completed_artifact":
                (work_root / candidate.source_path).unlink()
            else:
                slot = session.scalar(
                    select(FlowCandidateSlotRow).where(
                        FlowCandidateSlotRow.flow_generation_run_id == run.id,
                        FlowCandidateSlotRow.slot_index == 0,
                    )
                )
                assert slot is not None
                slot.staged_sha256 = "0" * 64
            session.commit()

    with pytest.raises(ImageRecoveryBlockedError):
        service.recover_generation(generation_id, reconciled_by="operator-1")

    with service._sessions() as session:
        job = session.get(JobRow, job_id)
        assert job is not None and job.status == "blocked"
        reconciled_count = len(
            list(
                session.scalars(
                    select(JobEventRow).where(
                        JobEventRow.job_id == job_id,
                        JobEventRow.event_type == "job.reconciled",
                    )
                )
            )
        )
    assert reconciled_count == 0
    assert runtime.generate_click_count == 0
    if failure in {
        "unsafe_route",
        "reference_mismatch",
        "job_fingerprint",
        "authorization_event",
        "duplicate_slot_fingerprint",
        "artifact_conflict",
        "ingested_db_conflict",
        "missing_completed_artifact",
    }:
        assert runtime.browser_opened is False
    service.close()


def test_recover_generation_redownloads_only_the_same_durable_intent_slot(
    tmp_path: Path,
) -> None:
    runtime = DownloadRecoveryRuntime()
    service, work_root, generation_id, _job_id = _flow_service(tmp_path, runtime=runtime)
    run, slots = _run_and_slots(service, generation_id)
    _seed_ingested_slot(service, work_root, generation_id, 1)
    final, facts = _write_final(service, work_root, generation_id, 0)
    runtime.final_facts = facts
    runtime.relative_staging = (
        final.parent / ".staging" / "candidate-0.part"
    ).relative_to(work_root).as_posix()
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        slot = session.get(FlowCandidateSlotRow, slots[0].id)
        assert persisted_run is not None and generation is not None and slot is not None
        persisted_run.stage = "downloading"
        persisted_run.dispatch_intent_at = NOW
        persisted_run.dispatch_confirmed_at = NOW
        generation.provider_state = "blocked"
        slot.provider_slot_fingerprint = "a" * 64
        slot.state = "download_intent_recorded"
        slot.download_intent_at = NOW
        session.commit()

    result = service.recover_generation(generation_id, reconciled_by="operator-1")

    assert result.reason == "existing_dispatch_reconciled"
    assert runtime.downloaded_slots == [0]
    assert runtime.generate_click_count == 0
    assert [slot.state for slot in result.slots] == ["ingested", "ingested"]
    assert result.flow_run.stage == "completed"
    service.close()


def test_resolve_no_dispatch_preserves_attempt_audit_and_requeues(tmp_path: Path) -> None:
    service, _work_root, generation_id, _job_id = _flow_service(tmp_path)
    run, _slots = _run_and_slots(service, generation_id)
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        assert persisted_run is not None and generation is not None
        persisted_run.stage = "ambiguous"
        persisted_run.dispatch_intent_at = NOW
        generation.provider_state = "blocked"
        session.commit()

    resolved = service.resolve_no_dispatch(
        generation_id,
        resolved_by="operator-1",
        reason="Operator inspected Flow history and confirmed no generation.",
    )

    assert resolved.reason == "no_dispatch_proven"
    assert resolved.flow_run.stage == "prepared"
    assert resolved.flow_run.dispatch_attempt_number == 2
    assert resolved.flow_run.dispatch_intent_at is None
    assert resolved.flow_run.dispatch_confirmed_at is None
    assert resolved.job.status == "queued"
    event = next(
        item for item in resolved.job.events if item.event_type == "job.flow_dispatch_resolved"
    )
    assert event.metadata == {
        "previousDispatchAttemptNumber": 1,
        "previousDispatchIntentAt": NOW.isoformat(),
        "previousDispatchConfirmedAt": None,
        "nextDispatchAttemptNumber": 2,
        "resolvedBy": "operator-1",
        "reason": "Operator inspected Flow history and confirmed no generation.",
    }
    service.close()


@pytest.mark.parametrize(
    "invalid",
    [
        "confirmed_dispatch",
        "non_ambiguous_run",
        "active_job",
        "unsafe_actor",
        "unsafe_reason",
        "exhausted_job",
        "job_fingerprint",
        "authorization_event",
    ],
)
def test_resolve_no_dispatch_rejects_unsafe_or_inconsistent_state(
    tmp_path: Path,
    invalid: str,
) -> None:
    service, _work_root, generation_id, job_id = _flow_service(tmp_path)
    run, _slots = _run_and_slots(service, generation_id)
    actor = "operator-1"
    reason = "Operator confirmed that no generation occurred."
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        job = session.get(JobRow, job_id)
        assert persisted_run is not None and job is not None
        persisted_run.stage = "ambiguous"
        persisted_run.dispatch_intent_at = NOW
        if invalid == "confirmed_dispatch":
            persisted_run.stage = "dispatch_confirmed"
            persisted_run.dispatch_confirmed_at = NOW
        elif invalid == "non_ambiguous_run":
            persisted_run.stage = "prepared"
            persisted_run.dispatch_intent_at = None
        elif invalid == "active_job":
            job.status = "queued"
        elif invalid == "unsafe_actor":
            actor = "operator\nPRIVATE"
        elif invalid == "unsafe_reason":
            reason = "token=PRIVATE"
        elif invalid == "exhausted_job":
            job.attempt_count = job.max_attempts
        elif invalid == "job_fingerprint":
            job.request_fingerprint = "0" * 64
        elif invalid == "authorization_event":
            authorization = session.scalar(
                select(JobEventRow).where(
                    JobEventRow.job_id == job_id,
                    JobEventRow.event_type == "job.authorized",
                )
            )
            assert authorization is not None
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=job_id,
                    event_type="job.authorized",
                    timestamp=authorization.timestamp,
                    metadata_json={"executor": "local_fake"},
                )
            )
        session.commit()

    with pytest.raises((ImageRecoveryBlockedError, ImageTransitionError, ValueError)):
        service.resolve_no_dispatch(
            generation_id,
            resolved_by=actor,
            reason=reason,
        )

    with service._sessions() as session:
        resolution_events = list(
            session.scalars(
                select(JobEventRow).where(
                    JobEventRow.job_id == job_id,
                    JobEventRow.event_type == "job.flow_dispatch_resolved",
                )
            )
        )
    assert resolution_events == []
    service.close()


def test_resolve_no_dispatch_is_idempotent_only_when_resume_was_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _work_root, generation_id, job_id = _flow_service(tmp_path)
    run, _slots = _run_and_slots(service, generation_id)
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        assert persisted_run is not None
        persisted_run.stage = "ambiguous"
        persisted_run.dispatch_intent_at = NOW
        session.commit()
    original_resume = service._jobs.resume_reconciled_job
    calls = 0

    def interrupted_resume(*_args: object, **_kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated process exit after resolution commit")
        return original_resume(*_args, **_kwargs)

    monkeypatch.setattr(service._jobs, "resume_reconciled_job", interrupted_resume)
    with pytest.raises(RuntimeError):
        service.resolve_no_dispatch(
            generation_id,
            resolved_by="operator-1",
            reason="Operator confirmed that no generation occurred.",
        )

    resumed = service.resolve_no_dispatch(
        generation_id,
        resolved_by="operator-1",
        reason="Operator confirmed that no generation occurred.",
    )

    assert resumed.job.status == "queued"
    assert resumed.flow_run.dispatch_attempt_number == 2
    events = [
        event for event in resumed.job.events if event.event_type == "job.flow_dispatch_resolved"
    ]
    assert len(events) == 1
    with pytest.raises((ImageRecoveryBlockedError, ImageTransitionError)):
        service.resolve_no_dispatch(
            generation_id,
            resolved_by="operator-1",
            reason="Operator confirmed that no generation occurred.",
        )
    assert service._jobs.get_job(job_id).status == "queued"
    service.close()

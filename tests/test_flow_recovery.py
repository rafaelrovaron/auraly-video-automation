from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy import select, text

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.flow.artifacts import (
    FlowArtifactFacts,
    inspect_flow_artifact,
    resolve_flow_final_path,
)
from auraly_pipeline.flow.generation_domain import (
    FlowDispatchAmbiguousError,
    FlowGenerationRuntimeError,
)
from auraly_pipeline.flow.generation import FlowGenerationDownloadCheckpointSink
from auraly_pipeline.images.db_models import (
    FlowCandidateSlotRow,
    FlowGenerationRunRow,
    ImageCandidateRow,
    ImageGenerationRow,
)
from auraly_pipeline.images.domain import (
    FlowReconciliationReason,
    ImageCandidate,
    ImageGenerateRequest,
)
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

    def download_slot(
        self,
        _slot_index: int,
        _checkpoint_sink: FlowGenerationDownloadCheckpointSink,
    ) -> FlowArtifactFacts:
        raise AssertionError("read-only observation cannot download an unrecorded slot")


class DownloadRecoveryRuntime(ReadOnlyRecoveryRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.downloaded_slots: list[int] = []
        self.final_facts = None
        self.relative_staging: str | None = None

    def download_slot(
        self,
        slot_index: int,
        checkpoint_sink: FlowGenerationDownloadCheckpointSink,
    ) -> FlowArtifactFacts:
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


def _seed_grid_evidence(
    service: ImageService,
    work_root: Path,
    run_id: str,
) -> None:
    evidence = work_root / "diagnostics" / run_id / "candidate-grid.png"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(b"sanitized-grid-evidence")
    with service._sessions() as session:
        run = session.get(FlowGenerationRunRow, run_id)
        assert run is not None
        run.grid_evidence_path = evidence.relative_to(work_root).as_posix()
        run.grid_evidence_sha256 = hashlib.sha256(evidence.read_bytes()).hexdigest()
        session.commit()


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
        assert run is not None
        slot = session.scalar(
            select(FlowCandidateSlotRow).where(
                FlowCandidateSlotRow.flow_generation_run_id == run.id,
                FlowCandidateSlotRow.slot_index == index,
            )
        )
        assert generation is not None and slot is not None
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
    _seed_grid_evidence(service, work_root, run.id)


def _seed_downloaded_slot(
    service: ImageService,
    work_root: Path,
    generation_id: str,
    index: int,
) -> None:
    final, facts = _write_final(service, work_root, generation_id, index)
    with service._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation_id
            )
        )
        assert run is not None
        slot = session.scalar(
            select(FlowCandidateSlotRow).where(
                FlowCandidateSlotRow.flow_generation_run_id == run.id,
                FlowCandidateSlotRow.slot_index == index,
            )
        )
        assert slot is not None
        slot.provider_slot_fingerprint = hashlib.sha256(f"slot-{index}".encode()).hexdigest()
        slot.download_intent_at = NOW
        slot.staging_path = (
            final.parent / ".staging" / f"candidate-{index}.part"
        ).relative_to(work_root).as_posix()
        slot.staged_sha256 = facts.sha256
        slot.state = "downloaded"
        session.commit()
    _seed_grid_evidence(service, work_root, run.id)


def _recovery_db_snapshot(
    service: ImageService,
    generation_id: str,
    job_id: str,
) -> tuple[object, ...]:
    with service._sessions() as session:
        generation = session.get(ImageGenerationRow, generation_id)
        job = session.get(JobRow, job_id)
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation_id
            )
        )
        assert generation is not None and job is not None and run is not None
        slots = list(
            session.scalars(
                select(FlowCandidateSlotRow)
                .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                .order_by(FlowCandidateSlotRow.slot_index)
            )
        )
        candidates = list(
            session.scalars(
                select(ImageCandidateRow)
                .where(ImageCandidateRow.image_generation_id == generation_id)
                .order_by(ImageCandidateRow.candidate_index)
            )
        )
        events = list(
            session.scalars(
                select(JobEventRow)
                .where(JobEventRow.job_id == job_id)
                .order_by(JobEventRow.sequence)
            )
        )
        return (
            job.status,
            generation.provider_state,
            generation.dispatched_at,
            generation.completed_at,
            run.stage,
            run.dispatch_attempt_number,
            run.dispatch_intent_at,
            run.dispatch_confirmed_at,
            run.grid_evidence_path,
            run.grid_evidence_sha256,
            tuple(
                (
                    slot.slot_index,
                    slot.state,
                    slot.provider_slot_fingerprint,
                    slot.download_intent_at,
                    slot.staging_path,
                    slot.staged_sha256,
                    slot.image_candidate_id,
                )
                for slot in slots
            ),
            tuple((candidate.id, candidate.sha256) for candidate in candidates),
            tuple((event.event_type, event.metadata_json) for event in events),
        )


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
            generation.dispatched_at = NOW
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
            generation.dispatched_at = NOW
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
                corrupt_slot = session.scalar(
                    select(FlowCandidateSlotRow).where(
                        FlowCandidateSlotRow.flow_generation_run_id == run.id,
                        FlowCandidateSlotRow.slot_index == 0,
                    )
                )
                assert corrupt_slot is not None
                corrupt_slot.staged_sha256 = "0" * 64
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


@pytest.mark.parametrize(
    "corruption",
    [
        "downloaded_missing_fingerprint",
        "downloaded_missing_intent",
        "downloaded_second_artifact_conflict",
        "ingested_missing_fingerprint",
        "ingested_missing_intent",
    ],
)
def test_recover_generation_validates_all_slot_facts_before_any_offline_mutation(
    tmp_path: Path,
    corruption: str,
) -> None:
    service, work_root, generation_id, job_id = _flow_service(tmp_path)
    run, slots = _run_and_slots(service, generation_id)
    is_ingested = corruption.startswith("ingested")
    for index in range(2):
        if is_ingested:
            _seed_ingested_slot(service, work_root, generation_id, index)
        else:
            _seed_downloaded_slot(service, work_root, generation_id, index)
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        corrupt_index = 1 if corruption == "downloaded_second_artifact_conflict" else 0
        slot = session.get(FlowCandidateSlotRow, slots[corrupt_index].id)
        assert persisted_run is not None and generation is not None and slot is not None
        persisted_run.stage = "completed" if is_ingested else "downloading"
        persisted_run.dispatch_intent_at = NOW
        persisted_run.dispatch_confirmed_at = NOW
        generation.provider_state = "completed" if is_ingested else "blocked"
        generation.dispatched_at = NOW
        generation.completed_at = NOW if is_ingested else None
        if corruption.endswith("missing_fingerprint"):
            slot.provider_slot_fingerprint = None
        elif corruption.endswith("missing_intent"):
            slot.download_intent_at = None
        else:
            slot.staged_sha256 = "0" * 64
        session.commit()
    before = _recovery_db_snapshot(service, generation_id, job_id)

    with pytest.raises(ImageRecoveryBlockedError):
        service.recover_generation(generation_id, reconciled_by="operator-1")

    assert _recovery_db_snapshot(service, generation_id, job_id) == before
    service.close()


@pytest.mark.parametrize(
    ("generation_state", "run_stage"),
    [
        ("failed", "prepared"),
        ("blocked", "failed"),
        ("completed", "prepared"),
    ],
)
def test_recover_generation_never_revives_terminal_or_incompatible_state(
    tmp_path: Path,
    generation_state: str,
    run_stage: str,
) -> None:
    service, _work_root, generation_id, job_id = _flow_service(tmp_path)
    run, _slots = _run_and_slots(service, generation_id)
    with service._sessions() as session:
        generation = session.get(ImageGenerationRow, generation_id)
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        assert generation is not None and persisted_run is not None
        generation.provider_state = generation_state
        generation.completed_at = NOW if generation_state == "completed" else None
        persisted_run.stage = run_stage
        session.commit()
    before = _recovery_db_snapshot(service, generation_id, job_id)

    with pytest.raises(ImageRecoveryBlockedError):
        service.recover_generation(generation_id, reconciled_by="operator-1")

    assert _recovery_db_snapshot(service, generation_id, job_id) == before
    service.close()


def test_recover_generation_blocks_prompt_tamper_without_state_change(tmp_path: Path) -> None:
    service, _work_root, generation_id, job_id = _flow_service(tmp_path)
    with service._sessions() as session:
        session.execute(text("DROP TRIGGER prevent_image_generation_intent_update"))
        session.execute(
            text(
                "UPDATE image_generations SET prompt_snapshot = :prompt WHERE id = :generation_id"
            ),
            {"prompt": "tampered private prompt", "generation_id": generation_id},
        )
        session.commit()
    before = _recovery_db_snapshot(service, generation_id, job_id)

    with pytest.raises(ImageRecoveryBlockedError):
        service.recover_generation(generation_id, reconciled_by="operator-1")

    assert _recovery_db_snapshot(service, generation_id, job_id) == before
    service.close()


def test_recover_generation_revalidates_all_slots_before_first_offline_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, work_root, generation_id, job_id = _flow_service(tmp_path)
    run, slots = _run_and_slots(service, generation_id)
    _seed_downloaded_slot(service, work_root, generation_id, 0)
    _seed_downloaded_slot(service, work_root, generation_id, 1)
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        assert persisted_run is not None and generation is not None
        persisted_run.stage = "downloading"
        persisted_run.dispatch_intent_at = NOW
        persisted_run.dispatch_confirmed_at = NOW
        generation.provider_state = "blocked"
        generation.dispatched_at = NOW
        session.commit()
    original_ingest = service._ingest_recovered_slot
    state_after_interleave: list[tuple[object, ...]] = []

    def ingest_after_slot_corruption(*args: object, **kwargs: object) -> None:
        if not state_after_interleave:
            with service._sessions() as session:
                slot_one = session.get(FlowCandidateSlotRow, slots[1].id)
                assert slot_one is not None
                slot_one.provider_slot_fingerprint = None
                session.commit()
            state_after_interleave.append(
                _recovery_db_snapshot(service, generation_id, job_id)
            )
        original_ingest(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "_ingest_recovered_slot", ingest_after_slot_corruption)

    with pytest.raises(ImageRecoveryBlockedError):
        service.recover_generation(generation_id, reconciled_by="operator-1")

    assert len(state_after_interleave) == 1
    assert _recovery_db_snapshot(service, generation_id, job_id) == state_after_interleave[0]
    service.close()


def test_recover_generation_revalidates_job_before_pre_intent_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _work_root, generation_id, job_id = _flow_service(tmp_path)
    original_reset = service._reset_pre_intent_run
    state_after_interleave: list[tuple[object, ...]] = []

    def reset_after_job_corruption(*args: object, **kwargs: object) -> None:
        with service._sessions() as session:
            job = session.get(JobRow, job_id)
            assert job is not None
            job.request_fingerprint = "0" * 64
            session.commit()
        state_after_interleave.append(_recovery_db_snapshot(service, generation_id, job_id))
        original_reset(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "_reset_pre_intent_run", reset_after_job_corruption)

    with pytest.raises(ImageRecoveryBlockedError):
        service.recover_generation(generation_id, reconciled_by="operator-1")

    assert len(state_after_interleave) == 1
    assert _recovery_db_snapshot(service, generation_id, job_id) == state_after_interleave[0]
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
        generation.dispatched_at = NOW
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


def test_resolve_no_dispatch_rejects_inconsistent_grid_evidence_without_erasing_it(
    tmp_path: Path,
) -> None:
    service, work_root, generation_id, job_id = _flow_service(tmp_path)
    run, _slots = _run_and_slots(service, generation_id)
    evidence = work_root / "diagnostics" / run.id / "prior-grid.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"sanitized-prior-grid")
    evidence_sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        assert persisted_run is not None and generation is not None
        persisted_run.stage = "ambiguous"
        persisted_run.dispatch_intent_at = NOW
        persisted_run.grid_evidence_path = evidence.relative_to(work_root).as_posix()
        persisted_run.grid_evidence_sha256 = evidence_sha
        generation.provider_state = "blocked"
        session.commit()
    before = _recovery_db_snapshot(service, generation_id, job_id)

    with pytest.raises(ImageRecoveryBlockedError):
        service.resolve_no_dispatch(
            generation_id,
            resolved_by="operator-1",
            reason="Operator confirmed that no generation occurred.",
        )

    assert _recovery_db_snapshot(service, generation_id, job_id) == before
    service.close()


def test_resolve_no_dispatch_revalidates_inside_atomic_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _work_root, generation_id, job_id = _flow_service(tmp_path)
    run, _slots = _run_and_slots(service, generation_id)
    with service._sessions() as session:
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        generation = session.get(ImageGenerationRow, generation_id)
        assert persisted_run is not None and generation is not None
        persisted_run.stage = "ambiguous"
        persisted_run.dispatch_intent_at = NOW
        generation.provider_state = "blocked"
        session.commit()
    original_immediate_transaction = service._repository.immediate_transaction
    interleaved = False

    def immediate_transaction_with_interleave(operation):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            with service._sessions() as session:
                job = session.get(JobRow, job_id)
                assert job is not None
                job.request_fingerprint = "0" * 64
                session.commit()
        return original_immediate_transaction(operation)

    monkeypatch.setattr(
        service._repository,
        "immediate_transaction",
        immediate_transaction_with_interleave,
    )

    with pytest.raises((ImageRecoveryBlockedError, ValueError)):
        service.resolve_no_dispatch(
            generation_id,
            resolved_by="operator-1",
            reason="Operator confirmed that no generation occurred.",
        )

    with service._sessions() as session:
        generation = session.get(ImageGenerationRow, generation_id)
        persisted_run = session.get(FlowGenerationRunRow, run.id)
        resolution_events = list(
            session.scalars(
                select(JobEventRow).where(
                    JobEventRow.job_id == job_id,
                    JobEventRow.event_type == "job.flow_dispatch_resolved",
                )
            )
        )
        assert generation is not None and persisted_run is not None
        assert generation.provider_state == "blocked"
        assert persisted_run.stage == "ambiguous"
        assert persisted_run.dispatch_intent_at is not None
        assert service._utc(persisted_run.dispatch_intent_at) == NOW
        assert resolution_events == []
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
        generation = session.get(ImageGenerationRow, generation_id)
        assert persisted_run is not None and generation is not None
        persisted_run.stage = "ambiguous"
        persisted_run.dispatch_intent_at = NOW
        generation.provider_state = "blocked"
        session.commit()
    original_resume = service._jobs.resume_reconciled_job
    calls = 0

    def interrupted_resume(
        job_id: str,
        *,
        reason: FlowReconciliationReason,
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated process exit after resolution commit")
        return original_resume(job_id, reason=reason)

    monkeypatch.setattr(service._jobs, "resume_reconciled_job", interrupted_resume)
    with pytest.raises(RuntimeError):
        service.resolve_no_dispatch(
            generation_id,
            resolved_by="operator-1",
            reason="Operator confirmed that no generation occurred.",
        )
    with service._sessions() as session:
        committed_run = session.get(FlowGenerationRunRow, run.id)
        committed_event = session.scalar(
            select(JobEventRow).where(
                JobEventRow.job_id == job_id,
                JobEventRow.event_type == "job.flow_dispatch_resolved",
            )
        )
        assert committed_run is not None and committed_event is not None
        assert committed_run.stage == "prepared"
        assert committed_event.metadata_json["nextDispatchAttemptNumber"] == 2
        assert committed_event.metadata_json["resolvedBy"] == "operator-1"
        assert committed_event.metadata_json["reason"] == (
            "Operator confirmed that no generation occurred."
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

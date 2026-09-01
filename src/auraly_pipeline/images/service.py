from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, SceneVariantRow
from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.config_paths import configured_work_root
from auraly_pipeline.flow.artifacts import (
    FlowArtifactConflictError,
    FlowArtifactFacts,
    FlowArtifactInvalidError,
    inspect_flow_artifact,
    publish_flow_artifact_exclusive,
    resolve_flow_final_path,
)
from auraly_pipeline.flow.config import (
    resolve_flow_generation_config,
    resolve_flow_runtime_config,
)
from auraly_pipeline.flow.generation import (
    FlowGenerationArtifactContext,
    FlowGenerationDownloadCheckpointSink,
    FlowGenerationRuntime,
)
from auraly_pipeline.flow.generation_domain import (
    FlowDispatchAmbiguousError,
    FlowGenerationRuntimeError,
    FlowWorkspaceIdentity,
)
from auraly_pipeline.images.db_models import (
    FlowCandidateSlotRow,
    FlowGenerationRunRow,
    ImageCandidateRow,
    ImageGenerationRow,
)
from auraly_pipeline.images.domain import (
    FlowCandidateSlot,
    FlowCandidateSlotState,
    FlowGenerationRun,
    FlowGenerationStage,
    FlowReconciliationReason,
    ImageCandidate,
    ImageCandidateReviewStatus,
    ImageExecutor,
    ImageGenerateRequest,
    ImageGeneration,
    ImageGenerationState,
    ImageGenerationSubmission,
    ImageProvider,
    generation_request_fingerprint,
)
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow
from auraly_pipeline.jobs.domain import Job, JobSubmit, RetrySafety
from auraly_pipeline.jobs.handlers import JobHandler
from auraly_pipeline.jobs.service import (
    JobIdempotencyConflictError,
    JobService,
    JobTransitionError,
)
from auraly_pipeline.metadata_security import (
    validate_safe_error_message,
    validate_safe_identifier,
)


class ImageError(RuntimeError):
    code = "image_operation_failed"
    public_message = "The image operation failed safely."

    def __init__(self) -> None:
        super().__init__(self.public_message)


class ImageGenerationNotFoundError(ImageError):
    code = "image_generation_not_found"
    public_message = "Image generation not found."


class ImageCandidateNotFoundError(ImageError):
    code = "image_candidate_not_found"
    public_message = "Image candidate not found."


class ImageIdempotencyConflictError(ImageError):
    code = "image_idempotency_conflict"
    public_message = "The idempotency key is already used by a different image generation request."


class ImageCandidateSceneMismatchError(ImageError):
    code = "image_candidate_scene_mismatch"
    public_message = "The image candidate does not belong to the requested SceneVariant."


class ImageApprovedCandidateExistsError(ImageError):
    code = "image_approved_candidate_exists"
    public_message = "The SceneVariant already has an approved image candidate."


class ImageArtifactMissingError(ImageError):
    code = "image_artifact_missing"
    public_message = "The persisted image artifact is missing."


class ImageArtifactConflictError(ImageError):
    code = "image_artifact_conflict"
    public_message = "The image artifact conflicts with persisted evidence."


class ImageTransitionError(ImageError):
    code = "image_invalid_transition"
    public_message = "The requested image state transition is not allowed."


class ImageRecoveryBlockedError(ImageError):
    code = "flow_recovery_blocked"
    public_message = "The Flow image generation could not be reconciled safely."


class _FlowRecoveryRuntime(Protocol):
    def recover_dispatch(
        self,
        workspace: FlowWorkspaceIdentity,
        *,
        expected_fingerprints: tuple[str, ...] = (),
    ) -> bool: ...

    def download_slot(
        self,
        slot_index: int,
        checkpoint_sink: FlowGenerationDownloadCheckpointSink,
    ) -> FlowArtifactFacts: ...


_FlowRecoveryRuntimeFactory = Callable[
    [FlowGenerationArtifactContext],
    _FlowRecoveryRuntime,
]


def _build_flow_recovery_runtime(
    context: FlowGenerationArtifactContext,
) -> _FlowRecoveryRuntime:
    return FlowGenerationRuntime(
        resolve_flow_generation_config(),
        runtime_config=resolve_flow_runtime_config(),
        artifact_context=context,
    )


@dataclass(frozen=True)
class ImageGenerationRecovery:
    generation: ImageGeneration
    flow_run: FlowGenerationRun
    slots: tuple[FlowCandidateSlot, FlowCandidateSlot]
    job: Job
    reason: FlowReconciliationReason


class _RecoveryDownloadCheckpointSink:
    """Permit only continuation of one already-audited download intent."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        run_id: str,
        clock: Callable[[], datetime],
        validate_locked_state: Callable[[Session], None],
    ) -> None:
        self._sessions = sessions
        self._run_id = run_id
        self._clock = clock
        self._validate_locked_state = validate_locked_state

    def candidate_fingerprint(self, slot_index: int) -> str:
        with self._sessions() as session:
            slot = session.scalar(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == self._run_id,
                    FlowCandidateSlotRow.slot_index == slot_index,
                    FlowCandidateSlotRow.state == "download_intent_recorded",
                )
            )
            if slot is None or slot.provider_slot_fingerprint is None:
                raise ImageRecoveryBlockedError
            return slot.provider_slot_fingerprint

    def record_download_intent(self, slot_index: int, fingerprint: str) -> None:
        if self.candidate_fingerprint(slot_index) != fingerprint:
            raise ImageRecoveryBlockedError

    def record_downloaded(
        self,
        slot_index: int,
        *,
        relative_path: str,
        sha256: str,
    ) -> None:
        with self._sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._validate_locked_state(session)
            slot = session.scalar(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == self._run_id,
                    FlowCandidateSlotRow.slot_index == slot_index,
                    FlowCandidateSlotRow.state == "download_intent_recorded",
                )
            )
            if slot is None or slot.provider_slot_fingerprint is None:
                session.rollback()
                raise ImageRecoveryBlockedError
            slot.state = "downloaded"
            slot.staging_path = relative_path
            slot.staged_sha256 = sha256
            slot.updated_at = self._clock()
            session.commit()


class ImageService:
    def __init__(
        self,
        engine: Engine,
        jobs: JobService,
        *,
        clock: Callable[[], datetime] | None = None,
        work_root: Path | None = None,
        _recovery_runtime_factory: _FlowRecoveryRuntimeFactory = _build_flow_recovery_runtime,
    ) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
        self._repository = ImageRepository(self._sessions)
        self._jobs = jobs
        self._clock = clock or (lambda: datetime.now(UTC))
        self._work_root = configured_work_root(work_root)
        self._recovery_runtime_factory = _recovery_runtime_factory

    @classmethod
    def for_database(
        cls,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        handlers: Mapping[str, JobHandler] | None = None,
        work_root: Path | None = None,
        _recovery_runtime_factory: _FlowRecoveryRuntimeFactory = _build_flow_recovery_runtime,
    ) -> ImageService:
        migrate_database(database_path)
        engine = create_sqlite_engine(database_path)
        jobs = JobService.for_database(
            database_path,
            clock=clock,
            handlers=handlers,
            work_root=work_root,
        )
        return cls(
            engine,
            jobs,
            clock=clock,
            work_root=work_root,
            _recovery_runtime_factory=_recovery_runtime_factory,
        )

    def close(self) -> None:
        self._jobs.close()
        self._engine.dispose()

    def worker_once(self, worker_id: str, *, lease_seconds: int = 60) -> Job | None:
        """Run one image Job through the durable worker service."""
        return self._jobs.worker_once(worker_id, lease_seconds=lease_seconds)

    def generate(self, request: ImageGenerateRequest) -> ImageGenerationSubmission:
        request_fingerprint = generation_request_fingerprint(request)

        def create_linked(session: Session, job: JobRow) -> ImageGeneration:
            timestamp = self._utc(self._clock())
            generation = ImageGeneration(
                image_generation_id=str(uuid4()),
                campaign_id=request.campaign_id,
                scene_variant_id=request.scene_variant_id,
                job_id=job.id,
                generation_number=ImageRepository.allocate_generation_number(
                    session, request.scene_variant_id
                ),
                idempotency_key=request.idempotency_key,
                request_fingerprint=request_fingerprint,
                prompt_snapshot=request.prompt_snapshot,
                prompt_sha256=request.prompt_sha256,
                reference_image_path=request.reference_image_path,
                reference_image_sha256=request.reference_image_sha256,
                provider=request.provider,
                executor=request.executor,
                provider_state="queued",
                created_at=timestamp,
                updated_at=timestamp,
            )
            ImageRepository.create_generation_in_session(session, generation)
            if request.executor == "playwright_python":
                # The authorization is durable data, never a worker/CLI choice.
                run_id = str(uuid4())
                flow_run = FlowGenerationRunRow(
                    id=run_id,
                    image_generation_id=generation.image_generation_id,
                    stage="prepared",
                    required_candidate_count=2,
                    required_resolution="2K",
                    provider_workspace_path=request.provider_workspace_path,
                    provider_workspace_fingerprint=request.provider_workspace_fingerprint,
                    dispatch_attempt_number=1,
                    dispatch_intent_at=None,
                    dispatch_confirmed_at=None,
                    grid_evidence_path=None,
                    grid_evidence_sha256=None,
                    last_failure_code=None,
                    provider_action_approved_by=request.provider_action_approved_by,
                    provider_action_approved_at=timestamp,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(flow_run)
                session.flush()
                session.add_all(
                    [
                        FlowCandidateSlotRow(
                            id=str(uuid4()),
                            flow_generation_run_id=run_id,
                            slot_index=index,
                            provider_slot_fingerprint=None,
                            state="pending",
                            download_intent_at=None,
                            staging_path=None,
                            staged_sha256=None,
                            image_candidate_id=None,
                            created_at=timestamp,
                            updated_at=timestamp,
                        )
                        for index in range(2)
                    ]
                )
                session.add(
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=job.id,
                        event_type="job.authorized",
                        timestamp=timestamp,
                        metadata_json={
                            "executor": "playwright_python",
                            "approvedBy": request.provider_action_approved_by,
                            "workspaceFingerprint": request.provider_workspace_fingerprint,
                        },
                    )
                )
                session.flush()
            return generation

        def load_existing(job: Job) -> ImageGeneration:
            row = self._generation_by_job_id(job.job_id)
            if row is None or row.request_fingerprint != request_fingerprint:
                raise ImageIdempotencyConflictError
            return self._generation_to_domain(row)

        job_request = JobSubmit(
            job_type="image.generate",
            campaign_id=request.campaign_id,
            scene_variant_id=request.scene_variant_id,
            idempotency_key=request.idempotency_key,
            input={"imageRequestFingerprint": request_fingerprint},
            retry_safety=(
                RetrySafety.IDEMPOTENT
                if request.executor == "local_fake"
                else RetrySafety.RECONCILE_BEFORE_RETRY
            ),
        )
        try:
            submitted = self._jobs.submit_linked_job(
                job_request,
                create_linked,
                load_existing,
            )
        except JobIdempotencyConflictError as exc:
            raise ImageIdempotencyConflictError from exc
        return ImageGenerationSubmission(
            generation=submitted.linked,
            job=submitted.job,
            reused=submitted.reused,
        )

    def regenerate(self, request: ImageGenerateRequest) -> ImageGenerationSubmission:
        submitted = self.generate(request)
        if submitted.reused:
            raise ImageIdempotencyConflictError
        return submitted

    def get_generation(self, image_generation_id: str) -> ImageGeneration:
        row = self._repository.get_generation(image_generation_id)
        if row is None:
            raise ImageGenerationNotFoundError
        return self._generation_to_domain(row)

    def list_generations(self, scene_variant_id: str) -> list[ImageGeneration]:
        return [
            self._generation_to_domain(row)
            for row in self._repository.list_generations(scene_variant_id)
        ]

    def get_candidate(self, image_candidate_id: str) -> ImageCandidate:
        row = self._repository.get_candidate(image_candidate_id)
        if row is None:
            raise ImageCandidateNotFoundError
        return self._candidate_to_domain(row)

    def list_candidates(self, image_generation_id: str) -> list[ImageCandidate]:
        return [
            self._candidate_to_domain(row)
            for row in self._repository.list_candidates(image_generation_id)
        ]

    def recover_generation(
        self,
        image_generation_id: str,
        *,
        reconciled_by: str,
    ) -> ImageGenerationRecovery:
        """Requeue one blocked Flow Job only after durable or read-only evidence."""
        validate_safe_identifier(reconciled_by, "reconciled_by", max_length=120)
        try:
            generation, run, slots, job = self._validated_recovery_state(
                image_generation_id
            )
            reason = self._recover_from_evidence(generation, run, slots)
            resumed = self._audit_and_resume_recovered_job(
                image_generation_id,
                expected_job_id=job.id,
                reconciled_by=reconciled_by,
                reason=reason,
            )
            return self._recovery_result(image_generation_id, resumed, reason)
        except ImageError:
            raise
        except (FlowDispatchAmbiguousError, FlowGenerationRuntimeError):
            raise ImageRecoveryBlockedError from None
        except (
            IntegrityError,
            OSError,
            ValueError,
            FlowArtifactConflictError,
            FlowArtifactInvalidError,
            JobTransitionError,
        ):
            raise ImageRecoveryBlockedError from None

    def resolve_no_dispatch(
        self,
        image_generation_id: str,
        *,
        resolved_by: str,
        reason: str,
    ) -> ImageGenerationRecovery:
        """Audit an operator-proven no-dispatch outcome before allowing another attempt."""
        validate_safe_identifier(resolved_by, "resolved_by", max_length=120)
        if not reason or not reason.strip():
            raise ValueError("reason must not be empty")
        validate_safe_error_message(reason, "reason")
        try:
            job_id = self._resolve_no_dispatch_transaction(
                image_generation_id,
                resolved_by=resolved_by,
                reason=reason,
            )
            resumed = self._resume_recovered_job(
                image_generation_id,
                expected_job_id=job_id,
                reason="no_dispatch_proven",
            )
        except ImageError:
            raise
        except (
            IntegrityError,
            OSError,
            ValueError,
            FlowArtifactConflictError,
            FlowArtifactInvalidError,
            JobTransitionError,
        ):
            raise ImageRecoveryBlockedError from None
        return self._recovery_result(
            image_generation_id,
            resumed,
            "no_dispatch_proven",
        )

    def approve_candidate(self, candidate_id: str, approved_by: str) -> ImageCandidate:
        validate_safe_identifier(approved_by, "approved_by", max_length=120)
        now = self._utc(self._clock())

        def approve(session: Session) -> ImageCandidate:
            ownership = self._repository.candidate_with_generation_in_session(session, candidate_id)
            if ownership is None:
                raise ImageCandidateNotFoundError
            candidate, generation = ownership
            if candidate.review_status != "pending_review":
                raise ImageTransitionError
            if (
                self._repository.approved_candidate_for_scene_in_session(
                    session, generation.scene_variant_id
                )
                is not None
            ):
                raise ImageApprovedCandidateExistsError
            candidate.review_status = "approved"
            candidate.approved_at = now
            candidate.approved_by = approved_by
            candidate.updated_at = now
            session.flush()
            return self._candidate_to_domain(candidate)

        return self._review_transaction(approve)

    def reject_candidate(
        self, candidate_id: str, rejected_by: str, rejection_reason: str
    ) -> ImageCandidate:
        validate_safe_identifier(rejected_by, "rejected_by", max_length=120)
        if not rejection_reason or not rejection_reason.strip():
            raise ValueError("rejection_reason must not be empty")
        validate_safe_error_message(rejection_reason, "rejection_reason")
        now = self._utc(self._clock())

        def reject(session: Session) -> ImageCandidate:
            ownership = self._repository.candidate_with_generation_in_session(session, candidate_id)
            if ownership is None:
                raise ImageCandidateNotFoundError
            candidate, _generation = ownership
            if candidate.review_status != "pending_review":
                raise ImageTransitionError
            candidate.review_status = "rejected"
            candidate.rejected_at = now
            candidate.rejected_by = rejected_by
            candidate.rejection_reason = rejection_reason
            candidate.updated_at = now
            session.flush()
            return self._candidate_to_domain(candidate)

        return self._review_transaction(reject)

    def replace_approved_candidate(
        self, scene_variant_id: str, new_candidate_id: str, approved_by: str
    ) -> ImageCandidate:
        validate_safe_identifier(approved_by, "approved_by", max_length=120)
        now = self._utc(self._clock())

        def replace(session: Session) -> ImageCandidate:
            ownership = self._repository.candidate_with_generation_in_session(
                session, new_candidate_id
            )
            if ownership is None:
                raise ImageCandidateNotFoundError
            new_candidate, new_generation = ownership
            if new_generation.scene_variant_id != scene_variant_id:
                raise ImageCandidateSceneMismatchError
            if new_candidate.review_status not in {"pending_review", "rejected"}:
                raise ImageTransitionError
            approved = self._repository.approved_candidate_for_scene_in_session(
                session, scene_variant_id
            )
            if approved is None:
                raise ImageTransitionError
            old_candidate, _old_generation = approved
            old_candidate.review_status = "superseded"
            old_candidate.superseded_at = now
            old_candidate.superseded_by_candidate_id = new_candidate.id
            old_candidate.updated_at = now
            session.flush()
            new_candidate.review_status = "approved"
            new_candidate.approved_at = now
            new_candidate.approved_by = approved_by
            new_candidate.updated_at = now
            session.flush()
            self._candidate_to_domain(old_candidate)
            return self._candidate_to_domain(new_candidate)

        return self._review_transaction(replace)

    def _review_transaction(self, operation: Callable[[Session], ImageCandidate]) -> ImageCandidate:
        try:
            return self._repository.immediate_transaction(operation)
        except IntegrityError as exc:
            if "approved candidate already exists" in str(exc.orig):
                raise ImageApprovedCandidateExistsError from None
            raise ImageTransitionError from None

    def _generation_by_job_id(self, job_id: str) -> ImageGenerationRow | None:
        with self._sessions() as session:
            return session.scalar(
                select(ImageGenerationRow).where(ImageGenerationRow.job_id == job_id)
            )

    def _validated_recovery_state(
        self,
        image_generation_id: str,
    ) -> tuple[
        ImageGenerationRow,
        FlowGenerationRunRow,
        list[FlowCandidateSlotRow],
        JobRow,
    ]:
        with self._sessions() as session:
            return self._validated_recovery_state_in_session(
                session,
                image_generation_id,
            )

    def _validated_recovery_state_in_session(
        self,
        session: Session,
        image_generation_id: str,
    ) -> tuple[
        ImageGenerationRow,
        FlowGenerationRunRow,
        list[FlowCandidateSlotRow],
        JobRow,
    ]:
        generation = session.get(ImageGenerationRow, image_generation_id)
        if generation is None:
            raise ImageGenerationNotFoundError
        job = session.get(JobRow, generation.job_id)
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation.id
            )
        )
        if (
            generation.executor != "playwright_python"
            or job is None
            or run is None
            or job.job_type != "image.generate"
            or job.campaign_id != generation.campaign_id
            or job.scene_variant_id != generation.scene_variant_id
            or job.retry_safety != RetrySafety.RECONCILE_BEFORE_RETRY.value
            or job.status != "blocked"
            or job.worker_id is not None
            or job.lease_expires_at is not None
            or job.attempt_count >= job.max_attempts
            or run.required_candidate_count != 2
            or run.required_resolution != "2K"
            or run.provider_workspace_path is None
            or run.provider_workspace_fingerprint is None
            or not run.provider_action_approved_by
            or job.input_json
            != {"imageRequestFingerprint": generation.request_fingerprint}
        ):
            raise ImageRecoveryBlockedError
        try:
            self._generation_to_domain(generation)
            self._flow_run_to_domain(run)
            expected_job_fingerprint = JobSubmit(
                job_type=job.job_type,
                campaign_id=job.campaign_id,
                scene_variant_id=job.scene_variant_id,
                idempotency_key=job.idempotency_key,
                input=job.input_json,
                priority=job.priority,
                max_attempts=job.max_attempts,
                retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
            ).request_fingerprint
        except ValueError:
            raise ImageRecoveryBlockedError from None
        if job.request_fingerprint != expected_job_fingerprint:
            raise ImageRecoveryBlockedError
        campaign = session.get(CampaignRow, generation.campaign_id)
        scene = session.get(SceneVariantRow, generation.scene_variant_id)
        if campaign is None or scene is None or scene.campaign_id != campaign.id:
            raise ImageRecoveryBlockedError
        authorizations = list(
            session.scalars(
                select(JobEventRow).where(
                    JobEventRow.job_id == job.id,
                    JobEventRow.event_type == "job.authorized",
                )
            )
        )
        if (
            len(authorizations) != 1
            or authorizations[0].metadata_json
            != {
                "executor": "playwright_python",
                "approvedBy": run.provider_action_approved_by,
                "workspaceFingerprint": run.provider_workspace_fingerprint,
            }
            or authorizations[0].timestamp != run.provider_action_approved_at
        ):
            raise ImageRecoveryBlockedError
        expected_workspace_hash = hashlib.sha256(
            run.provider_workspace_path.encode("utf-8")
        ).hexdigest()
        if run.provider_workspace_fingerprint != expected_workspace_hash:
            raise ImageRecoveryBlockedError
        slots = list(
            session.scalars(
                select(FlowCandidateSlotRow)
                .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                .order_by(FlowCandidateSlotRow.slot_index)
            )
        )
        if [slot.slot_index for slot in slots] != [0, 1]:
            raise ImageRecoveryBlockedError
        try:
            for slot in slots:
                self._flow_slot_to_domain(slot)
        except ValueError:
            raise ImageRecoveryBlockedError from None
        fingerprints = [
            slot.provider_slot_fingerprint
            for slot in slots
            if slot.provider_slot_fingerprint is not None
        ]
        if len(set(fingerprints)) != len(fingerprints):
            raise ImageRecoveryBlockedError
        self._validate_recovery_state_compatibility(generation, run, slots)
        if generation.reference_image_path is None or generation.reference_image_sha256 is None:
            raise ImageRecoveryBlockedError
        reference = (self._work_root / generation.reference_image_path).resolve(strict=True)
        reference.relative_to(self._work_root)
        if hashlib.sha256(reference.read_bytes()).hexdigest() != generation.reference_image_sha256:
            raise ImageRecoveryBlockedError
        if (
            hashlib.sha256(generation.prompt_snapshot.encode("utf-8")).hexdigest()
            != generation.prompt_sha256
        ):
            raise ImageRecoveryBlockedError
        if run.grid_evidence_path is not None:
            evidence = (self._work_root / run.grid_evidence_path).resolve(strict=True)
            evidence.relative_to(self._work_root)
            if hashlib.sha256(evidence.read_bytes()).hexdigest() != run.grid_evidence_sha256:
                raise ImageRecoveryBlockedError
        for slot in slots:
            if slot.state == "downloaded":
                self._recovery_downloaded_facts(generation, slot)
            elif slot.state == "ingested":
                self._validate_recovery_ingested_in_session(session, generation, slot)
        return generation, run, slots, job

    def _validate_recovery_run_identity(
        self,
        session: Session,
        image_generation_id: str,
        run_id: str,
    ) -> None:
        _generation, run, _slots, _job = self._validated_recovery_state_in_session(
            session,
            image_generation_id,
        )
        if run.id != run_id:
            raise ImageRecoveryBlockedError

    @staticmethod
    def _recovery_slot_identity(slot: FlowCandidateSlotRow) -> tuple[object, ...]:
        return (
            slot.id,
            slot.slot_index,
            slot.state,
            slot.provider_slot_fingerprint,
            slot.download_intent_at,
            slot.staging_path,
            slot.staged_sha256,
            slot.image_candidate_id,
        )

    @staticmethod
    def _validate_recovery_state_compatibility(
        generation: ImageGenerationRow,
        run: FlowGenerationRunRow,
        slots: list[FlowCandidateSlotRow],
    ) -> None:
        states = [slot.state for slot in slots]
        if generation.provider_state == "failed" or run.stage == "failed":
            raise ImageRecoveryBlockedError
        if generation.provider_state == "completed":
            if (
                run.stage != "completed"
                or generation.completed_at is None
                or generation.dispatched_at is None
            ):
                raise ImageRecoveryBlockedError
        elif (
            generation.provider_state not in {"queued", "generating", "blocked"}
            or run.stage == "completed"
            or generation.completed_at is not None
        ):
            raise ImageRecoveryBlockedError
        if (run.dispatch_confirmed_at is None) != (generation.dispatched_at is None):
            raise ImageRecoveryBlockedError
        if run.stage in {"prepared", "inputs_verified", "dispatch_intent_recorded", "ambiguous"}:
            if states != ["pending", "pending"] or run.grid_evidence_path is not None:
                raise ImageRecoveryBlockedError
        elif run.stage == "dispatch_confirmed":
            if states != ["pending", "pending"] or run.grid_evidence_path is not None:
                raise ImageRecoveryBlockedError
        elif run.stage == "candidates_observed":
            if states != ["observed", "observed"] or run.grid_evidence_path is None:
                raise ImageRecoveryBlockedError
        elif run.stage == "downloading":
            if (
                any(
                    state
                    not in {"observed", "download_intent_recorded", "downloaded", "ingested"}
                    for state in states
                )
                or run.grid_evidence_path is None
            ):
                raise ImageRecoveryBlockedError
        elif run.stage == "completed":
            if states != ["ingested", "ingested"] or run.grid_evidence_path is None:
                raise ImageRecoveryBlockedError
        elif run.stage == "blocked":
            progressed = any(state != "pending" for state in states)
            if progressed and run.grid_evidence_path is None:
                raise ImageRecoveryBlockedError

    @staticmethod
    def _validate_reconciliation_reason_state(
        reason: FlowReconciliationReason,
        generation: ImageGenerationRow,
        run: FlowGenerationRunRow,
        slots: list[FlowCandidateSlotRow],
    ) -> None:
        states = [slot.state for slot in slots]
        if reason == "no_dispatch_proven":
            valid = (
                generation.provider_state == "queued"
                and generation.dispatched_at is None
                and generation.completed_at is None
                and run.stage == "prepared"
                and run.dispatch_intent_at is None
                and run.dispatch_confirmed_at is None
                and run.grid_evidence_path is None
                and states == ["pending", "pending"]
            )
        elif reason == "existing_dispatch_reconciled":
            valid = (
                generation.provider_state in {"generating", "completed"}
                and generation.dispatched_at is not None
                and run.dispatch_intent_at is not None
                and run.dispatch_confirmed_at is not None
                and run.stage
                in {"dispatch_confirmed", "candidates_observed", "downloading", "completed"}
            )
        else:
            valid = (
                generation.provider_state == "completed"
                and generation.dispatched_at is not None
                and generation.completed_at is not None
                and run.stage == "completed"
                and states == ["ingested", "ingested"]
            )
        if not valid:
            raise ImageRecoveryBlockedError

    def _recover_from_evidence(
        self,
        generation: ImageGenerationRow,
        run: FlowGenerationRunRow,
        slots: list[FlowCandidateSlotRow],
    ) -> FlowReconciliationReason:
        if all(slot.state == "ingested" for slot in slots):
            for slot in slots:
                self._validate_recovery_ingested(generation, slot)
            self._complete_recovered_generation(generation.id, run.id)
            return "completed_generation_reconciled"

        if any(slot.state == "downloaded" for slot in slots):
            downloaded: list[tuple[FlowCandidateSlotRow, FlowArtifactFacts]] = []
            for slot in slots:
                if slot.state == "ingested":
                    self._validate_recovery_ingested(generation, slot)
                elif slot.state == "downloaded":
                    facts = self._recovery_downloaded_facts(generation, slot)
                    downloaded.append((slot, facts))
                elif slot.state not in {"observed", "download_intent_recorded"}:
                    raise ImageRecoveryBlockedError
            for slot, facts in downloaded:
                self._ingest_recovered_slot(generation, run.id, slot.slot_index, facts)
            with self._sessions() as session:
                slots = list(
                    session.scalars(
                        select(FlowCandidateSlotRow)
                        .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                        .order_by(FlowCandidateSlotRow.slot_index)
                    )
                )
            if [slot.state for slot in slots] == ["ingested", "ingested"]:
                self._complete_recovered_generation(generation.id, run.id)
                return "staged_artifact_reconciled"

        if run.dispatch_intent_at is None:
            if run.dispatch_confirmed_at is not None or any(
                slot.state != "pending" for slot in slots
            ):
                raise ImageRecoveryBlockedError
            self._reset_pre_intent_run(generation.id, run.id)
            return "no_dispatch_proven"

        workspace = FlowWorkspaceIdentity(
            workspace_path=run.provider_workspace_path or "",
            fingerprint=run.provider_workspace_fingerprint or "",
        )
        expected_fingerprints = tuple(
            slot.provider_slot_fingerprint
            for slot in slots
            if slot.provider_slot_fingerprint is not None
        )
        artifact_context = FlowGenerationArtifactContext(
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            work_root=self._work_root,
            workspace=workspace,
        )
        runtime = self._recovery_runtime_factory(artifact_context)
        if not runtime.recover_dispatch(
            workspace,
            expected_fingerprints=expected_fingerprints,
        ):
            raise ImageRecoveryBlockedError
        self._promote_recovered_dispatch(generation.id, run.id, slots)
        intent_slots = [slot for slot in slots if slot.state == "download_intent_recorded"]
        if intent_slots:
            sink = _RecoveryDownloadCheckpointSink(
                self._sessions,
                run_id=run.id,
                clock=lambda: self._utc(self._clock()),
                validate_locked_state=lambda session: self._validate_recovery_run_identity(
                    session,
                    generation.id,
                    run.id,
                ),
            )
            for slot in intent_slots:
                facts = runtime.download_slot(
                    slot.slot_index,
                    cast(FlowGenerationDownloadCheckpointSink, sink),
                )
                self._ingest_recovered_slot(
                    generation,
                    run.id,
                    slot.slot_index,
                    facts,
                )
            with self._sessions() as session:
                refreshed = list(
                    session.scalars(
                        select(FlowCandidateSlotRow)
                        .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                        .order_by(FlowCandidateSlotRow.slot_index)
                    )
                )
            if [slot.state for slot in refreshed] == ["ingested", "ingested"]:
                self._complete_recovered_generation(generation.id, run.id)
        return "existing_dispatch_reconciled"

    def _reset_pre_intent_run(self, generation_id: str, run_id: str) -> None:
        def reset(session: Session) -> None:
            generation, run, slots, _job = self._validated_recovery_state_in_session(
                session,
                generation_id,
            )
            if (
                run.id != run_id
                or generation.provider_state not in {"queued", "generating", "blocked"}
                or generation.dispatched_at is not None
                or generation.completed_at is not None
                or run.dispatch_intent_at is not None
                or run.dispatch_confirmed_at is not None
                or run.stage not in {"prepared", "inputs_verified", "blocked"}
                or [slot.slot_index for slot in slots] != [0, 1]
            ):
                raise ImageRecoveryBlockedError
            run.stage = "prepared"
            run.last_failure_code = None
            run.updated_at = self._utc(self._clock())
            generation.provider_state = "queued"
            generation.updated_at = run.updated_at
            session.flush()

        self._repository.immediate_transaction(reset)

    def _promote_recovered_dispatch(
        self,
        generation_id: str,
        run_id: str,
        slots: list[FlowCandidateSlotRow],
    ) -> None:
        slot_states = [slot.state for slot in slots]
        expected_slots = [self._recovery_slot_identity(slot) for slot in slots]

        def promote(session: Session) -> None:
            generation, run, current_slots, _job = (
                self._validated_recovery_state_in_session(session, generation_id)
            )
            if (
                run.id != run_id
                or run.dispatch_intent_at is None
                or [self._recovery_slot_identity(slot) for slot in current_slots]
                != expected_slots
            ):
                raise ImageRecoveryBlockedError
            now = self._utc(self._clock())
            if run.dispatch_confirmed_at is None:
                run.dispatch_confirmed_at = now
            if any(state in {"download_intent_recorded", "downloaded", "ingested"} for state in slot_states):
                run.stage = "downloading"
            elif any(state == "observed" for state in slot_states):
                run.stage = "candidates_observed"
            else:
                run.stage = "dispatch_confirmed"
            run.last_failure_code = None
            run.updated_at = now
            generation.provider_state = "generating"
            generation.dispatched_at = generation.dispatched_at or now
            generation.updated_at = now
            session.flush()

        self._repository.immediate_transaction(promote)

    def _recovery_downloaded_facts(
        self,
        generation: ImageGenerationRow,
        slot: FlowCandidateSlotRow,
    ) -> FlowArtifactFacts:
        if slot.staging_path is None or slot.staged_sha256 is None:
            raise ImageRecoveryBlockedError
        staging = (self._work_root / slot.staging_path).resolve(strict=False)
        staging.relative_to(self._work_root)
        staged: FlowArtifactFacts | None = None
        if staging.exists():
            staged = inspect_flow_artifact(staging)
            if staged.sha256 != slot.staged_sha256:
                raise ImageRecoveryBlockedError
        matching: list[FlowArtifactFacts] = []
        existing_final_count = 0
        for image_format in ("png", "jpeg", "webp"):
            final = resolve_flow_final_path(
                work_root=self._work_root,
                campaign_id=generation.campaign_id,
                scene_variant_id=generation.scene_variant_id,
                generation_number=generation.generation_number,
                candidate_index=slot.slot_index,
                image_format=image_format,
            )
            if final.exists():
                existing_final_count += 1
                facts = inspect_flow_artifact(final)
                if facts.sha256 == slot.staged_sha256:
                    matching.append(facts)
        if len(matching) == 1:
            if staged is not None and staged != matching[0]:
                raise ImageRecoveryBlockedError
            return matching[0]
        if len(matching) > 1 or existing_final_count or staged is None:
            raise ImageRecoveryBlockedError
        final = resolve_flow_final_path(
            work_root=self._work_root,
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            candidate_index=slot.slot_index,
            image_format=staged.format,
        )
        published = publish_flow_artifact_exclusive(
            staging,
            final,
            trusted_root=self._work_root,
        )
        if published != staged:
            raise ImageRecoveryBlockedError
        return published

    def _ingest_recovered_slot(
        self,
        generation: ImageGenerationRow,
        run_id: str,
        slot_index: int,
        facts: FlowArtifactFacts,
    ) -> None:
        final = resolve_flow_final_path(
            work_root=self._work_root,
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            candidate_index=slot_index,
            image_format=facts.format,
        )
        if inspect_flow_artifact(final) != facts:
            raise ImageRecoveryBlockedError
        candidate = ImageCandidate(
            image_candidate_id=str(uuid4()),
            image_generation_id=generation.id,
            candidate_index=slot_index,
            source_path=final.relative_to(self._work_root).as_posix(),
            sha256=facts.sha256,
            width=facts.width,
            height=facts.height,
            size_bytes=facts.size_bytes,
            format=facts.format,
            review_status="pending_review",
            created_at=self._utc(self._clock()),
            updated_at=self._utc(self._clock()),
        )

        def ingest(session: Session) -> None:
            current_generation, run, _slots, _job = (
                self._validated_recovery_state_in_session(session, generation.id)
            )
            if (
                run.id != run_id
                or current_generation.id != generation.id
                or inspect_flow_artifact(final) != facts
            ):
                raise ImageRecoveryBlockedError
            slot = session.scalar(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == run_id,
                    FlowCandidateSlotRow.slot_index == slot_index,
                )
            )
            if (
                slot is None
                or slot.state != "downloaded"
                or slot.staged_sha256 != facts.sha256
                or slot.image_candidate_id is not None
            ):
                raise ImageRecoveryBlockedError
            ImageRepository.create_candidate_in_session(session, candidate)
            slot.image_candidate_id = candidate.image_candidate_id
            slot.state = "ingested"
            slot.updated_at = candidate.updated_at
            session.flush()

        self._repository.immediate_transaction(ingest)

    def _validate_recovery_ingested(
        self,
        generation: ImageGenerationRow,
        slot: FlowCandidateSlotRow,
    ) -> None:
        with self._sessions() as session:
            self._validate_recovery_ingested_in_session(session, generation, slot)

    def _validate_recovery_ingested_in_session(
        self,
        session: Session,
        generation: ImageGenerationRow,
        slot: FlowCandidateSlotRow,
    ) -> None:
        if slot.image_candidate_id is None:
            raise ImageRecoveryBlockedError
        candidate = session.get(ImageCandidateRow, slot.image_candidate_id)
        if (
            candidate is None
            or candidate.image_generation_id != generation.id
            or candidate.candidate_index != slot.slot_index
        ):
            raise ImageRecoveryBlockedError
        artifact = (self._work_root / candidate.source_path).resolve(strict=True)
        artifact.relative_to(self._work_root)
        facts = inspect_flow_artifact(artifact)
        if (
            candidate.sha256,
            candidate.width,
            candidate.height,
            candidate.size_bytes,
            candidate.format,
        ) != (facts.sha256, facts.width, facts.height, facts.size_bytes, facts.format):
            raise ImageRecoveryBlockedError
        expected = resolve_flow_final_path(
            work_root=self._work_root,
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            candidate_index=slot.slot_index,
            image_format=facts.format,
        )
        if (
            artifact != expected
            or slot.provider_slot_fingerprint is None
            or slot.staging_path is None
            or slot.staged_sha256 != facts.sha256
        ):
            raise ImageRecoveryBlockedError

    def _complete_recovered_generation(self, generation_id: str, run_id: str) -> None:
        def complete(session: Session) -> None:
            generation, run, slots, _job = self._validated_recovery_state_in_session(
                session,
                generation_id,
            )
            if (
                run.id != run_id
                or [slot.state for slot in slots] != ["ingested", "ingested"]
            ):
                raise ImageRecoveryBlockedError
            now = self._utc(self._clock())
            run.stage = "completed"
            run.last_failure_code = None
            run.updated_at = now
            generation.provider_state = "completed"
            generation.completed_at = generation.completed_at or now
            generation.dispatched_at = generation.dispatched_at or run.dispatch_confirmed_at
            generation.updated_at = now
            session.flush()

        self._repository.immediate_transaction(complete)

    def _resolve_no_dispatch_transaction(
        self,
        image_generation_id: str,
        *,
        resolved_by: str,
        reason: str,
    ) -> str:
        def resolve(session: Session) -> str:
            generation, run, _slots, job = self._validated_recovery_state_in_session(
                session,
                image_generation_id,
            )
            existing = session.scalar(
                select(JobEventRow)
                .where(
                    JobEventRow.job_id == job.id,
                    JobEventRow.event_type == "job.flow_dispatch_resolved",
                )
                .order_by(JobEventRow.sequence.desc())
            )
            already_committed = (
                run.stage == "prepared"
                and run.dispatch_intent_at is None
                and run.dispatch_confirmed_at is None
                and existing is not None
                and existing.metadata_json.get("nextDispatchAttemptNumber")
                == run.dispatch_attempt_number
                and existing.metadata_json.get("resolvedBy") == resolved_by
                and existing.metadata_json.get("reason") == reason
            )
            if already_committed:
                return job.id
            if (
                generation.provider_state != "blocked"
                or run.stage != "ambiguous"
                or run.dispatch_intent_at is None
                or run.dispatch_confirmed_at is not None
            ):
                raise ImageTransitionError
            now = self._utc(self._clock())
            previous_attempt = run.dispatch_attempt_number
            previous_intent = self._utc(run.dispatch_intent_at)
            previous_confirmation = (
                None
                if run.dispatch_confirmed_at is None
                else self._utc(run.dispatch_confirmed_at).isoformat()
            )
            run.dispatch_attempt_number += 1
            run.stage = "prepared"
            run.dispatch_intent_at = None
            run.dispatch_confirmed_at = None
            run.last_failure_code = None
            run.updated_at = now
            generation.provider_state = "queued"
            generation.updated_at = now
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=job.id,
                    event_type="job.flow_dispatch_resolved",
                    timestamp=now,
                    metadata_json={
                        "previousDispatchAttemptNumber": previous_attempt,
                        "previousDispatchIntentAt": previous_intent.isoformat(),
                        "previousDispatchConfirmedAt": previous_confirmation,
                        "nextDispatchAttemptNumber": run.dispatch_attempt_number,
                        "resolvedBy": resolved_by,
                        "reason": reason,
                    },
                )
            )
            session.flush()
            return job.id

        return self._repository.immediate_transaction(resolve)

    def _audit_and_resume_recovered_job(
        self,
        image_generation_id: str,
        *,
        expected_job_id: str,
        reconciled_by: str,
        reason: FlowReconciliationReason,
    ) -> Job:
        def audit_and_resume(session: Session) -> str:
            generation, run, slots, job = (
                self._validated_recovery_state_in_session(
                    session,
                    image_generation_id,
                )
            )
            if job.id != expected_job_id:
                raise ImageRecoveryBlockedError
            self._validate_reconciliation_reason_state(reason, generation, run, slots)
            events = list(
                session.scalars(
                    select(JobEventRow).where(
                        JobEventRow.job_id == job.id,
                        JobEventRow.event_type == "job.flow_recovery_reconciled",
                    )
                )
            )
            if not any(event.metadata_json.get("reason") == reason for event in events):
                session.add(
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=job.id,
                        event_type="job.flow_recovery_reconciled",
                        timestamp=self._utc(self._clock()),
                        metadata_json={
                            "reconciledBy": reconciled_by,
                            "reason": reason,
                        },
                    )
                )
            self._jobs._resume_reconciled_job_in_session(
                session,
                job,
                reason=reason,
            )
            session.flush()
            return job.id

        try:
            job_id = self._repository.immediate_transaction(audit_and_resume)
        except JobTransitionError:
            raise ImageRecoveryBlockedError from None
        return self._jobs.get_job(job_id)

    def _resume_recovered_job(
        self,
        image_generation_id: str,
        *,
        expected_job_id: str,
        reason: FlowReconciliationReason,
    ) -> Job:
        def resume(session: Session) -> str:
            generation, run, slots, job = self._validated_recovery_state_in_session(
                session,
                image_generation_id,
            )
            if job.id != expected_job_id:
                raise ImageRecoveryBlockedError
            self._validate_reconciliation_reason_state(reason, generation, run, slots)
            self._jobs._resume_reconciled_job_in_session(
                session,
                job,
                reason=reason,
            )
            session.flush()
            return job.id

        try:
            job_id = self._repository.immediate_transaction(resume)
        except JobTransitionError:
            raise ImageRecoveryBlockedError from None
        return self._jobs.get_job(job_id)

    def _recovery_result(
        self,
        image_generation_id: str,
        job: Job,
        reason: FlowReconciliationReason,
    ) -> ImageGenerationRecovery:
        with self._sessions() as session:
            generation = session.get(ImageGenerationRow, image_generation_id)
            run = session.scalar(
                select(FlowGenerationRunRow).where(
                    FlowGenerationRunRow.image_generation_id == image_generation_id
                )
            )
            if generation is None or run is None:
                raise ImageRecoveryBlockedError
            slots = list(
                session.scalars(
                    select(FlowCandidateSlotRow)
                    .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                    .order_by(FlowCandidateSlotRow.slot_index)
                )
            )
            if len(slots) != 2:
                raise ImageRecoveryBlockedError
            return ImageGenerationRecovery(
                generation=self._generation_to_domain(generation),
                flow_run=self._flow_run_to_domain(run),
                slots=(
                    self._flow_slot_to_domain(slots[0]),
                    self._flow_slot_to_domain(slots[1]),
                ),
                job=job,
                reason=reason,
            )

    @classmethod
    def _generation_to_domain(cls, row: ImageGenerationRow) -> ImageGeneration:
        return ImageGeneration(
            image_generation_id=row.id,
            campaign_id=row.campaign_id,
            scene_variant_id=row.scene_variant_id,
            job_id=row.job_id,
            generation_number=row.generation_number,
            idempotency_key=row.idempotency_key,
            request_fingerprint=row.request_fingerprint,
            prompt_snapshot=row.prompt_snapshot,
            prompt_sha256=row.prompt_sha256,
            reference_image_path=row.reference_image_path,
            reference_image_sha256=row.reference_image_sha256,
            provider=cast(ImageProvider, row.provider),
            executor=cast(ImageExecutor, row.executor),
            provider_state=cast(ImageGenerationState, row.provider_state),
            created_at=cls._utc(row.created_at),
            updated_at=cls._utc(row.updated_at),
            dispatched_at=cls._optional_utc(row.dispatched_at),
            completed_at=cls._optional_utc(row.completed_at),
        )

    @classmethod
    def _candidate_to_domain(cls, row: ImageCandidateRow) -> ImageCandidate:
        return ImageCandidate(
            image_candidate_id=row.id,
            image_generation_id=row.image_generation_id,
            candidate_index=row.candidate_index,
            source_path=row.source_path,
            sha256=row.sha256,
            width=row.width,
            height=row.height,
            size_bytes=row.size_bytes,
            format=row.format,
            review_status=cast(ImageCandidateReviewStatus, row.review_status),
            approved_at=cls._optional_utc(row.approved_at),
            approved_by=row.approved_by,
            rejected_at=cls._optional_utc(row.rejected_at),
            rejected_by=row.rejected_by,
            rejection_reason=row.rejection_reason,
            superseded_at=cls._optional_utc(row.superseded_at),
            superseded_by_candidate_id=row.superseded_by_candidate_id,
            created_at=cls._utc(row.created_at),
            updated_at=cls._utc(row.updated_at),
        )

    @classmethod
    def _flow_run_to_domain(cls, row: FlowGenerationRunRow) -> FlowGenerationRun:
        return FlowGenerationRun(
            flow_generation_run_id=row.id,
            image_generation_id=row.image_generation_id,
            stage=cast(FlowGenerationStage, row.stage),
            required_candidate_count=2,
            required_resolution="2K",
            provider_workspace_path=row.provider_workspace_path,
            provider_workspace_fingerprint=row.provider_workspace_fingerprint,
            dispatch_attempt_number=row.dispatch_attempt_number,
            dispatch_intent_at=cls._optional_utc(row.dispatch_intent_at),
            dispatch_confirmed_at=cls._optional_utc(row.dispatch_confirmed_at),
            grid_evidence_path=row.grid_evidence_path,
            grid_evidence_sha256=row.grid_evidence_sha256,
            last_failure_code=row.last_failure_code,
            provider_action_approved_by=row.provider_action_approved_by,
            provider_action_approved_at=cls._utc(row.provider_action_approved_at),
            created_at=cls._utc(row.created_at),
            updated_at=cls._utc(row.updated_at),
        )

    @classmethod
    def _flow_slot_to_domain(cls, row: FlowCandidateSlotRow) -> FlowCandidateSlot:
        return FlowCandidateSlot(
            flow_candidate_slot_id=row.id,
            flow_generation_run_id=row.flow_generation_run_id,
            slot_index=row.slot_index,
            provider_slot_fingerprint=row.provider_slot_fingerprint,
            state=cast(FlowCandidateSlotState, row.state),
            download_intent_at=cls._optional_utc(row.download_intent_at),
            staging_path=row.staging_path,
            staged_sha256=row.staged_sha256,
            image_candidate_id=row.image_candidate_id,
            created_at=cls._utc(row.created_at),
            updated_at=cls._utc(row.updated_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _optional_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else cls._utc(value)

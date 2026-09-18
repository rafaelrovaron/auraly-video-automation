"""Durable, fail-closed bridge from an authorized image Job to Flow."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, SceneVariantRow
from auraly_pipeline.flow.artifacts import (
    FlowArtifactConflictError,
    FlowArtifactFacts,
    FlowArtifactInvalidError,
    capture_flow_reference,
    inspect_flow_artifact,
    resolve_flow_final_path,
)
from auraly_pipeline.flow.config import (
    resolve_flow_generation_config,
    resolve_flow_runtime_config,
)
from auraly_pipeline.flow.generation import (
    FlowGenerationArtifactContext,
    FlowGenerationRequest,
    FlowGenerationRuntime,
)
from auraly_pipeline.flow.domain import (
    FlowAuthenticationTimeoutError,
    FlowDiagnosticSanitizationError,
    FlowRuntimeBusyError,
    FlowUnexpectedStateError,
)
from auraly_pipeline.flow.generation_domain import (
    FlowCandidateObservation,
    FlowDispatchAmbiguousError,
    FlowDownloadCorrelationError,
    FlowGenerationObservation,
    FlowGenerationRuntimeError,
    FlowGenerationUiContractError,
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
    ImageCandidate,
)
from auraly_pipeline.images.flow_repository import (
    FlowCheckpointBusyError,
    FlowCheckpointConflictError,
    FlowCheckpointRepository,
)
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext
from auraly_pipeline.metadata_security import (
    validate_safe_error_message,
    validate_safe_identifier,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_database_concurrency_error(error: DBAPIError) -> bool:
    original = error.orig
    sqlite_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(sqlite_code, int) and sqlite_code & 0xFF in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    message = str(original).casefold()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "database schema is locked",
            "database is busy",
        )
    )


def _build_flow_runtime(context: FlowGenerationArtifactContext) -> FlowGenerationRuntime:
    """Production construction has no target or browser override seam."""
    return FlowGenerationRuntime(
        resolve_flow_generation_config(),
        runtime_config=resolve_flow_runtime_config(),
        artifact_context=context,
    )


class FlowGenerationCheckpointSink:
    """Commit a single trusted runtime fact per durable checkpoint boundary."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        run_id: str,
        clock: Callable[[], datetime],
        initial_stage: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._repository = FlowCheckpointRepository(sessions)
        self._run_id = run_id
        self._clock = clock
        if initial_stage is None:
            run = self._repository.get_run(self._run_id)
            if run is None:
                raise ValueError("Flow generation run is missing")
            initial_stage = run.stage
        self._run_stage = initial_stage

    @property
    def run_stage(self) -> str:
        return self._run_stage

    def set_workspace(self, workspace: FlowWorkspaceIdentity) -> None:
        self._repository.set_workspace(
            self._run_id,
            expected_stage="prepared",
            workspace_path=workspace.workspace_path,
            workspace_fingerprint=workspace.fingerprint,
            now=self._clock(),
        )

    def record_inputs_verified(self, observation: FlowGenerationObservation) -> None:
        if not (observation.reference_verified and observation.prompt_verified):
            raise ValueError("unverified Flow inputs cannot be checkpointed")
        self._repository.transition_run(
            self._run_id,
            expected_stage="prepared",
            target_stage="inputs_verified",
            now=self._clock(),
        )
        self._run_stage = "inputs_verified"

    def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None:
        now = self._clock()
        self._repository.transition_run(
            self._run_id,
            expected_stage="inputs_verified",
            target_stage="dispatch_intent_recorded",
            updates={
                "provider_workspace_path": workspace.workspace_path,
                "provider_workspace_fingerprint": workspace.fingerprint,
                "dispatch_intent_at": now,
            },
            now=now,
        )
        self._run_stage = "dispatch_intent_recorded"

    def record_dispatch_confirmed(self, observation: FlowGenerationObservation) -> None:
        if not (observation.reference_verified and observation.prompt_verified):
            raise ValueError("unverified Flow inputs cannot confirm dispatch")
        self._repository.confirm_dispatch(
            self._run_id,
            expected_stage="dispatch_intent_recorded",
            now=self._clock(),
        )
        self._run_stage = "dispatch_confirmed"

    def bind_candidate_slot(self, slot_index: int, observation: FlowCandidateObservation) -> None:
        if slot_index not in {0, 1} or observation.semantic_order != slot_index:
            raise ValueError("unexpected Flow candidate slot")
        self._repository.transition_slot(
            self._run_id,
            slot_index,
            expected_state="pending",
            target_state="observed",
            updates={"provider_slot_fingerprint": observation.fingerprint},
            now=self._clock(),
        )

    def record_candidates_observed(self, evidence: object) -> None:
        relative_path = getattr(evidence, "relative_path", None)
        sha256 = getattr(evidence, "sha256", None)
        if not isinstance(relative_path, str) or not isinstance(sha256, str):
            raise ValueError("invalid Flow grid evidence")
        self._repository.transition_run(
            self._run_id,
            expected_stage="dispatch_confirmed",
            target_stage="candidates_observed",
            updates={"grid_evidence_path": relative_path, "grid_evidence_sha256": sha256},
            now=self._clock(),
        )
        self._run_stage = "candidates_observed"

    def candidate_fingerprint(self, slot_index: int) -> str:
        slot = self._repository.get_slot(self._run_id, slot_index)
        if slot is None or slot.state not in {
            "observed",
            "download_intent_recorded",
            "downloaded",
        }:
            raise ValueError("candidate slot is not downloadable")
        if slot.provider_slot_fingerprint is None:
            raise ValueError("candidate slot is unbound")
        return slot.provider_slot_fingerprint

    def record_download_intent(self, slot_index: int, fingerprint: str) -> None:
        self._repository.record_download_intent(
            self._run_id,
            slot_index,
            expected_state="observed",
            expected_fingerprint=fingerprint,
            now=self._clock(),
        )
        self._run_stage = "downloading"

    def record_downloaded(self, slot_index: int, *, relative_path: str, sha256: str) -> None:
        fingerprint = self.candidate_fingerprint(slot_index)
        self._repository.record_downloaded(
            self._run_id,
            slot_index,
            expected_state="download_intent_recorded",
            expected_fingerprint=fingerprint,
            relative_path=relative_path,
            sha256=sha256,
            now=self._clock(),
        )


class FlowImageGenerateHandler:
    retry_safety = RetrySafety.RECONCILE_BEFORE_RETRY

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        work_root: Path,
        clock: Callable[[], datetime] | None = None,
        _runtime_factory: Callable[
            [FlowGenerationArtifactContext], FlowGenerationRuntime
        ] = _build_flow_runtime,
    ) -> None:
        self._sessions = session_factory
        self._checkpoints = FlowCheckpointRepository(session_factory)
        self._work_root = work_root.resolve()
        self._clock = clock or _utc_now
        self._runtime_factory = _runtime_factory

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        validated = self._validate(context)
        if validated is None:
            return self._terminal("image_job_integrity_failed")
        generation, run, slots, reference, workspace = validated
        if run.stage == "completed":
            return self._completed_if_intact(generation, slots)
        if workspace is None:
            return self._terminal("image_job_integrity_failed")
        sink = FlowGenerationCheckpointSink(
            self._sessions,
            run_id=run.id,
            clock=self._clock,
            initial_stage=run.stage,
        )
        artifact_context = FlowGenerationArtifactContext(
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            work_root=self._work_root,
            workspace=workspace,
        )
        try:
            self._mark_generating(generation.id)
            runtime = self._runtime_factory(artifact_context)
            if run.stage == "prepared":
                runtime.prepare_and_dispatch(
                    FlowGenerationRequest(
                        reference=reference,
                        prompt_snapshot=generation.prompt_snapshot,
                        prompt_sha256=generation.prompt_sha256,
                        workspace=workspace,
                    ),
                    sink,
                )
                run = self._run(run.id)
            elif run.stage in {
                "dispatch_intent_recorded",
                "dispatch_confirmed",
                "candidates_observed",
                "downloading",
                "ambiguous",
            }:
                runtime.reconcile(workspace, sink)
                if run.stage == "dispatch_intent_recorded":
                    self._set_run_failure(
                        run.id,
                        "ambiguous",
                        expected_stage=sink.run_stage,
                    )
                    return self._blocked("flow_dispatch_ambiguous")
                if run.stage == "ambiguous":
                    return self._blocked("flow_dispatch_ambiguous")
            else:
                return self._terminal("image_job_integrity_failed")
            if run.stage == "dispatch_confirmed":
                runtime.observe_candidates(sink)
                run = self._run(run.id)
            if run.stage in {"candidates_observed", "downloading"}:
                for slot_index in range(2):
                    slot = self._slot(run.id, slot_index)
                    if slot.state == "ingested":
                        self._validate_ingested(generation, slot)
                        continue
                    if slot.state == "downloaded":
                        self._ingest_slot(
                            generation,
                            run.id,
                            slot_index,
                            self._recover_downloaded_facts(generation, slot),
                        )
                        continue
                    if slot.state != "observed":
                        return self._blocked("flow_recovery_blocked")
                    facts = runtime.download_slot(slot_index, sink)
                    self._ingest_slot(generation, run.id, slot_index, facts)
                self._complete(generation.id, run.id)
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS,
                result={
                    "imageGenerationId": generation.id,
                    "candidateCount": 2,
                    "resolution": "2K",
                },
            )
        except FlowDispatchAmbiguousError:
            self._set_run_failure(run.id, "ambiguous", expected_stage=sink.run_stage)
            return self._blocked("flow_dispatch_ambiguous")
        except FlowGenerationUiContractError as error:
            if error.candidate_baseline_failure is None:
                return self._block_runtime_failure(
                    run.id,
                    sink,
                    self._generation_runtime_failure_code(error),
                )
            self._set_run_failure(
                run.id,
                "blocked",
                "flow_candidate_grid_ambiguous",
                expected_stage=sink.run_stage,
            )
            result: dict[str, JsonValue] = {
                "candidateBaselineFailure": error.candidate_baseline_failure.model_dump(
                    by_alias=True, mode="json"
                )
            }
            return self._blocked("flow_candidate_grid_ambiguous", result=result)
        except FlowDownloadCorrelationError:
            self._set_run_failure(
                run.id,
                "blocked",
                "flow_download_failed",
                expected_stage=sink.run_stage,
            )
            return self._blocked("flow_download_failed")
        except FlowArtifactInvalidError:
            self._set_run_failure(
                run.id,
                "blocked",
                "flow_artifact_invalid",
                expected_stage=sink.run_stage,
            )
            return self._blocked("flow_artifact_invalid")
        except FlowArtifactConflictError:
            self._set_run_failure(
                run.id,
                "blocked",
                "flow_artifact_conflict",
                expected_stage=sink.run_stage,
            )
            return self._blocked("flow_artifact_conflict")
        except FlowCheckpointConflictError:
            self._set_run_failure(
                run.id,
                "blocked",
                "flow_recovery_blocked",
                expected_stage=sink.run_stage,
            )
            return self._blocked("flow_recovery_blocked")
        except DBAPIError as error:
            if not _is_database_concurrency_error(error):
                raise
            self._set_run_failure(
                run.id,
                "blocked",
                "flow_recovery_blocked",
                expected_stage=sink.run_stage,
            )
            return self._blocked("flow_recovery_blocked")
        except FlowRuntimeBusyError:
            return self._block_runtime_failure(run.id, sink, "flow_runtime_busy")
        except FlowAuthenticationTimeoutError:
            return self._block_runtime_failure(
                run.id,
                sink,
                "flow_authentication_required",
            )
        except FlowDiagnosticSanitizationError:
            return self._block_runtime_failure(
                run.id,
                sink,
                "flow_diagnostic_sanitization_failed",
            )
        except FlowUnexpectedStateError as error:
            code = (
                "flow_browser_close_failed"
                if error.failed_step == "close_browser"
                else "flow_ui_contract_failed"
            )
            return self._block_runtime_failure(run.id, sink, code)
        except FlowGenerationRuntimeError as error:
            return self._block_runtime_failure(
                run.id,
                sink,
                self._generation_runtime_failure_code(error),
            )

    @staticmethod
    def _generation_runtime_failure_code(error: FlowGenerationRuntimeError) -> str:
        if error.failed_step in {
            "upload_reference",
            "verify_reference",
            "fill_prompt",
            "verify_prompt",
        }:
            return "flow_input_verification_failed"
        if error.failed_step == "capture_grid_evidence":
            return "flow_diagnostic_sanitization_failed"
        if error.failed_step == "close_browser":
            return "flow_browser_close_failed"
        return "flow_ui_contract_failed"

    def _block_runtime_failure(
        self,
        run_id: str,
        sink: FlowGenerationCheckpointSink,
        code: str,
    ) -> JobExecutionResult:
        self._set_run_failure(
            run_id,
            "blocked",
            code,
            expected_stage=sink.run_stage,
        )
        return self._blocked(code)

    def _validate(self, context: JobExecutionContext):
        with self._sessions() as session:
            job = session.get(JobRow, context.job_id)
            generation = session.scalar(
                select(ImageGenerationRow).where(ImageGenerationRow.job_id == context.job_id)
            )
            if (
                job is None
                or generation is None
                or context.job_type != "image.generate"
                or generation.campaign_id != context.campaign_id
                or generation.executor != "playwright_python"
                or context.input != {"imageRequestFingerprint": generation.request_fingerprint}
                or job.job_type != context.job_type
                or job.campaign_id != generation.campaign_id
                or job.scene_variant_id != generation.scene_variant_id
                or job.input_json != context.input
                or job.retry_safety != RetrySafety.RECONCILE_BEFORE_RETRY.value
                or generation.reference_image_path is None
                or generation.reference_image_sha256 is None
            ):
                return None
            try:
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
                return None
            if job.request_fingerprint != expected_job_fingerprint:
                return None
            campaign = session.get(CampaignRow, generation.campaign_id)
            scene = session.get(SceneVariantRow, generation.scene_variant_id)
            if campaign is None or scene is None or scene.campaign_id != campaign.id:
                return None
            run = session.scalar(
                select(FlowGenerationRunRow).where(
                    FlowGenerationRunRow.image_generation_id == generation.id
                )
            )
            if (
                run is None
                or run.required_candidate_count != 2
                or run.required_resolution != "2K"
                or not run.provider_action_approved_by
                or run.provider_action_approved_at is None
                or run.provider_workspace_path is None
                or run.provider_workspace_fingerprint is None
                or run.stage in {"blocked", "failed", "inputs_verified"}
            ):
                return None
            slots = list(
                session.scalars(
                    select(FlowCandidateSlotRow)
                    .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                    .order_by(FlowCandidateSlotRow.slot_index)
                )
            )
            if [slot.slot_index for slot in slots] != [0, 1]:
                return None
            if any(slot.state == "download_intent_recorded" for slot in slots):
                return None
            if not self._persisted_flow_state_is_valid(
                session,
                job=job,
                generation=generation,
                run=run,
                slots=slots,
            ):
                return None
            authorizations = list(
                session.scalars(
                    select(JobEventRow).where(
                        JobEventRow.job_id == job.id,
                        JobEventRow.event_type == "job.provider_action_authorized",
                    )
                )
            )
            if (
                len(authorizations) != 1
                or authorizations[0].metadata_json
                != {
                    "executor": "playwright_python",
                    "approvedBy": run.provider_action_approved_by,
                    "candidateCount": 2,
                    "resolution": "2K",
                }
                or authorizations[0].timestamp != run.provider_action_approved_at
            ):
                return None
            try:
                lexical_reference_path = self._work_root / generation.reference_image_path
                reference_path = lexical_reference_path.resolve(strict=True)
                reference_path.relative_to(self._work_root)
                reference = capture_flow_reference(
                    lexical_reference_path,
                    generation.reference_image_sha256,
                )
                expected_workspace_fingerprint = hashlib.sha256(
                    run.provider_workspace_path.encode("utf-8")
                ).hexdigest()
                if run.provider_workspace_fingerprint != expected_workspace_fingerprint:
                    return None
                workspace = (
                    None
                    if run.provider_workspace_path is None
                    else FlowWorkspaceIdentity(
                        workspace_path=run.provider_workspace_path,
                        fingerprint=run.provider_workspace_fingerprint or "",
                    )
                )
            except (FlowArtifactInvalidError, OSError, ValueError):
                return None
            return generation, run, slots, reference, workspace

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _optional_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else cls._utc(value)

    @classmethod
    def _persisted_flow_state_is_valid(
        cls,
        session: Session,
        *,
        job: JobRow,
        generation: ImageGenerationRow,
        run: FlowGenerationRunRow,
        slots: list[FlowCandidateSlotRow],
    ) -> bool:
        try:
            FlowGenerationRun(
                flow_generation_run_id=run.id,
                image_generation_id=run.image_generation_id,
                stage=cast(FlowGenerationStage, run.stage),
                required_candidate_count=2,
                required_resolution="2K",
                provider_workspace_path=run.provider_workspace_path,
                provider_workspace_fingerprint=run.provider_workspace_fingerprint,
                dispatch_attempt_number=run.dispatch_attempt_number,
                dispatch_intent_at=cls._optional_utc(run.dispatch_intent_at),
                dispatch_confirmed_at=cls._optional_utc(run.dispatch_confirmed_at),
                grid_evidence_path=run.grid_evidence_path,
                grid_evidence_sha256=run.grid_evidence_sha256,
                last_failure_code=run.last_failure_code,
                provider_action_approved_by=run.provider_action_approved_by,
                provider_action_approved_at=cls._utc(run.provider_action_approved_at),
                created_at=cls._utc(run.created_at),
                updated_at=cls._utc(run.updated_at),
            )
            for slot in slots:
                FlowCandidateSlot(
                    flow_candidate_slot_id=slot.id,
                    flow_generation_run_id=slot.flow_generation_run_id,
                    slot_index=slot.slot_index,
                    provider_slot_fingerprint=slot.provider_slot_fingerprint,
                    state=cast(FlowCandidateSlotState, slot.state),
                    download_intent_at=cls._optional_utc(slot.download_intent_at),
                    staging_path=slot.staging_path,
                    staged_sha256=slot.staged_sha256,
                    image_candidate_id=slot.image_candidate_id,
                    created_at=cls._utc(slot.created_at),
                    updated_at=cls._utc(slot.updated_at),
                )
        except (TypeError, ValueError):
            return False

        states = [slot.state for slot in slots]
        if (
            run.image_generation_id != generation.id
            or any(slot.flow_generation_run_id != run.id for slot in slots)
            or any(cls._utc(slot.created_at) != cls._utc(run.created_at) for slot in slots)
        ):
            return False
        if (run.dispatch_confirmed_at is None) != (generation.dispatched_at is None):
            return False
        if run.dispatch_confirmed_at is not None and generation.dispatched_at is not None:
            if cls._utc(run.dispatch_confirmed_at) != cls._utc(generation.dispatched_at):
                return False
        for slot in slots:
            if slot.download_intent_at is not None and (
                run.dispatch_confirmed_at is None
                or cls._utc(slot.download_intent_at) < cls._utc(run.dispatch_confirmed_at)
            ):
                return False
        if generation.completed_at is not None and run.stage != "completed":
            return False
        if run.stage in {"prepared", "inputs_verified", "dispatch_intent_recorded", "ambiguous"}:
            if states != ["pending", "pending"] or run.grid_evidence_path is not None:
                return False
        elif run.stage == "dispatch_confirmed":
            if states != ["pending", "pending"] or run.grid_evidence_path is not None:
                return False
        elif run.stage == "candidates_observed":
            if states != ["observed", "observed"] or run.grid_evidence_path is None:
                return False
        elif run.stage == "downloading":
            if (
                any(state not in {"observed", "downloaded", "ingested"} for state in states)
                or run.grid_evidence_path is None
            ):
                return False
        elif run.stage == "completed":
            if states != ["ingested", "ingested"] or run.grid_evidence_path is None:
                return False

        resolutions = list(
            session.scalars(
                select(JobEventRow)
                .where(
                    JobEventRow.job_id == job.id,
                    JobEventRow.event_type == "job.flow_dispatch_resolved",
                )
                .order_by(JobEventRow.sequence)
            )
        )
        if len(resolutions) != run.dispatch_attempt_number - 1:
            return False
        previous_resolution_at = cls._utc(run.created_at)
        for previous_attempt, event in enumerate(resolutions, start=1):
            metadata = event.metadata_json
            if (
                metadata.get("previousDispatchAttemptNumber") != previous_attempt
                or metadata.get("nextDispatchAttemptNumber") != previous_attempt + 1
                or not isinstance(metadata.get("previousDispatchIntentAt"), str)
                or metadata.get("previousDispatchConfirmedAt") is not None
                or not isinstance(metadata.get("resolvedBy"), str)
                or not metadata.get("resolvedBy")
                or not isinstance(metadata.get("reason"), str)
                or not metadata.get("reason")
                or set(metadata)
                != {
                    "previousDispatchAttemptNumber",
                    "previousDispatchIntentAt",
                    "previousDispatchConfirmedAt",
                    "nextDispatchAttemptNumber",
                    "resolvedBy",
                    "reason",
                }
            ):
                return False
            try:
                intent_at = datetime.fromisoformat(metadata["previousDispatchIntentAt"])
                resolved_by = validate_safe_identifier(
                    metadata["resolvedBy"],
                    "resolved_by",
                    max_length=120,
                )
                validate_safe_error_message(metadata["reason"], "reason")
            except (TypeError, ValueError):
                return False
            event_at = cls._utc(event.timestamp)
            if (
                intent_at.tzinfo is None
                or cls._utc(intent_at) < cls._utc(run.created_at)
                or cls._utc(intent_at) > event_at
                or event_at < previous_resolution_at
                or resolved_by != metadata["resolvedBy"]
            ):
                return False
            previous_resolution_at = event_at
        return True

    def _mark_generating(self, generation_id: str) -> None:
        try:
            self._checkpoints.mark_generation_generating(
                generation_id,
                now=self._clock(),
            )
        except FlowCheckpointBusyError:
            raise
        except FlowCheckpointConflictError as error:
            raise FlowArtifactConflictError() from error

    def _run(self, run_id: str) -> FlowGenerationRunRow:
        row = self._checkpoints.get_run(run_id)
        if row is None:
            raise FlowArtifactConflictError()
        return row

    def _slot(self, run_id: str, index: int) -> FlowCandidateSlotRow:
        row = self._checkpoints.get_slot(run_id, index)
        if row is None:
            raise FlowArtifactConflictError()
        return row

    def _ingest_slot(
        self, generation: ImageGenerationRow, run_id: str, index: int, facts: FlowArtifactFacts
    ) -> None:
        final = resolve_flow_final_path(
            work_root=self._work_root,
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            candidate_index=index,
            image_format=facts.format,
        )
        verified = inspect_flow_artifact(final)
        if verified != facts:
            raise FlowArtifactConflictError()
        candidate = ImageCandidate(
            image_candidate_id=str(uuid4()),
            image_generation_id=generation.id,
            candidate_index=index,
            source_path=final.relative_to(self._work_root).as_posix(),
            sha256=facts.sha256,
            width=facts.width,
            height=facts.height,
            size_bytes=facts.size_bytes,
            format=facts.format,
            review_status="pending_review",
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        self._checkpoints.ingest_candidate(
            run_id,
            index,
            expected_state="downloaded",
            expected_staged_sha256=facts.sha256,
            candidate=candidate,
            now=self._clock(),
        )

    def _recover_downloaded_facts(
        self, generation: ImageGenerationRow, slot: FlowCandidateSlotRow
    ) -> FlowArtifactFacts:
        if slot.staging_path is None or slot.staged_sha256 is None:
            raise FlowArtifactConflictError()
        try:
            staging = (self._work_root / slot.staging_path).resolve(strict=False)
            staging.relative_to(self._work_root)
        except ValueError as exc:
            raise FlowArtifactConflictError() from exc
        if staging.exists():
            staged = inspect_flow_artifact(staging)
            if staged.sha256 != slot.staged_sha256:
                raise FlowArtifactConflictError()
        matching: list[FlowArtifactFacts] = []
        for image_format in ("png", "jpeg", "webp"):
            final = resolve_flow_final_path(
                work_root=self._work_root,
                campaign_id=generation.campaign_id,
                scene_variant_id=generation.scene_variant_id,
                generation_number=generation.generation_number,
                candidate_index=slot.slot_index,
                image_format=image_format,
            )
            if not final.exists():
                continue
            facts = inspect_flow_artifact(final)
            if facts.sha256 == slot.staged_sha256:
                matching.append(facts)
        if len(matching) != 1:
            raise FlowArtifactConflictError()
        return matching[0]

    def _validate_ingested(
        self, generation: ImageGenerationRow, slot: FlowCandidateSlotRow
    ) -> None:
        if slot.image_candidate_id is None:
            raise FlowArtifactConflictError()
        with self._sessions() as session:
            candidate = session.get(ImageCandidateRow, slot.image_candidate_id)
            if candidate is None or candidate.image_generation_id != generation.id:
                raise FlowArtifactConflictError()
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
                raise FlowArtifactConflictError()

    def _complete(self, generation_id: str, run_id: str) -> None:
        self._checkpoints.complete_generation(
            run_id,
            generation_id,
            expected_stage="downloading",
            now=self._clock(),
        )

    def _completed_if_intact(
        self, generation: ImageGenerationRow, slots: list[FlowCandidateSlotRow]
    ) -> JobExecutionResult:
        try:
            if generation.provider_state != "completed" or [slot.state for slot in slots] != [
                "ingested",
                "ingested",
            ]:
                raise FlowArtifactConflictError()
            for slot in slots:
                self._validate_ingested(generation, slot)
        except (OSError, ValueError, FlowArtifactInvalidError, FlowArtifactConflictError):
            return self._terminal("image_job_integrity_failed")
        return JobExecutionResult(
            outcome=JobExecutionOutcome.SUCCESS,
            result={"imageGenerationId": generation.id, "candidateCount": 2, "resolution": "2K"},
        )

    def _set_run_failure(
        self,
        run_id: str,
        stage: str,
        code: str | None = None,
        *,
        expected_stage: str,
    ) -> None:
        self._checkpoints.try_set_run_failure(
            run_id,
            expected_stage=cast(FlowGenerationStage, expected_stage),
            target_stage=cast(FlowGenerationStage, stage),
            code=code,
            now=self._clock(),
        )

    @staticmethod
    def _terminal(code: str) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.TERMINAL_FAILURE,
            error_code=code,
            error_message="The Image Generation job relationship is invalid.",
        )

    @staticmethod
    def _blocked(
        code: str, *, result: dict[str, JsonValue] | None = None
    ) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.BLOCKED,
            error_code=code,
            error_message="The authorized Flow image generation requires review.",
            result=result or {},
        )

"""Durable, fail-closed bridge from an authorized image Job to Flow."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
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
from auraly_pipeline.images.repository import ImageRepository
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


class FlowCheckpointConflictError(RuntimeError):
    pass


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


def _rowcount(result: object) -> int:
    return int(cast(Any, result).rowcount)


@contextmanager
def _checkpoint_session(sessions: sessionmaker[Session]) -> Iterator[Session]:
    try:
        with sessions() as session:
            yield session
    except DBAPIError as error:
        if _is_database_concurrency_error(error):
            raise FlowCheckpointConflictError() from error
        raise


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
        self._run_id = run_id
        self._clock = clock
        if initial_stage is None:
            with self._sessions() as session:
                run = session.get(FlowGenerationRunRow, self._run_id)
                if run is None:
                    raise ValueError("Flow generation run is missing")
                initial_stage = run.stage
        self._run_stage = initial_stage

    @property
    def run_stage(self) -> str:
        return self._run_stage

    def set_workspace(self, workspace: FlowWorkspaceIdentity) -> None:
        self._mutate_run({"prepared"}, "prepared", workspace=workspace)

    def record_inputs_verified(self, observation: FlowGenerationObservation) -> None:
        if not (observation.reference_verified and observation.prompt_verified):
            raise ValueError("unverified Flow inputs cannot be checkpointed")
        self._mutate_run({"prepared"}, "inputs_verified")

    def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None:
        self._mutate_run({"inputs_verified"}, "dispatch_intent_recorded", workspace=workspace)

    def record_dispatch_confirmed(self, observation: FlowGenerationObservation) -> None:
        if not (observation.reference_verified and observation.prompt_verified):
            raise ValueError("unverified Flow inputs cannot confirm dispatch")
        self._mutate_run({"dispatch_intent_recorded"}, "dispatch_confirmed", confirmed=True)

    def bind_candidate_slot(self, slot_index: int, observation: FlowCandidateObservation) -> None:
        if slot_index not in {0, 1} or observation.semantic_order != slot_index:
            raise ValueError("unexpected Flow candidate slot")
        with _checkpoint_session(self._sessions) as session:
            changed = _rowcount(
                session.execute(
                    update(FlowCandidateSlotRow)
                    .where(
                        FlowCandidateSlotRow.flow_generation_run_id == self._run_id,
                        FlowCandidateSlotRow.slot_index == slot_index,
                        FlowCandidateSlotRow.state == "pending",
                    )
                    .values(
                        provider_slot_fingerprint=observation.fingerprint,
                        state="observed",
                        updated_at=self._clock(),
                    )
                )
            )
            if changed != 1:
                session.rollback()
                raise FlowCheckpointConflictError()
            session.commit()
        self._reload_slot(slot_index, "observed")

    def record_candidates_observed(self, evidence: object) -> None:
        relative_path = getattr(evidence, "relative_path", None)
        sha256 = getattr(evidence, "sha256", None)
        if not isinstance(relative_path, str) or not isinstance(sha256, str):
            raise ValueError("invalid Flow grid evidence")
        self._mutate_run(
            {"dispatch_confirmed"},
            "candidates_observed",
            grid_evidence=(relative_path, sha256),
        )

    def candidate_fingerprint(self, slot_index: int) -> str:
        with self._sessions() as session:
            slot = self._slot(session, slot_index)
            if slot.state not in {"observed", "download_intent_recorded", "downloaded"}:
                raise ValueError("candidate slot is not downloadable")
            if slot.provider_slot_fingerprint is None:
                raise ValueError("candidate slot is unbound")
            return slot.provider_slot_fingerprint

    def record_download_intent(self, slot_index: int, fingerprint: str) -> None:
        with _checkpoint_session(self._sessions) as session:
            now = self._clock()
            changed = _rowcount(
                session.execute(
                    update(FlowCandidateSlotRow)
                    .where(
                        FlowCandidateSlotRow.flow_generation_run_id == self._run_id,
                        FlowCandidateSlotRow.slot_index == slot_index,
                        FlowCandidateSlotRow.state == "observed",
                        FlowCandidateSlotRow.provider_slot_fingerprint == fingerprint,
                    )
                    .values(
                        state="download_intent_recorded",
                        download_intent_at=now,
                        updated_at=now,
                    )
                )
            )
            run_changed = _rowcount(
                session.execute(
                    update(FlowGenerationRunRow)
                    .where(
                        FlowGenerationRunRow.id == self._run_id,
                        FlowGenerationRunRow.stage.in_({"candidates_observed", "downloading"}),
                    )
                    .values(stage="downloading", updated_at=now)
                )
            )
            if changed != 1 or run_changed != 1:
                session.rollback()
                raise FlowCheckpointConflictError()
            session.commit()
        self._reload_slot(slot_index, "download_intent_recorded")
        self._reload_run("downloading")
        self._run_stage = "downloading"

    def record_downloaded(self, slot_index: int, *, relative_path: str, sha256: str) -> None:
        with _checkpoint_session(self._sessions) as session:
            changed = _rowcount(
                session.execute(
                    update(FlowCandidateSlotRow)
                    .where(
                        FlowCandidateSlotRow.flow_generation_run_id == self._run_id,
                        FlowCandidateSlotRow.slot_index == slot_index,
                        FlowCandidateSlotRow.state == "download_intent_recorded",
                    )
                    .values(
                        state="downloaded",
                        staging_path=relative_path,
                        staged_sha256=sha256,
                        updated_at=self._clock(),
                    )
                )
            )
            if changed != 1:
                session.rollback()
                raise FlowCheckpointConflictError()
            session.commit()
        self._reload_slot(slot_index, "downloaded")

    def _mutate_run(
        self,
        expected: set[str],
        target: str,
        *,
        workspace: FlowWorkspaceIdentity | None = None,
        confirmed: bool = False,
        grid_evidence: tuple[str, str] | None = None,
    ) -> None:
        with _checkpoint_session(self._sessions) as session:
            now = self._clock()
            generation_id: str | None = None
            if confirmed:
                current = session.get(FlowGenerationRunRow, self._run_id)
                if current is None or current.stage not in expected:
                    session.rollback()
                    raise FlowCheckpointConflictError()
                generation_id = current.image_generation_id
            values: dict[str, object] = {"stage": target, "updated_at": now}
            if workspace is not None:
                values["provider_workspace_path"] = workspace.workspace_path
                values["provider_workspace_fingerprint"] = workspace.fingerprint
            if target == "dispatch_intent_recorded":
                values["dispatch_intent_at"] = now
            if confirmed:
                values["dispatch_confirmed_at"] = now
            if grid_evidence is not None:
                values["grid_evidence_path"], values["grid_evidence_sha256"] = grid_evidence
            changed = _rowcount(
                session.execute(
                    update(FlowGenerationRunRow)
                    .where(
                        FlowGenerationRunRow.id == self._run_id,
                        FlowGenerationRunRow.stage.in_(expected),
                        *(
                            (
                                FlowGenerationRunRow.dispatch_intent_at.is_(None),
                                FlowGenerationRunRow.dispatch_confirmed_at.is_(None),
                            )
                            if target == "dispatch_intent_recorded"
                            else ()
                        ),
                    )
                    .values(**values)
                )
            )
            if changed != 1:
                session.rollback()
                raise FlowCheckpointConflictError()
            if confirmed:
                generation_changed = _rowcount(
                    session.execute(
                        update(ImageGenerationRow)
                        .where(
                            ImageGenerationRow.id == generation_id,
                            ImageGenerationRow.provider_state == "generating",
                            ImageGenerationRow.dispatched_at.is_(None),
                        )
                        .values(dispatched_at=now, updated_at=now)
                    )
                )
                if generation_changed != 1:
                    session.rollback()
                    raise FlowCheckpointConflictError()
            session.commit()
        self._reload_run(target)
        self._run_stage = target

    def _slot(self, session: Session, slot_index: int) -> FlowCandidateSlotRow:
        row = session.scalar(
            select(FlowCandidateSlotRow).where(
                FlowCandidateSlotRow.flow_generation_run_id == self._run_id,
                FlowCandidateSlotRow.slot_index == slot_index,
            )
        )
        if row is None:
            raise ValueError("Flow candidate slot is missing")
        return row

    def _reload_run(self, expected: str) -> None:
        with _checkpoint_session(self._sessions) as session:
            row = session.get(FlowGenerationRunRow, self._run_id)
            if row is None or row.stage != expected:
                raise FlowCheckpointConflictError()

    def _reload_slot(self, slot_index: int, expected: str) -> None:
        with _checkpoint_session(self._sessions) as session:
            if self._slot(session, slot_index).state != expected:
                raise FlowCheckpointConflictError()


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
        except FlowGenerationUiContractError:
            self._set_run_failure(
                run.id,
                "blocked",
                "flow_candidate_grid_ambiguous",
                expected_stage=sink.run_stage,
            )
            return self._blocked("flow_candidate_grid_ambiguous")
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
            if error.failed_step in {
                "upload_reference",
                "verify_reference",
                "fill_prompt",
                "verify_prompt",
            }:
                code = "flow_input_verification_failed"
            elif error.failed_step == "capture_grid_evidence":
                code = "flow_diagnostic_sanitization_failed"
            elif error.failed_step == "close_browser":
                code = "flow_browser_close_failed"
            else:
                code = "flow_ui_contract_failed"
            return self._block_runtime_failure(run.id, sink, code)

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
        with _checkpoint_session(self._sessions) as session:
            generation = session.get(ImageGenerationRow, generation_id)
            if generation is None or generation.provider_state not in {
                "queued",
                "generating",
            }:
                raise FlowArtifactConflictError()
            generation.provider_state = "generating"
            generation.updated_at = self._clock()
            session.commit()

    def _run(self, run_id: str) -> FlowGenerationRunRow:
        with self._sessions() as session:
            row = session.get(FlowGenerationRunRow, run_id)
            if row is None:
                raise FlowArtifactConflictError()
            return row

    def _slot(self, run_id: str, index: int) -> FlowCandidateSlotRow:
        with self._sessions() as session:
            row = session.scalar(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == run_id,
                    FlowCandidateSlotRow.slot_index == index,
                )
            )
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
        try:
            with self._sessions() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                ImageRepository.create_candidate_in_session(session, candidate)
                changed = _rowcount(
                    session.execute(
                        update(FlowCandidateSlotRow)
                        .where(
                            FlowCandidateSlotRow.flow_generation_run_id == run_id,
                            FlowCandidateSlotRow.slot_index == index,
                            FlowCandidateSlotRow.state == "downloaded",
                            FlowCandidateSlotRow.staged_sha256 == facts.sha256,
                            FlowCandidateSlotRow.image_candidate_id.is_(None),
                        )
                        .values(
                            image_candidate_id=candidate.image_candidate_id,
                            state="ingested",
                            updated_at=self._clock(),
                        )
                    )
                )
                if changed != 1:
                    session.rollback()
                    raise FlowCheckpointConflictError()
                session.commit()
        except IntegrityError as exc:
            raise FlowCheckpointConflictError() from exc
        except DBAPIError as exc:
            if _is_database_concurrency_error(exc):
                raise FlowCheckpointConflictError() from exc
            raise
        self._reload_ingested_slot(run_id, index, candidate.image_candidate_id)

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
        with _checkpoint_session(self._sessions) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            run = session.get(FlowGenerationRunRow, run_id)
            generation = session.get(ImageGenerationRow, generation_id)
            slots = list(
                session.scalars(
                    select(FlowCandidateSlotRow).where(
                        FlowCandidateSlotRow.flow_generation_run_id == run_id
                    )
                )
            )
            if (
                run is None
                or generation is None
                or run.stage != "downloading"
                or [slot.state for slot in sorted(slots, key=lambda item: item.slot_index)]
                != ["ingested", "ingested"]
            ):
                session.rollback()
                raise FlowCheckpointConflictError()
            now = self._clock()
            run_changed = _rowcount(
                session.execute(
                    update(FlowGenerationRunRow)
                    .where(
                        FlowGenerationRunRow.id == run_id,
                        FlowGenerationRunRow.stage == "downloading",
                    )
                    .values(stage="completed", updated_at=now)
                )
            )
            generation_changed = _rowcount(
                session.execute(
                    update(ImageGenerationRow)
                    .where(
                        ImageGenerationRow.id == generation_id,
                        ImageGenerationRow.provider_state == "generating",
                    )
                    .values(provider_state="completed", completed_at=now, updated_at=now)
                )
            )
            if run_changed != 1 or generation_changed != 1:
                session.rollback()
                raise FlowCheckpointConflictError()
            session.commit()
        self._reload_completed(run_id, generation_id)

    def _reload_ingested_slot(self, run_id: str, index: int, candidate_id: str) -> None:
        with self._sessions() as session:
            slot = session.scalar(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == run_id,
                    FlowCandidateSlotRow.slot_index == index,
                )
            )
            if slot is None or slot.state != "ingested" or slot.image_candidate_id != candidate_id:
                raise FlowCheckpointConflictError()

    def _reload_completed(self, run_id: str, generation_id: str) -> None:
        with self._sessions() as session:
            run = session.get(FlowGenerationRunRow, run_id)
            generation = session.get(ImageGenerationRow, generation_id)
            if (
                run is None
                or generation is None
                or run.stage != "completed"
                or generation.provider_state != "completed"
            ):
                raise FlowCheckpointConflictError()

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
        with self._sessions() as session:
            try:
                changed = _rowcount(
                    session.execute(
                        update(FlowGenerationRunRow)
                        .where(
                            FlowGenerationRunRow.id == run_id,
                            FlowGenerationRunRow.stage == expected_stage,
                        )
                        .values(
                            stage=stage,
                            last_failure_code=code,
                            updated_at=self._clock(),
                        )
                    )
                )
                if changed != 1:
                    session.rollback()
                    return
                session.commit()
            except DBAPIError as exc:
                session.rollback()
                if _is_database_concurrency_error(exc):
                    return
                raise

    @staticmethod
    def _terminal(code: str) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.TERMINAL_FAILURE,
            error_code=code,
            error_message="The Image Generation job relationship is invalid.",
        )

    @staticmethod
    def _blocked(code: str) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.BLOCKED,
            error_code=code,
            error_message="The authorized Flow image generation requires review.",
        )

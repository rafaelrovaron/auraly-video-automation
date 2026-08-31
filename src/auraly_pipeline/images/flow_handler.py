"""Durable, fail-closed bridge from an authorized image Job to Flow."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, SceneVariantRow
from auraly_pipeline.flow.artifacts import (
    FlowArtifactConflictError,
    FlowArtifactFacts,
    FlowArtifactInvalidError,
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
from auraly_pipeline.images.domain import ImageCandidate
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FlowCheckpointConflictError(RuntimeError):
    pass


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
    ) -> None:
        self._sessions = sessions
        self._run_id = run_id
        self._clock = clock

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
        with self._sessions() as session:
            changed = session.execute(
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
            ).rowcount
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
        with self._sessions() as session:
            slot = self._slot(session, slot_index)
            if (
                slot.state == "download_intent_recorded"
                and slot.provider_slot_fingerprint == fingerprint
            ):
                return
            if slot.state != "observed" or slot.provider_slot_fingerprint != fingerprint:
                raise FlowCheckpointConflictError()
            slot.state = "download_intent_recorded"
            slot.download_intent_at = self._clock()
            slot.updated_at = self._clock()
            run = session.get(FlowGenerationRunRow, self._run_id)
            if run is None or run.stage not in {"candidates_observed", "downloading"}:
                raise ValueError("Flow run cannot start downloads")
            if run.stage == "candidates_observed":
                run.stage = "downloading"
                run.updated_at = self._clock()
            session.commit()
        self._reload_slot(slot_index, "download_intent_recorded")

    def record_downloaded(self, slot_index: int, *, relative_path: str, sha256: str) -> None:
        with self._sessions() as session:
            changed = session.execute(
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
            ).rowcount
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
        with self._sessions() as session:
            now = self._clock()
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
            changed = session.execute(
                update(FlowGenerationRunRow)
                .where(
                    FlowGenerationRunRow.id == self._run_id,
                    FlowGenerationRunRow.stage.in_(expected),
                )
                .values(**values)
            ).rowcount
            if changed != 1:
                session.rollback()
                raise ValueError("Flow checkpoint compare-and-set failed")
            session.commit()
        self._reload_run(target)

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
        with self._sessions() as session:
            row = session.get(FlowGenerationRunRow, self._run_id)
            if row is None or row.stage != expected:
                raise ValueError("Flow run checkpoint did not persist")

    def _reload_slot(self, slot_index: int, expected: str) -> None:
        with self._sessions() as session:
            if self._slot(session, slot_index).state != expected:
                raise ValueError("Flow slot checkpoint did not persist")


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
        _workspace_factory: Callable[[], FlowWorkspaceIdentity | None] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._work_root = work_root.resolve()
        self._clock = clock or _utc_now
        self._runtime_factory = _runtime_factory
        self._workspace_factory = _workspace_factory or (lambda: None)

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        validated = self._validate(context)
        if validated is None:
            return self._terminal("image_job_integrity_failed")
        generation, run, slots, reference, workspace = validated
        if run.stage == "completed":
            return self._completed_if_intact(generation, slots)
        if workspace is None:
            return self._terminal("image_job_integrity_failed")
        sink = FlowGenerationCheckpointSink(self._sessions, run_id=run.id, clock=self._clock)
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
                        reference_path=reference,
                        reference_sha256=generation.reference_image_sha256 or "",
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
                    self._set_run_failure(run.id, "ambiguous")
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
            self._set_run_failure(run.id, "ambiguous")
            return self._blocked("flow_dispatch_ambiguous")
        except FlowGenerationUiContractError:
            self._set_run_failure(run.id, "blocked", "flow_candidate_grid_ambiguous")
            return self._blocked("flow_candidate_grid_ambiguous")
        except FlowDownloadCorrelationError:
            self._set_run_failure(run.id, "blocked", "flow_download_failed")
            return self._blocked("flow_download_failed")
        except FlowArtifactInvalidError:
            self._set_run_failure(run.id, "blocked", "flow_artifact_invalid")
            return self._blocked("flow_artifact_invalid")
        except FlowArtifactConflictError:
            self._set_run_failure(run.id, "blocked", "flow_artifact_conflict")
            return self._blocked("flow_artifact_conflict")
        except FlowCheckpointConflictError:
            self._set_run_failure(run.id, "blocked", "flow_recovery_blocked")
            return self._blocked("flow_recovery_blocked")
        except FlowGenerationRuntimeError:
            self._set_run_failure(run.id, "blocked", "flow_ui_contract_failed")
            return self._blocked("flow_ui_contract_failed")

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
            authorization = session.scalar(
                select(JobEventRow).where(
                    JobEventRow.job_id == job.id,
                    JobEventRow.event_type == "job.authorized",
                )
            )
            if (
                authorization is None
                or authorization.metadata_json
                != {
                    "executor": "playwright_python",
                    "approvedBy": run.provider_action_approved_by,
                    "workspaceFingerprint": run.provider_workspace_fingerprint,
                }
                or authorization.timestamp != run.provider_action_approved_at
            ):
                return None
            try:
                reference = (self._work_root / generation.reference_image_path).resolve(strict=True)
                reference.relative_to(self._work_root)
                if (
                    hashlib.sha256(reference.read_bytes()).hexdigest()
                    != generation.reference_image_sha256
                ):
                    return None
                workspace = (
                    None
                    if run.provider_workspace_path is None
                    else FlowWorkspaceIdentity(
                        workspace_path=run.provider_workspace_path,
                        fingerprint=run.provider_workspace_fingerprint or "",
                    )
                )
            except (OSError, ValueError):
                return None
            return generation, run, slots, reference, workspace

    def _mark_generating(self, generation_id: str) -> None:
        with self._sessions() as session:
            generation = session.get(ImageGenerationRow, generation_id)
            if generation is None or generation.provider_state not in {"queued", "generating"}:
                raise FlowArtifactConflictError()
            generation.provider_state = "generating"
            generation.dispatched_at = generation.dispatched_at or self._clock()
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
        with self._sessions() as session:
            slot = session.scalar(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == run_id,
                    FlowCandidateSlotRow.slot_index == index,
                )
            )
            if slot is None or slot.state != "downloaded" or slot.staged_sha256 != facts.sha256:
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
            ImageRepository.create_candidate_in_session(session, candidate)
            slot.image_candidate_id = candidate.image_candidate_id
            slot.state = "ingested"
            slot.updated_at = self._clock()
            session.commit()

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
        with self._sessions() as session:
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
                raise FlowArtifactConflictError()
            now = self._clock()
            run.stage = "completed"
            run.updated_at = now
            generation.provider_state = "completed"
            generation.completed_at = now
            generation.updated_at = now
            session.commit()

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

    def _set_run_failure(self, run_id: str, stage: str, code: str | None = None) -> None:
        with self._sessions() as session:
            run = session.get(FlowGenerationRunRow, run_id)
            if run is not None and run.stage not in {"completed", "blocked", "failed"}:
                run.stage = stage
                run.last_failure_code = code
                run.updated_at = self._clock()
                session.commit()

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

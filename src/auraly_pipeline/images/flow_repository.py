from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar, cast
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

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
    ensure_flow_run_transition,
    ensure_flow_slot_transition,
)
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow


Result = TypeVar("Result")


class FlowCheckpointConflictError(RuntimeError):
    pass


class FlowCheckpointBusyError(FlowCheckpointConflictError):
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


class FlowCheckpointRepository:
    """Own Flow checkpoint compare-and-set and multi-row transactions."""

    _RUN_UPDATE_FIELDS = frozenset(
        {
            "provider_workspace_path",
            "provider_workspace_fingerprint",
            "dispatch_intent_at",
            "dispatch_confirmed_at",
            "grid_evidence_path",
            "grid_evidence_sha256",
            "last_failure_code",
        }
    )
    _SLOT_UPDATE_FIELDS = frozenset(
        {
            "provider_slot_fingerprint",
            "download_intent_at",
            "staging_path",
            "staged_sha256",
        }
    )

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def create_run_in_session(
        session: Session,
        run: FlowGenerationRun,
        slots: Sequence[FlowCandidateSlot],
    ) -> FlowGenerationRunRow:
        if (
            len(slots) != 2
            or sorted(slot.slot_index for slot in slots) != [0, 1]
            or any(slot.flow_generation_run_id != run.flow_generation_run_id for slot in slots)
        ):
            raise FlowCheckpointConflictError()
        generation = session.get(ImageGenerationRow, run.image_generation_id)
        if generation is None or generation.executor != "playwright_python":
            raise FlowCheckpointConflictError()
        try:
            with session.begin_nested():
                row = FlowGenerationRunRow(
                    id=run.flow_generation_run_id,
                    image_generation_id=run.image_generation_id,
                    stage=run.stage,
                    required_candidate_count=run.required_candidate_count,
                    required_resolution=run.required_resolution,
                    provider_workspace_path=run.provider_workspace_path,
                    provider_workspace_fingerprint=run.provider_workspace_fingerprint,
                    dispatch_attempt_number=run.dispatch_attempt_number,
                    dispatch_intent_at=run.dispatch_intent_at,
                    dispatch_confirmed_at=run.dispatch_confirmed_at,
                    grid_evidence_path=run.grid_evidence_path,
                    grid_evidence_sha256=run.grid_evidence_sha256,
                    last_failure_code=run.last_failure_code,
                    provider_action_approved_by=run.provider_action_approved_by,
                    provider_action_approved_at=run.provider_action_approved_at,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
                session.add(row)
                session.add_all(
                    [
                        FlowCandidateSlotRow(
                            id=slot.flow_candidate_slot_id,
                            flow_generation_run_id=slot.flow_generation_run_id,
                            slot_index=slot.slot_index,
                            provider_slot_fingerprint=slot.provider_slot_fingerprint,
                            state=slot.state,
                            download_intent_at=slot.download_intent_at,
                            staging_path=slot.staging_path,
                            staged_sha256=slot.staged_sha256,
                            image_candidate_id=slot.image_candidate_id,
                            created_at=slot.created_at,
                            updated_at=slot.updated_at,
                        )
                        for slot in slots
                    ]
                )
                session.flush()
            return row
        except (IntegrityError, ValueError) as error:
            raise FlowCheckpointConflictError() from error

    def get_run(self, run_id: str) -> FlowGenerationRunRow | None:
        with self._session_factory() as session:
            return session.get(FlowGenerationRunRow, run_id)

    def get_run_for_generation(self, generation_id: str) -> FlowGenerationRunRow | None:
        with self._session_factory() as session:
            return session.scalar(
                select(FlowGenerationRunRow).where(
                    FlowGenerationRunRow.image_generation_id == generation_id
                )
            )

    def get_slot(self, run_id: str, slot_index: int) -> FlowCandidateSlotRow | None:
        with self._session_factory() as session:
            return self._get_slot(session, run_id, slot_index)

    def list_slots(self, run_id: str) -> list[FlowCandidateSlotRow]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(FlowCandidateSlotRow)
                    .where(FlowCandidateSlotRow.flow_generation_run_id == run_id)
                    .order_by(FlowCandidateSlotRow.slot_index)
                )
            )

    def get_candidate(self, candidate_id: str) -> ImageCandidateRow | None:
        with self._session_factory() as session:
            return session.get(ImageCandidateRow, candidate_id)

    def set_workspace(
        self,
        run_id: str,
        *,
        expected_stage: FlowGenerationStage,
        workspace_path: str,
        workspace_fingerprint: str,
        now: datetime,
    ) -> FlowGenerationRunRow:
        def set_value(session: Session) -> FlowGenerationRunRow:
            changed = _rowcount(
                session.execute(
                    update(FlowGenerationRunRow)
                    .where(
                        FlowGenerationRunRow.id == run_id,
                        FlowGenerationRunRow.stage == expected_stage,
                    )
                    .values(
                        provider_workspace_path=workspace_path,
                        provider_workspace_fingerprint=workspace_fingerprint,
                        updated_at=now,
                    )
                )
            )
            if changed != 1:
                raise FlowCheckpointConflictError()
            row = session.get(FlowGenerationRunRow, run_id)
            if row is None:
                raise FlowCheckpointConflictError()
            self._validate_run(row)
            return row

        return self._transaction(set_value)

    def mark_generation_generating(
        self,
        generation_id: str,
        *,
        now: datetime,
    ) -> ImageGenerationRow:
        def mark(session: Session) -> ImageGenerationRow:
            generation = session.get(ImageGenerationRow, generation_id)
            if generation is None or generation.provider_state not in {"queued", "generating"}:
                raise FlowCheckpointConflictError()
            generation.provider_state = "generating"
            generation.updated_at = now
            session.flush()
            return generation

        return self._transaction(mark)

    def transition_run(
        self,
        run_id: str,
        *,
        expected_stage: FlowGenerationStage,
        target_stage: FlowGenerationStage,
        now: datetime,
        updates: Mapping[str, object] | None = None,
    ) -> FlowGenerationRunRow:
        ensure_flow_run_transition(expected_stage, target_stage)
        values = self._allowed_updates(updates, self._RUN_UPDATE_FIELDS)
        values.update(stage=target_stage, updated_at=now)

        def transition(session: Session) -> FlowGenerationRunRow:
            where = [
                FlowGenerationRunRow.id == run_id,
                FlowGenerationRunRow.stage == expected_stage,
            ]
            if target_stage == "dispatch_intent_recorded":
                where.extend(
                    (
                        FlowGenerationRunRow.dispatch_intent_at.is_(None),
                        FlowGenerationRunRow.dispatch_confirmed_at.is_(None),
                    )
                )
            changed = _rowcount(
                session.execute(
                    update(FlowGenerationRunRow).where(*where).values(**values)
                )
            )
            if changed != 1:
                raise FlowCheckpointConflictError()
            row = session.get(FlowGenerationRunRow, run_id)
            if row is None:
                raise FlowCheckpointConflictError()
            self._validate_run(row)
            return row

        return self._transaction(transition)

    def confirm_dispatch(
        self,
        run_id: str,
        *,
        expected_stage: FlowGenerationStage,
        now: datetime,
    ) -> FlowGenerationRunRow:
        ensure_flow_run_transition(expected_stage, "dispatch_confirmed")

        def confirm(session: Session) -> FlowGenerationRunRow:
            run = session.get(FlowGenerationRunRow, run_id)
            if run is None or run.stage != expected_stage or run.dispatch_intent_at is None:
                raise FlowCheckpointConflictError()
            run_changed = _rowcount(
                session.execute(
                    update(FlowGenerationRunRow)
                    .where(
                        FlowGenerationRunRow.id == run_id,
                        FlowGenerationRunRow.stage == expected_stage,
                        FlowGenerationRunRow.dispatch_confirmed_at.is_(None),
                    )
                    .values(stage="dispatch_confirmed", dispatch_confirmed_at=now, updated_at=now)
                )
            )
            generation_changed = _rowcount(
                session.execute(
                    update(ImageGenerationRow)
                    .where(
                        ImageGenerationRow.id == run.image_generation_id,
                        ImageGenerationRow.provider_state == "generating",
                        ImageGenerationRow.dispatched_at.is_(None),
                    )
                    .values(dispatched_at=now, updated_at=now)
                )
            )
            if run_changed != 1 or generation_changed != 1:
                raise FlowCheckpointConflictError()
            session.expire(run)
            self._validate_run(run)
            return run

        return self._transaction(confirm)

    def transition_slot(
        self,
        run_id: str,
        slot_index: int,
        *,
        expected_state: FlowCandidateSlotState,
        target_state: FlowCandidateSlotState,
        now: datetime,
        updates: Mapping[str, object] | None = None,
        expected_fingerprint: str | None = None,
    ) -> FlowCandidateSlotRow:
        ensure_flow_slot_transition(expected_state, target_state)
        values = self._allowed_updates(updates, self._SLOT_UPDATE_FIELDS)
        values.update(state=target_state, updated_at=now)

        def transition(session: Session) -> FlowCandidateSlotRow:
            where = [
                FlowCandidateSlotRow.flow_generation_run_id == run_id,
                FlowCandidateSlotRow.slot_index == slot_index,
                FlowCandidateSlotRow.state == expected_state,
            ]
            if expected_fingerprint is not None:
                where.append(
                    FlowCandidateSlotRow.provider_slot_fingerprint == expected_fingerprint
                )
            changed = _rowcount(
                session.execute(update(FlowCandidateSlotRow).where(*where).values(**values))
            )
            if changed != 1:
                raise FlowCheckpointConflictError()
            row = self._get_slot(session, run_id, slot_index)
            if row is None:
                raise FlowCheckpointConflictError()
            self._validate_slot(row)
            return row

        return self._transaction(transition)

    def record_download_intent(
        self,
        run_id: str,
        slot_index: int,
        *,
        expected_state: FlowCandidateSlotState,
        expected_fingerprint: str,
        now: datetime,
    ) -> FlowCandidateSlotRow:
        ensure_flow_slot_transition(expected_state, "download_intent_recorded")

        def record(session: Session) -> FlowCandidateSlotRow:
            slot_changed = _rowcount(
                session.execute(
                    update(FlowCandidateSlotRow)
                    .where(
                        FlowCandidateSlotRow.flow_generation_run_id == run_id,
                        FlowCandidateSlotRow.slot_index == slot_index,
                        FlowCandidateSlotRow.state == expected_state,
                        FlowCandidateSlotRow.provider_slot_fingerprint == expected_fingerprint,
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
                        FlowGenerationRunRow.id == run_id,
                        FlowGenerationRunRow.stage.in_({"candidates_observed", "downloading"}),
                    )
                    .values(stage="downloading", updated_at=now)
                )
            )
            if slot_changed != 1 or run_changed != 1:
                raise FlowCheckpointConflictError()
            row = self._get_slot(session, run_id, slot_index)
            if row is None:
                raise FlowCheckpointConflictError()
            self._validate_slot(row)
            return row

        return self._transaction(record)

    def record_downloaded(
        self,
        run_id: str,
        slot_index: int,
        *,
        expected_state: FlowCandidateSlotState,
        expected_fingerprint: str,
        relative_path: str,
        sha256: str,
        now: datetime,
    ) -> FlowCandidateSlotRow:
        return self.transition_slot(
            run_id,
            slot_index,
            expected_state=expected_state,
            target_state="downloaded",
            expected_fingerprint=expected_fingerprint,
            now=now,
            updates={"staging_path": relative_path, "staged_sha256": sha256},
        )

    def record_recovery_downloaded(
        self,
        run_id: str,
        generation_id: str,
        job_id: str,
        slot_index: int,
        *,
        expected_fingerprint: str,
        relative_path: str,
        sha256: str,
        now: datetime,
    ) -> FlowCandidateSlotRow:
        def record(session: Session) -> FlowCandidateSlotRow:
            self._require_blocked_recovery(session, run_id, generation_id, job_id)
            return self._transition_slot_in_session(
                session,
                run_id,
                slot_index,
                expected_state="download_intent_recorded",
                target_state="downloaded",
                expected_fingerprint=expected_fingerprint,
                updates={"staging_path": relative_path, "staged_sha256": sha256},
                now=now,
            )

        return self._transaction(record)

    def ingest_candidate(
        self,
        run_id: str,
        slot_index: int,
        *,
        expected_state: FlowCandidateSlotState,
        expected_staged_sha256: str,
        candidate: ImageCandidate,
        now: datetime,
    ) -> FlowCandidateSlotRow:
        ensure_flow_slot_transition(expected_state, "ingested")

        def ingest(session: Session) -> FlowCandidateSlotRow:
            return self._ingest_candidate_in_session(
                session,
                run_id,
                slot_index,
                expected_state=expected_state,
                expected_staged_sha256=expected_staged_sha256,
                candidate=candidate,
                now=now,
            )

        return self._transaction(ingest)

    def ingest_recovery_candidate(
        self,
        run_id: str,
        generation_id: str,
        job_id: str,
        slot_index: int,
        *,
        expected_slot_identities: Sequence[tuple[object, ...]],
        expected_staged_sha256: str,
        candidate: ImageCandidate,
        now: datetime,
    ) -> FlowCandidateSlotRow:
        def ingest(session: Session) -> FlowCandidateSlotRow:
            self._require_blocked_recovery(session, run_id, generation_id, job_id)
            if [self.slot_identity(slot) for slot in self._list_slots(session, run_id)] != list(
                expected_slot_identities
            ):
                raise FlowCheckpointConflictError()
            return self._ingest_candidate_in_session(
                session,
                run_id,
                slot_index,
                expected_state="downloaded",
                expected_staged_sha256=expected_staged_sha256,
                candidate=candidate,
                now=now,
            )

        return self._transaction(ingest)

    def complete_generation(
        self,
        run_id: str,
        generation_id: str,
        *,
        expected_stage: FlowGenerationStage,
        now: datetime,
    ) -> FlowGenerationRunRow:
        ensure_flow_run_transition(expected_stage, "completed")

        def complete(session: Session) -> None:
            run = session.get(FlowGenerationRunRow, run_id)
            generation = session.get(ImageGenerationRow, generation_id)
            slots = list(
                session.scalars(
                    select(FlowCandidateSlotRow)
                    .where(FlowCandidateSlotRow.flow_generation_run_id == run_id)
                    .order_by(FlowCandidateSlotRow.slot_index)
                )
            )
            if (
                run is None
                or generation is None
                or run.image_generation_id != generation_id
                or run.stage != expected_stage
                or [slot.slot_index for slot in slots] != [0, 1]
                or [slot.state for slot in slots] != ["ingested", "ingested"]
            ):
                raise FlowCheckpointConflictError()
            run_changed = _rowcount(
                session.execute(
                    update(FlowGenerationRunRow)
                    .where(
                        FlowGenerationRunRow.id == run_id,
                        FlowGenerationRunRow.stage == expected_stage,
                    )
                    .values(stage="completed", last_failure_code=None, updated_at=now)
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
                raise FlowCheckpointConflictError()

        self._transaction(complete)
        with self._session_factory() as session:
            run = session.get(FlowGenerationRunRow, run_id)
            generation = session.get(ImageGenerationRow, generation_id)
            if (
                run is None
                or generation is None
                or run.stage != "completed"
                or generation.provider_state != "completed"
            ):
                raise FlowCheckpointConflictError()
            self._validate_run(run)
            return run

    def reset_pre_intent(
        self,
        run_id: str,
        generation_id: str,
        *,
        expected_stages: frozenset[FlowGenerationStage],
        now: datetime,
    ) -> FlowGenerationRunRow:
        def reset(session: Session) -> FlowGenerationRunRow:
            run = session.get(FlowGenerationRunRow, run_id)
            generation = session.get(ImageGenerationRow, generation_id)
            slots = self._list_slots(session, run_id)
            if (
                run is None
                or generation is None
                or run.image_generation_id != generation_id
                or run.stage not in expected_stages
                or generation.provider_state not in {"queued", "generating", "blocked"}
                or generation.dispatched_at is not None
                or generation.completed_at is not None
                or run.dispatch_intent_at is not None
                or run.dispatch_confirmed_at is not None
                or [slot.slot_index for slot in slots] != [0, 1]
                or [slot.state for slot in slots] != ["pending", "pending"]
            ):
                raise FlowCheckpointConflictError()
            run.stage = "prepared"
            run.last_failure_code = None
            run.updated_at = now
            generation.provider_state = "queued"
            generation.updated_at = now
            session.flush()
            self._validate_run(run)
            return run

        return self._transaction(reset)

    def promote_recovered_dispatch(
        self,
        run_id: str,
        generation_id: str,
        *,
        expected_slot_identities: Sequence[tuple[object, ...]],
        now: datetime,
    ) -> FlowGenerationRunRow:
        def promote(session: Session) -> FlowGenerationRunRow:
            run = session.get(FlowGenerationRunRow, run_id)
            generation = session.get(ImageGenerationRow, generation_id)
            slots = self._list_slots(session, run_id)
            if (
                run is None
                or generation is None
                or run.image_generation_id != generation_id
                or run.dispatch_intent_at is None
                or [self.slot_identity(slot) for slot in slots]
                != list(expected_slot_identities)
            ):
                raise FlowCheckpointConflictError()
            if run.dispatch_confirmed_at is None:
                run.dispatch_confirmed_at = now
            states = [slot.state for slot in slots]
            if any(state in {"download_intent_recorded", "downloaded", "ingested"} for state in states):
                run.stage = "downloading"
            elif any(state == "observed" for state in states):
                run.stage = "candidates_observed"
            else:
                run.stage = "dispatch_confirmed"
            run.last_failure_code = None
            run.updated_at = now
            generation.provider_state = "generating"
            generation.dispatched_at = generation.dispatched_at or now
            generation.updated_at = now
            session.flush()
            self._validate_run(run)
            return run

        return self._transaction(promote)

    def complete_recovered_generation(
        self,
        run_id: str,
        generation_id: str,
        *,
        now: datetime,
    ) -> FlowGenerationRunRow:
        def complete(session: Session) -> FlowGenerationRunRow:
            run = session.get(FlowGenerationRunRow, run_id)
            generation = session.get(ImageGenerationRow, generation_id)
            slots = self._list_slots(session, run_id)
            if (
                run is None
                or generation is None
                or run.image_generation_id != generation_id
                or [slot.slot_index for slot in slots] != [0, 1]
                or [slot.state for slot in slots] != ["ingested", "ingested"]
            ):
                raise FlowCheckpointConflictError()
            run.stage = "completed"
            run.last_failure_code = None
            run.updated_at = now
            generation.provider_state = "completed"
            generation.completed_at = generation.completed_at or now
            generation.dispatched_at = generation.dispatched_at or run.dispatch_confirmed_at
            generation.updated_at = now
            session.flush()
            self._validate_run(run)
            return run

        return self._transaction(complete)

    @staticmethod
    def resolve_no_dispatch_in_session(
        session: Session,
        *,
        run_id: str,
        generation_id: str,
        job_id: str,
        resolved_by: str,
        reason: str,
        now: datetime,
    ) -> str:
        run = session.get(FlowGenerationRunRow, run_id)
        generation = session.get(ImageGenerationRow, generation_id)
        job = session.get(JobRow, job_id)
        if run is None or generation is None or job is None:
            raise FlowCheckpointConflictError()
        existing = session.scalar(
            select(JobEventRow)
            .where(
                JobEventRow.job_id == job_id,
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
            run.image_generation_id != generation_id
            or generation.job_id != job_id
            or generation.provider_state != "blocked"
            or job.status != "blocked"
            or run.stage != "ambiguous"
            or run.dispatch_intent_at is None
            or run.dispatch_confirmed_at is not None
        ):
            raise FlowCheckpointConflictError()
        previous_attempt = run.dispatch_attempt_number
        previous_intent = FlowCheckpointRepository._utc(run.dispatch_intent_at)
        if previous_intent is None:
            raise FlowCheckpointConflictError()
        previous_confirmation = FlowCheckpointRepository._utc(run.dispatch_confirmed_at)
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
                    "previousDispatchConfirmedAt": (
                        None if previous_confirmation is None else previous_confirmation.isoformat()
                    ),
                    "nextDispatchAttemptNumber": run.dispatch_attempt_number,
                    "resolvedBy": resolved_by,
                    "reason": reason,
                },
            )
        )
        session.flush()
        return job.id

    def try_set_run_failure(
        self,
        run_id: str,
        *,
        expected_stage: FlowGenerationStage,
        target_stage: FlowGenerationStage,
        code: str | None,
        now: datetime,
    ) -> bool:
        try:
            self.transition_run(
                run_id,
                expected_stage=expected_stage,
                target_stage=target_stage,
                updates={"last_failure_code": code},
                now=now,
            )
        except FlowCheckpointConflictError:
            return False
        return True

    def _transaction(self, operation: Callable[[Session], Result]) -> Result:
        try:
            with self._session_factory() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                result = operation(session)
                session.commit()
                return result
        except FlowCheckpointConflictError:
            raise
        except IntegrityError as error:
            raise FlowCheckpointConflictError() from error
        except DBAPIError as error:
            if _is_database_concurrency_error(error):
                raise FlowCheckpointBusyError() from error
            raise
        except ValueError as error:
            raise FlowCheckpointConflictError() from error

    def _transition_slot_in_session(
        self,
        session: Session,
        run_id: str,
        slot_index: int,
        *,
        expected_state: FlowCandidateSlotState,
        target_state: FlowCandidateSlotState,
        expected_fingerprint: str | None,
        updates: Mapping[str, object] | None,
        now: datetime,
    ) -> FlowCandidateSlotRow:
        ensure_flow_slot_transition(expected_state, target_state)
        values = self._allowed_updates(updates, self._SLOT_UPDATE_FIELDS)
        values.update(state=target_state, updated_at=now)
        where = [
            FlowCandidateSlotRow.flow_generation_run_id == run_id,
            FlowCandidateSlotRow.slot_index == slot_index,
            FlowCandidateSlotRow.state == expected_state,
        ]
        if expected_fingerprint is not None:
            where.append(FlowCandidateSlotRow.provider_slot_fingerprint == expected_fingerprint)
        changed = _rowcount(
            session.execute(update(FlowCandidateSlotRow).where(*where).values(**values))
        )
        if changed != 1:
            raise FlowCheckpointConflictError()
        row = self._get_slot(session, run_id, slot_index)
        if row is None:
            raise FlowCheckpointConflictError()
        self._validate_slot(row)
        return row

    def _ingest_candidate_in_session(
        self,
        session: Session,
        run_id: str,
        slot_index: int,
        *,
        expected_state: FlowCandidateSlotState,
        expected_staged_sha256: str,
        candidate: ImageCandidate,
        now: datetime,
    ) -> FlowCandidateSlotRow:
        run = session.get(FlowGenerationRunRow, run_id)
        slot = self._get_slot(session, run_id, slot_index)
        if run is None or slot is None:
            raise FlowCheckpointConflictError()
        if slot.state == "ingested":
            existing = (
                None
                if slot.image_candidate_id is None
                else session.get(ImageCandidateRow, slot.image_candidate_id)
            )
            if existing is None or not self._candidate_matches(existing, candidate):
                raise FlowCheckpointConflictError()
            return slot
        if (
            slot.state != expected_state
            or slot.staged_sha256 != expected_staged_sha256
            or slot.image_candidate_id is not None
            or candidate.image_generation_id != run.image_generation_id
            or candidate.candidate_index != slot_index
            or candidate.sha256 != expected_staged_sha256
        ):
            raise FlowCheckpointConflictError()
        ImageRepository.create_candidate_in_session(session, candidate)
        slot.image_candidate_id = candidate.image_candidate_id
        slot.state = "ingested"
        slot.updated_at = now
        session.flush()
        self._validate_slot(slot)
        return slot

    @staticmethod
    def _require_blocked_recovery(
        session: Session,
        run_id: str,
        generation_id: str,
        job_id: str,
    ) -> None:
        run = session.get(FlowGenerationRunRow, run_id)
        generation = session.get(ImageGenerationRow, generation_id)
        job = session.get(JobRow, job_id)
        if (
            run is None
            or generation is None
            or job is None
            or run.image_generation_id != generation_id
            or generation.job_id != job_id
            or generation.provider_state not in {"queued", "generating", "blocked"}
            or job.status != "blocked"
            or job.worker_id is not None
            or job.lease_expires_at is not None
        ):
            raise FlowCheckpointConflictError()

    @staticmethod
    def _get_slot(
        session: Session, run_id: str, slot_index: int
    ) -> FlowCandidateSlotRow | None:
        return session.scalar(
            select(FlowCandidateSlotRow).where(
                FlowCandidateSlotRow.flow_generation_run_id == run_id,
                FlowCandidateSlotRow.slot_index == slot_index,
            )
        )

    @staticmethod
    def _list_slots(session: Session, run_id: str) -> list[FlowCandidateSlotRow]:
        return list(
            session.scalars(
                select(FlowCandidateSlotRow)
                .where(FlowCandidateSlotRow.flow_generation_run_id == run_id)
                .order_by(FlowCandidateSlotRow.slot_index)
            )
        )

    @staticmethod
    def slot_identity(slot: FlowCandidateSlotRow) -> tuple[object, ...]:
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
    def _allowed_updates(
        updates: Mapping[str, object] | None, allowed: frozenset[str]
    ) -> dict[str, object]:
        values = dict(updates or {})
        if not values.keys() <= allowed:
            raise FlowCheckpointConflictError()
        return values

    @classmethod
    def _validate_run(cls, row: FlowGenerationRunRow) -> None:
        FlowGenerationRun(
            flow_generation_run_id=row.id,
            image_generation_id=row.image_generation_id,
            stage=cast(FlowGenerationStage, row.stage),
            required_candidate_count=cast(Literal[2], row.required_candidate_count),
            required_resolution=cast(Any, row.required_resolution),
            provider_workspace_path=row.provider_workspace_path,
            provider_workspace_fingerprint=row.provider_workspace_fingerprint,
            dispatch_attempt_number=row.dispatch_attempt_number,
            dispatch_intent_at=cls._utc(row.dispatch_intent_at),
            dispatch_confirmed_at=cls._utc(row.dispatch_confirmed_at),
            grid_evidence_path=row.grid_evidence_path,
            grid_evidence_sha256=row.grid_evidence_sha256,
            last_failure_code=row.last_failure_code,
            provider_action_approved_by=row.provider_action_approved_by,
            provider_action_approved_at=cast(datetime, cls._utc(row.provider_action_approved_at)),
            created_at=cast(datetime, cls._utc(row.created_at)),
            updated_at=cast(datetime, cls._utc(row.updated_at)),
        )

    @classmethod
    def _validate_slot(cls, row: FlowCandidateSlotRow) -> None:
        FlowCandidateSlot(
            flow_candidate_slot_id=row.id,
            flow_generation_run_id=row.flow_generation_run_id,
            slot_index=row.slot_index,
            provider_slot_fingerprint=row.provider_slot_fingerprint,
            state=cast(FlowCandidateSlotState, row.state),
            download_intent_at=cls._utc(row.download_intent_at),
            staging_path=row.staging_path,
            staged_sha256=row.staged_sha256,
            image_candidate_id=row.image_candidate_id,
            created_at=cast(datetime, cls._utc(row.created_at)),
            updated_at=cast(datetime, cls._utc(row.updated_at)),
        )

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _candidate_matches(row: ImageCandidateRow, candidate: ImageCandidate) -> bool:
        return (
            row.id,
            row.image_generation_id,
            row.candidate_index,
            row.source_path,
            row.sha256,
            row.width,
            row.height,
            row.size_bytes,
            row.format,
            row.review_status,
        ) == (
            candidate.image_candidate_id,
            candidate.image_generation_id,
            candidate.candidate_index,
            candidate.source_path,
            candidate.sha256,
            candidate.width,
            candidate.height,
            candidate.size_bytes,
            candidate.format,
            candidate.review_status,
        )

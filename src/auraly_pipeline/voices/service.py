from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.config_paths import configured_work_root
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow
from auraly_pipeline.jobs.domain import Job, JobSubmit, RetrySafety
from auraly_pipeline.jobs.handlers import default_fake_handlers
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.metadata_security import validate_safe_error_message, validate_safe_identifier
from auraly_pipeline.voices.db_models import VoiceMasterRow
from auraly_pipeline.voices.domain import (
    TranscriptMatchStatus,
    TranscriptSource,
    VoiceGenerateRequest,
    VoiceMaster,
    VoiceMasterStatus,
)
from auraly_pipeline.voices.handler import SpeechProvider, TranscriptProvider, VoiceGenerateHandler
from auraly_pipeline.voices.repository import VoiceMasterRepository


class VoiceMasterError(RuntimeError):
    public_message = "The Voice Master operation failed safely."


class VoiceMasterNotFoundError(VoiceMasterError):
    public_message = "Voice Master not found."


class ApprovedCopyMasterNotFoundError(VoiceMasterError):
    public_message = "An approved CopyMaster is required."


class VoiceMasterReviewError(VoiceMasterError):
    public_message = "Voice Master QC does not permit approval."


class VoiceMasterConflictError(VoiceMasterError):
    public_message = "The Voice Master request conflicts with persisted state."


@dataclass(frozen=True)
class VoiceGenerationSubmission:
    voice_master: VoiceMaster
    job: Job


class VoiceMasterService:
    def __init__(
        self,
        engine: Engine,
        *,
        work_root: Path,
        provider: SpeechProvider | None = None,
        transcriber: TranscriptProvider | None = None,
    ) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
        self._work_root = work_root.resolve()
        handler = VoiceGenerateHandler(
            self._sessions,
            work_root=self._work_root,
            provider=provider,
            transcriber=transcriber,
        )
        handlers = default_fake_handlers()
        handlers["voice.generate"] = handler
        from auraly_pipeline.jobs.repository import JobRepository

        self._jobs = JobService(engine, JobRepository(self._sessions), handlers=handlers)

    @classmethod
    def for_database(
        cls,
        database_path: Path,
        *,
        work_root: Path | None = None,
        provider: SpeechProvider | None = None,
        transcriber: TranscriptProvider | None = None,
    ) -> VoiceMasterService:
        migrate_database(database_path)
        engine = create_sqlite_engine(database_path)
        root = configured_work_root(work_root)
        return cls(engine, work_root=root, provider=provider, transcriber=transcriber)

    def close(self) -> None:
        self._engine.dispose()

    def generate(self, request: VoiceGenerateRequest) -> VoiceGenerationSubmission:
        return self._submit(request, force_new=False)

    def regenerate(self, request: VoiceGenerateRequest) -> VoiceGenerationSubmission:
        return self._submit(request, force_new=True)

    def _paid_authorization_mutation(
        self,
        *,
        voice_master_id: str,
        campaign_budget_limit_cents: int,
        campaign_currency: str,
        now: datetime,
    ) -> Callable[[Session, JobRow], None]:
        def mutate(session: Session, job: JobRow) -> None:
            voice = session.get(VoiceMasterRow, voice_master_id)
            if voice is None:
                raise VoiceMasterConflictError
            if (
                job.job_type != "voice.generate"
                or job.campaign_id != voice.campaign_id
                or job.input_json != {"voiceMasterId": voice.id}
                or job.idempotency_key != f"voice.generate:{voice.logical_key}"
                or voice.job_id not in {None, job.id}
            ):
                raise VoiceMasterConflictError
            approved_by = voice.settings_json.get("paid_request_approved_by")
            approved_at = voice.settings_json.get("paid_request_approved_at")
            approved_budget = voice.settings_json.get("approved_budget_cents")
            if (
                voice.settings_json.get("paid_request_approved") is not True
                or not isinstance(approved_by, str)
                or not isinstance(approved_at, str)
                or not isinstance(approved_budget, int)
                or isinstance(approved_budget, bool)
                or approved_budget <= 0
                or approved_budget > campaign_budget_limit_cents
            ):
                raise VoiceMasterConflictError
            if voice.job_id is None:
                voice.job_id = job.id
                voice.updated_at = now
            existing_event = (
                session.query(JobEventRow)
                .filter_by(job_id=job.id, event_type="job.paid_authorized")
                .one_or_none()
            )
            metadata = {
                "approvedBy": approved_by,
                "approvedBudgetCents": approved_budget,
                "approvedAt": approved_at,
                "campaignBudgetLimitCents": campaign_budget_limit_cents,
                "currency": campaign_currency,
            }
            if existing_event is None:
                session.add(
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=job.id,
                        event_type="job.paid_authorized",
                        timestamp=now,
                        metadata_json=metadata,
                    )
                )
            elif existing_event.metadata_json != metadata:
                raise VoiceMasterConflictError

        return mutate

    def _ensure_paid_authorization(
        self,
        *,
        voice_master_id: str,
        job_id: str,
        campaign_budget_limit_cents: int,
        campaign_currency: str,
    ) -> Job:
        mutation = self._paid_authorization_mutation(
            voice_master_id=voice_master_id,
            campaign_budget_limit_cents=campaign_budget_limit_cents,
            campaign_currency=campaign_currency,
            now=datetime.now(UTC),
        )
        self._jobs._repository.apply(job_id, mutate=mutation)
        return self._jobs.get_job(job_id)

    def _submit(
        self, request: VoiceGenerateRequest, *, force_new: bool
    ) -> VoiceGenerationSubmission:
        now = datetime.now(UTC)
        with self._sessions() as session:
            repository = VoiceMasterRepository(session)
            campaign = repository.campaign(request.campaign_id)
            if campaign is None:
                raise VoiceMasterNotFoundError
            if (
                not request.paid_request_approved
                or request.paid_request_approved_by is None
                or request.approved_budget_cents is None
            ):
                raise VoiceMasterConflictError
            budget_limit = campaign.budget_json.get("limitCents")
            budget_currency = campaign.budget_json.get("currency")
            if (
                not isinstance(budget_limit, int)
                or isinstance(budget_limit, bool)
                or budget_limit <= 0
                or request.approved_budget_cents > budget_limit
                or not isinstance(budget_currency, str)
                or len(budget_currency) != 3
                or not budget_currency.isascii()
                or not budget_currency.isalpha()
                or budget_currency != budget_currency.upper()
            ):
                raise VoiceMasterConflictError
            copy = repository.approved_copy(request.campaign_id, request.copy_master_version)
            if copy is None:
                raise ApprovedCopyMasterNotFoundError
            provider_settings = dict(request.voice_settings)
            settings: dict[str, object] = dict(provider_settings)
            settings["transcript_match_threshold"] = request.transcript_match_threshold
            settings["approved_budget_cents"] = request.approved_budget_cents
            settings["paid_request_approved"] = True
            settings["paid_request_approved_by"] = request.paid_request_approved_by
            settings["paid_request_approved_at"] = datetime.now(UTC).isoformat()
            settings_fingerprint = self._hash(
                {
                    "modelId": request.model_id,
                    "outputFormat": request.output_format,
                    "voiceId": request.voice_id,
                    "voicePreset": campaign.voice_preset,
                    "voiceSettings": provider_settings,
                }
            )
            base = {
                "campaignId": campaign.id,
                "copyMasterId": copy.id,
                "copyMasterSha256": copy.sha256,
                "copyMasterVersion": copy.version,
                "settingsFingerprint": settings_fingerprint,
            }
            base_logical_key = self._hash(base)
            if not force_new:
                existing = repository.by_logical_key(base_logical_key)
                if existing is not None:
                    if existing.job_id is None:
                        job = self._jobs.submit_job(
                            JobSubmit(
                                job_type="voice.generate",
                                campaign_id=request.campaign_id,
                                idempotency_key=f"voice.generate:{base_logical_key}",
                                input={"voiceMasterId": existing.id},
                                max_attempts=2,
                                retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
                            ),
                            before_commit=self._paid_authorization_mutation(
                                voice_master_id=existing.id,
                                campaign_budget_limit_cents=budget_limit,
                                campaign_currency=budget_currency,
                                now=now,
                            ),
                        )
                        return VoiceGenerationSubmission(self._to_domain(existing), job)
                    job = self._ensure_paid_authorization(
                        voice_master_id=existing.id,
                        job_id=existing.job_id,
                        campaign_budget_limit_cents=budget_limit,
                        campaign_currency=budget_currency,
                    )
                    return VoiceGenerationSubmission(
                        voice_master=self._to_domain(existing),
                        job=job,
                    )
            session.rollback()
            session.execute(text("BEGIN IMMEDIATE"))
            repository = VoiceMasterRepository(session)
            if not force_new:
                existing = repository.by_logical_key(base_logical_key)
                if existing is not None:
                    existing_id = existing.id
                    existing_job_id = existing.job_id
                    session.rollback()
                    if existing_job_id is None:
                        job = self._jobs.submit_job(
                            JobSubmit(
                                job_type="voice.generate",
                                campaign_id=request.campaign_id,
                                idempotency_key=f"voice.generate:{base_logical_key}",
                                input={"voiceMasterId": existing_id},
                                max_attempts=2,
                                retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
                            ),
                            before_commit=self._paid_authorization_mutation(
                                voice_master_id=existing_id,
                                campaign_budget_limit_cents=budget_limit,
                                campaign_currency=budget_currency,
                                now=now,
                            ),
                        )
                    else:
                        job = self._ensure_paid_authorization(
                            voice_master_id=existing_id,
                            job_id=existing_job_id,
                            campaign_budget_limit_cents=budget_limit,
                            campaign_currency=budget_currency,
                        )
                    return VoiceGenerationSubmission(self.get(existing_id), job)
            if repository.approved_for_campaign(campaign.id) is not None:
                raise VoiceMasterConflictError
            if force_new:
                existing_rows = repository.list(campaign_id=campaign.id)
                if existing_rows and existing_rows[-1].status not in {"rejected", "failed"}:
                    raise VoiceMasterConflictError
            generation = repository.next_generation(copy.id)
            logical_key = (
                base_logical_key if not force_new else self._hash(base | {"generation": generation})
            )
            voice_master_id = str(uuid4())
            row = VoiceMasterRow(
                id=voice_master_id,
                campaign_id=campaign.id,
                copy_master_id=copy.id,
                copy_master_version=copy.version,
                generation=generation,
                logical_key=logical_key,
                status="pending",
                provider="elevenlabs",
                voice_preset=campaign.voice_preset,
                voice_id=request.voice_id,
                model_id=request.model_id,
                output_format=request.output_format,
                settings_json=settings,
                settings_fingerprint=settings_fingerprint,
                raw_audio_path=None,
                processed_audio_path=None,
                transcript_path=None,
                manifest_path=None,
                raw_sha256=None,
                processed_sha256=None,
                transcript_sha256=None,
                manifest_sha256=None,
                raw_size_bytes=None,
                raw_format=None,
                duration_seconds=None,
                word_count=None,
                wpm=None,
                sample_rate=None,
                channels=None,
                loudness_lufs=None,
                true_peak_dbfs=None,
                leading_silence_seconds=None,
                trailing_silence_seconds=None,
                long_internal_pauses_json=[],
                transcript_source=None,
                transcript_match_status=None,
                transcript_match_score=None,
                headline_spoken=None,
                qc_findings_json=[],
                provider_request_id=None,
                provider_state="not_dispatched",
                job_id=None,
                approved_at=None,
                approved_by=None,
                rejected_at=None,
                rejected_by=None,
                rejection_reason=None,
                failure_code=None,
                created_at=now,
                updated_at=now,
            )
            job_request = JobSubmit(
                job_type="voice.generate",
                campaign_id=request.campaign_id,
                idempotency_key=f"voice.generate:{logical_key}",
                input={"voiceMasterId": voice_master_id},
                max_attempts=2,
                retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
            )
            try:
                repository.add(row)
                job_row = self._jobs._repository.create_in_session(
                    session,
                    job_request,
                    now,
                    before_commit=self._paid_authorization_mutation(
                        voice_master_id=voice_master_id,
                        campaign_budget_limit_cents=budget_limit,
                        campaign_currency=budget_currency,
                        now=now,
                    ),
                )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if not force_new:
                    existing = repository.by_logical_key(logical_key)
                    if existing is not None and existing.job_id is not None:
                        job = self._ensure_paid_authorization(
                            voice_master_id=existing.id,
                            job_id=existing.job_id,
                            campaign_budget_limit_cents=budget_limit,
                            campaign_currency=budget_currency,
                        )
                        return VoiceGenerationSubmission(
                            voice_master=self._to_domain(existing),
                            job=job,
                        )
                raise VoiceMasterConflictError from exc
            job_id = job_row.id
        return VoiceGenerationSubmission(
            self.get(voice_master_id), self._jobs.get_job(job_id)
        )

    def worker_once(self, worker_id: str, *, lease_seconds: int = 300) -> Job | None:
        return self._jobs.worker_once(worker_id, lease_seconds=lease_seconds)

    def get(self, voice_master_id: str) -> VoiceMaster:
        with self._sessions() as session:
            row = VoiceMasterRepository(session).get(voice_master_id)
            if row is None:
                raise VoiceMasterNotFoundError
            return self._to_domain(row)

    def list(
        self, *, campaign_id: str | None = None, status: VoiceMasterStatus | None = None
    ) -> list[VoiceMaster]:
        with self._sessions() as session:
            return [
                self._to_domain(row)
                for row in VoiceMasterRepository(session).list(
                    campaign_id=campaign_id,
                    status=None if status is None else status.value,
                )
            ]

    def approve(self, voice_master_id: str, *, approved_by: str) -> VoiceMaster:
        validate_safe_identifier(approved_by, "approved_by", max_length=120)
        now = datetime.now(UTC)
        with self._sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            repository = VoiceMasterRepository(session)
            row = repository.get(voice_master_id)
            if row is None:
                raise VoiceMasterNotFoundError
            if (
                row.status != "review_required"
                or row.headline_spoken
                or row.transcript_match_status != "matched"
                or row.qc_findings_json
                or not row.processed_audio_path
                or not row.processed_sha256
                or not row.raw_audio_path
                or not row.raw_sha256
                or not row.transcript_path
                or not row.transcript_sha256
                or not row.manifest_path
                or not row.manifest_sha256
            ):
                raise VoiceMasterReviewError
            artifacts = (
                (row.raw_audio_path, row.raw_sha256),
                (row.processed_audio_path, row.processed_sha256),
                (row.transcript_path, row.transcript_sha256),
                (row.manifest_path, row.manifest_sha256),
            )
            if any(
                not (artifact := (self._work_root / path).resolve()).is_relative_to(self._work_root)
                or not artifact.is_file()
                or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest
                for path, digest in artifacts
            ):
                raise VoiceMasterReviewError
            if (
                repository.approved_for_campaign(row.campaign_id) is not None
                or repository.active_other_for_campaign(row.campaign_id, row.id) is not None
            ):
                raise VoiceMasterConflictError
            row.status = "approved"
            row.approved_by = approved_by
            row.approved_at = now
            row.updated_at = now
            try:
                repository.commit()
            except IntegrityError as exc:
                repository.rollback()
                raise VoiceMasterConflictError from exc
            return self._to_domain(row)

    def reject(
        self,
        voice_master_id: str,
        *,
        rejected_by: str,
        reason: str,
    ) -> VoiceMaster:
        validate_safe_identifier(rejected_by, "rejected_by", max_length=120)
        validate_safe_error_message(reason, "rejection_reason")
        now = datetime.now(UTC)
        with self._sessions() as session:
            repository = VoiceMasterRepository(session)
            row = repository.get(voice_master_id)
            if row is None:
                raise VoiceMasterNotFoundError
            if row.status != "review_required":
                raise VoiceMasterReviewError
            row.status = "rejected"
            row.rejected_by = rejected_by
            row.rejected_at = now
            row.rejection_reason = reason
            row.updated_at = now
            repository.commit()
            return self._to_domain(row)

    def resolve_ambiguous_without_artifact(
        self,
        voice_master_id: str,
        *,
        resolved_by: str,
        reason: str,
    ) -> VoiceMaster:
        """Record an operator-confirmed no-artifact outcome before regeneration."""
        validate_safe_identifier(resolved_by, "resolved_by", max_length=120)
        validate_safe_error_message(reason, "reconciliation_reason")
        job_id: str
        with self._sessions() as session:
            row = VoiceMasterRepository(session).get(voice_master_id)
            if row is None or row.raw_audio_path is not None or row.job_id is None:
                raise VoiceMasterReviewError
            job_id = row.job_id
            job_row = session.get(JobRow, job_id)
            already_reconciled = (
                row.status == "pending"
                and row.provider_state == "not_dispatched"
                and job_row is not None
                and job_row.status == "blocked"
            )
            if not already_reconciled and (
                row.status != "generating"
                or row.provider_state not in {"ambiguous", "dispatching", "not_dispatched"}
            ):
                raise VoiceMasterReviewError
            if not already_reconciled:
                previous_state = row.provider_state
                row.status = "pending"
                row.provider_state = "not_dispatched"
                row.failure_code = None
                row.qc_findings_json = []
                row.updated_at = datetime.now(UTC)
                session.add(
                    JobEventRow(
                        id=str(uuid4()),
                        job_id=job_id,
                        event_type="job.provider_reconciled",
                        timestamp=row.updated_at,
                        metadata_json={
                            "previousProviderState": previous_state,
                            "resolvedBy": resolved_by,
                            "reason": reason,
                        },
                    )
                )
                session.commit()
        self._jobs.resume_reconciled_job(job_id)
        return self.get(voice_master_id)

    def approved_for_campaign(self, campaign_id: str) -> VoiceMaster:
        with self._sessions() as session:
            row = VoiceMasterRepository(session).approved_for_campaign(campaign_id)
            if row is None:
                raise VoiceMasterNotFoundError
            return self._to_domain(row)

    @staticmethod
    def _hash(value: dict[str, object]) -> str:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @classmethod
    def _required_utc(cls, value: datetime) -> datetime:
        converted = cls._utc(value)
        assert converted is not None
        return converted

    @classmethod
    def _to_domain(cls, row: VoiceMasterRow) -> VoiceMaster:
        return VoiceMaster(
            voice_master_id=row.id,
            campaign_id=row.campaign_id,
            copy_master_id=row.copy_master_id,
            copy_master_version=row.copy_master_version,
            generation=row.generation,
            status=VoiceMasterStatus(row.status),
            provider=cast("Literal['elevenlabs']", row.provider),
            voice_preset=row.voice_preset,
            voice_id=row.voice_id,
            model_id=row.model_id,
            settings_fingerprint=row.settings_fingerprint,
            raw_audio_path=row.raw_audio_path,
            processed_audio_path=row.processed_audio_path,
            transcript_path=row.transcript_path,
            manifest_path=row.manifest_path,
            raw_sha256=row.raw_sha256,
            processed_sha256=row.processed_sha256,
            transcript_sha256=row.transcript_sha256,
            manifest_sha256=row.manifest_sha256,
            raw_size_bytes=row.raw_size_bytes,
            raw_format=row.raw_format,
            duration_seconds=row.duration_seconds,
            word_count=row.word_count,
            wpm=row.wpm,
            sample_rate=row.sample_rate,
            channels=row.channels,
            loudness_lufs=row.loudness_lufs,
            true_peak_dbfs=row.true_peak_dbfs,
            leading_silence_seconds=row.leading_silence_seconds,
            trailing_silence_seconds=row.trailing_silence_seconds,
            long_internal_pauses=[
                (float(item[0]), float(item[1])) for item in row.long_internal_pauses_json
            ],
            transcript_source=(
                None if row.transcript_source is None else TranscriptSource(row.transcript_source)
            ),
            transcript_match_status=(
                None
                if row.transcript_match_status is None
                else TranscriptMatchStatus(row.transcript_match_status)
            ),
            transcript_match_score=row.transcript_match_score,
            headline_spoken=row.headline_spoken,
            qc_findings=row.qc_findings_json,
            provider_request_id=row.provider_request_id,
            approved_at=cls._utc(row.approved_at),
            approved_by=row.approved_by,
            rejected_at=cls._utc(row.rejected_at),
            rejected_by=row.rejected_by,
            rejection_reason=row.rejection_reason,
            created_at=cls._required_utc(row.created_at),
            updated_at=cls._required_utc(row.updated_at),
        )

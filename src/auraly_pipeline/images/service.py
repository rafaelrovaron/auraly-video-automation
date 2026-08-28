from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.images.db_models import ImageCandidateRow, ImageGenerationRow
from auraly_pipeline.images.domain import (
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
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.domain import Job, JobSubmit, RetrySafety
from auraly_pipeline.jobs.handlers import JobHandler
from auraly_pipeline.jobs.service import JobIdempotencyConflictError, JobService
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
    public_message = (
        "The idempotency key is already used by a different image generation request."
    )


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


class ImageService:
    def __init__(
        self,
        engine: Engine,
        jobs: JobService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
        self._repository = ImageRepository(self._sessions)
        self._jobs = jobs
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_database(
        cls,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        handlers: Mapping[str, JobHandler] | None = None,
        work_root: Path | None = None,
    ) -> ImageService:
        migrate_database(database_path)
        engine = create_sqlite_engine(database_path)
        jobs = JobService.for_database(
            database_path,
            clock=clock,
            handlers=handlers,
            work_root=work_root,
        )
        return cls(engine, jobs, clock=clock)

    def close(self) -> None:
        self._jobs.close()
        self._engine.dispose()

    def generate(self, request: ImageGenerateRequest) -> ImageGenerationSubmission:
        if request.executor != "local_fake":
            raise ImageError
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
            retry_safety=RetrySafety.IDEMPOTENT,
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

    def approve_candidate(self, candidate_id: str, approved_by: str) -> ImageCandidate:
        validate_safe_identifier(approved_by, "approved_by", max_length=120)
        now = self._utc(self._clock())

        def approve(session: Session) -> ImageCandidate:
            ownership = self._repository.candidate_with_generation_in_session(
                session, candidate_id
            )
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
            ownership = self._repository.candidate_with_generation_in_session(
                session, candidate_id
            )
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

    def _review_transaction(
        self, operation: Callable[[Session], ImageCandidate]
    ) -> ImageCandidate:
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

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _optional_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else cls._utc(value)

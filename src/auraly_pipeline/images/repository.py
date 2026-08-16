from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.images.db_models import ImageCandidateRow, ImageGenerationRow
from auraly_pipeline.images.domain import ImageCandidate, ImageGeneration


Result = TypeVar("Result")


class ImageRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def allocate_generation_number(session: Session, scene_variant_id: str) -> int:
        current = session.scalar(
            select(func.max(ImageGenerationRow.generation_number)).where(
                ImageGenerationRow.scene_variant_id == scene_variant_id
            )
        )
        return int(current or 0) + 1

    @staticmethod
    def create_generation_in_session(
        session: Session, generation: ImageGeneration
    ) -> ImageGenerationRow:
        row = ImageGenerationRow(
            id=generation.image_generation_id,
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            job_id=generation.job_id,
            generation_number=generation.generation_number,
            idempotency_key=generation.idempotency_key,
            request_fingerprint=generation.request_fingerprint,
            prompt_snapshot=generation.prompt_snapshot,
            prompt_sha256=generation.prompt_sha256,
            reference_image_path=generation.reference_image_path,
            reference_image_sha256=generation.reference_image_sha256,
            provider=generation.provider,
            executor=generation.executor,
            provider_state=generation.provider_state,
            created_at=generation.created_at,
            updated_at=generation.updated_at,
            dispatched_at=generation.dispatched_at,
            completed_at=generation.completed_at,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def create_candidate_in_session(
        session: Session, candidate: ImageCandidate
    ) -> ImageCandidateRow:
        row = ImageCandidateRow(
            id=candidate.image_candidate_id,
            image_generation_id=candidate.image_generation_id,
            candidate_index=candidate.candidate_index,
            source_path=candidate.source_path,
            sha256=candidate.sha256,
            width=candidate.width,
            height=candidate.height,
            size_bytes=candidate.size_bytes,
            format=candidate.format,
            review_status=candidate.review_status,
            approved_at=candidate.approved_at,
            approved_by=candidate.approved_by,
            rejected_at=candidate.rejected_at,
            rejected_by=candidate.rejected_by,
            rejection_reason=candidate.rejection_reason,
            superseded_at=candidate.superseded_at,
            superseded_by_candidate_id=candidate.superseded_by_candidate_id,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def candidates_by_index_in_session(
        session: Session, image_generation_id: str
    ) -> dict[int, ImageCandidateRow]:
        statement = select(ImageCandidateRow).where(
            ImageCandidateRow.image_generation_id == image_generation_id
        )
        return {row.candidate_index: row for row in session.scalars(statement)}

    def immediate_transaction(self, operation: Callable[[Session], Result]) -> Result:
        with self._session_factory() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                result = operation(session)
                session.commit()
                return result
            except BaseException:
                session.rollback()
                raise

    @staticmethod
    def candidate_with_generation_in_session(
        session: Session, image_candidate_id: str
    ) -> tuple[ImageCandidateRow, ImageGenerationRow] | None:
        statement = (
            select(ImageCandidateRow, ImageGenerationRow)
            .join(
                ImageGenerationRow,
                ImageCandidateRow.image_generation_id == ImageGenerationRow.id,
            )
            .where(ImageCandidateRow.id == image_candidate_id)
        )
        return session.execute(statement).tuples().one_or_none()

    @staticmethod
    def approved_candidate_for_scene_in_session(
        session: Session, scene_variant_id: str
    ) -> tuple[ImageCandidateRow, ImageGenerationRow] | None:
        statement = (
            select(ImageCandidateRow, ImageGenerationRow)
            .join(
                ImageGenerationRow,
                ImageCandidateRow.image_generation_id == ImageGenerationRow.id,
            )
            .where(
                ImageGenerationRow.scene_variant_id == scene_variant_id,
                ImageCandidateRow.review_status == "approved",
            )
        )
        return session.execute(statement).tuples().one_or_none()

    def get_generation(self, image_generation_id: str) -> ImageGenerationRow | None:
        with self._session_factory() as session:
            return session.get(ImageGenerationRow, image_generation_id)

    def list_generations(self, scene_variant_id: str) -> list[ImageGenerationRow]:
        statement: Select[tuple[ImageGenerationRow]] = (
            select(ImageGenerationRow)
            .where(ImageGenerationRow.scene_variant_id == scene_variant_id)
            .order_by(ImageGenerationRow.generation_number, ImageGenerationRow.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(statement).all())

    def get_candidate(self, image_candidate_id: str) -> ImageCandidateRow | None:
        with self._session_factory() as session:
            return session.get(ImageCandidateRow, image_candidate_id)

    def list_candidates(self, image_generation_id: str) -> list[ImageCandidateRow]:
        statement: Select[tuple[ImageCandidateRow]] = (
            select(ImageCandidateRow)
            .where(ImageCandidateRow.image_generation_id == image_generation_id)
            .order_by(ImageCandidateRow.candidate_index, ImageCandidateRow.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(statement).all())

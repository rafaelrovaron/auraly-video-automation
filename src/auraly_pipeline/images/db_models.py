from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from auraly_pipeline.campaigns.db_models import Base


class ImageGenerationRow(Base):
    __tablename__ = "image_generations"
    __table_args__ = (
        CheckConstraint("generation_number > 0", name="image_generation_number"),
        CheckConstraint("provider = 'google_flow'", name="image_generation_provider"),
        CheckConstraint(
            "executor IN ('local_fake','playwright_python')", name="image_generation_executor"
        ),
        CheckConstraint(
            "provider_state IN ('created','queued','generating','completed','failed','blocked')",
            name="image_generation_provider_state",
        ),
        CheckConstraint(
            "(reference_image_path IS NULL) = (reference_image_sha256 IS NULL)",
            name="image_generation_reference_pair",
        ),
        UniqueConstraint(
            "scene_variant_id",
            "generation_number",
            name="uq_image_generation_scene_number",
        ),
        UniqueConstraint("job_id", name="uq_image_generations_job_id"),
        UniqueConstraint("idempotency_key", name="uq_image_generations_idempotency_key"),
        Index("ix_image_generations_scene_number", "scene_variant_id", "generation_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True
    )
    scene_variant_id: Mapped[str] = mapped_column(
        ForeignKey("scene_variants.id", ondelete="RESTRICT"), index=True
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    generation_number: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    prompt_snapshot: Mapped[str] = mapped_column(Text)
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    reference_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference_image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    executor: Mapped[str] = mapped_column(String(32))
    provider_state: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImageCandidateRow(Base):
    __tablename__ = "image_candidates"
    __table_args__ = (
        CheckConstraint("candidate_index >= 0", name="image_candidate_index"),
        CheckConstraint(
            "width > 0 AND height > 0 AND size_bytes > 0", name="image_candidate_artifact_facts"
        ),
        CheckConstraint(
            "review_status IN ('pending_review','approved','rejected','superseded')",
            name="image_candidate_review_status",
        ),
        CheckConstraint(
            "(approved_at IS NULL) = (approved_by IS NULL) "
            "AND (review_status <> 'approved' OR approved_at IS NOT NULL)",
            name="image_candidate_approval_audit",
        ),
        CheckConstraint(
            "((rejected_at IS NULL AND rejected_by IS NULL AND rejection_reason IS NULL) OR "
            "(rejected_at IS NOT NULL AND rejected_by IS NOT NULL AND rejection_reason IS NOT NULL)) "
            "AND (review_status <> 'rejected' OR rejected_at IS NOT NULL)",
            name="image_candidate_rejection_audit",
        ),
        CheckConstraint(
            "(superseded_at IS NULL) = (superseded_by_candidate_id IS NULL) "
            "AND (review_status <> 'superseded' OR superseded_at IS NOT NULL)",
            name="image_candidate_supersession_audit",
        ),
        UniqueConstraint(
            "image_generation_id",
            "candidate_index",
            name="uq_image_candidate_generation_index",
        ),
        Index(
            "ix_image_candidates_generation_index", "image_generation_id", "candidate_index"
        ),
        Index("ix_image_candidates_review_status", "review_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    image_generation_id: Mapped[str] = mapped_column(
        ForeignKey("image_generations.id", ondelete="RESTRICT"), index=True
    )
    candidate_index: Mapped[int] = mapped_column(Integer)
    source_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(20))
    review_status: Mapped[str] = mapped_column(String(32))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("image_candidates.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

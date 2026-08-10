from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from auraly_pipeline.campaigns.db_models import Base


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','blocked','retry_scheduled','cancelled')",
            name="job_status",
        ),
        CheckConstraint("priority BETWEEN -100 AND 100", name="job_priority"),
        CheckConstraint(
            "retry_safety IN ('idempotent','manual_only','reconcile_before_retry')",
            name="job_retry_safety",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20 "
            "AND attempt_count <= max_attempts",
            name="job_attempt_bounds",
        ),
        CheckConstraint(
            "scene_variant_id IS NULL OR campaign_id IS NOT NULL",
            name="job_reference_scope",
        ),
        CheckConstraint(
            "((status = 'running' AND worker_id IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'running' AND worker_id IS NULL AND lease_expires_at IS NULL)) "
            "AND ((status = 'completed') = (completed_at IS NOT NULL)) "
            "AND ((status = 'cancelled') = (cancelled_at IS NOT NULL)) "
            "AND ((status = 'retry_scheduled') = (next_retry_at IS NOT NULL))",
            name="job_status_fields",
        ),
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        Index("ix_jobs_queue_order", "status", "priority", "queued_at", "id"),
        Index("ix_jobs_retry_due", "status", "next_retry_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(100))
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    scene_variant_id: Mapped[str | None] = mapped_column(
        ForeignKey("scene_variants.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    priority: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    retry_safety: Mapped[str] = mapped_column(String(32))
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempts: Mapped[list[JobAttemptRow]] = relationship(
        back_populates="job",
        order_by="JobAttemptRow.attempt_number",
        lazy="selectin",
    )
    events: Mapped[list[JobEventRow]] = relationship(
        back_populates="job",
        order_by="JobEventRow.sequence",
        lazy="selectin",
    )


class JobAttemptRow(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number >= 1", name="job_attempt_number"),
        CheckConstraint(
            "((status = 'running' AND finished_at IS NULL) OR "
            "(status IN ('completed','retryable_failure','terminal_failure','blocked','interrupted') "
            "AND finished_at IS NOT NULL))",
            name="job_attempt_status",
        ),
        UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt_job_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[JobRow] = relationship(back_populates="attempts")


class JobEventRow(Base):
    __tablename__ = "job_events"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)

    job: Mapped[JobRow] = relationship(back_populates="events")

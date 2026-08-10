"""Persistent local job orchestration schema.

Revision ID: 0002_persistent_job_orchestration
Revises: 0001_campaign_foundation
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_persistent_job_orchestration"
down_revision: str | None = "0001_campaign_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column(
            "campaign_id",
            sa.String(length=80),
            sa.ForeignKey("campaigns.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "scene_variant_id",
            sa.String(length=36),
            sa.ForeignKey("scene_variants.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("retry_safety", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','blocked','retry_scheduled','cancelled')",
            name="job_status",
        ),
        sa.CheckConstraint("priority BETWEEN -100 AND 100", name="job_priority"),
        sa.CheckConstraint(
            "retry_safety IN ('idempotent','manual_only','reconcile_before_retry')",
            name="job_retry_safety",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20 "
            "AND attempt_count <= max_attempts",
            name="job_attempt_bounds",
        ),
        sa.CheckConstraint(
            "scene_variant_id IS NULL OR campaign_id IS NOT NULL",
            name="job_reference_scope",
        ),
        sa.CheckConstraint(
            "((status = 'running' AND worker_id IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'running' AND worker_id IS NULL AND lease_expires_at IS NULL)) "
            "AND ((status = 'completed') = (completed_at IS NOT NULL)) "
            "AND ((status = 'cancelled') = (cancelled_at IS NOT NULL)) "
            "AND ((status = 'retry_scheduled') = (next_retry_at IS NOT NULL))",
            name="job_status_fields",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
    )
    op.create_index("ix_jobs_campaign_id", "jobs", ["campaign_id"])
    op.create_index("ix_jobs_scene_variant_id", "jobs", ["scene_variant_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index(
        "ix_jobs_queue_order",
        "jobs",
        ["status", "priority", "queued_at", "id"],
    )
    op.create_index("ix_jobs_retry_due", "jobs", ["status", "next_retry_at"])

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_number >= 1", name="job_attempt_number"),
        sa.CheckConstraint(
            "((status = 'running' AND finished_at IS NULL) OR "
            "(status IN ('completed','retryable_failure','terminal_failure','blocked','interrupted') "
            "AND finished_at IS NOT NULL))",
            name="job_attempt_status",
        ),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt_job_number"),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])

    op.create_table(
        "job_events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("id", name="uq_job_events_id"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])
    op.create_index("ix_job_events_event_type", "job_events", ["event_type"])

    op.execute(
        """
        CREATE TRIGGER enforce_job_scene_campaign_insert
        BEFORE INSERT ON jobs
        WHEN NEW.scene_variant_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM scene_variants
              WHERE id = NEW.scene_variant_id AND campaign_id = NEW.campaign_id
          )
        BEGIN
            SELECT RAISE(ABORT, 'SceneVariant must belong to Campaign');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_job_scene_campaign_update
        BEFORE UPDATE OF campaign_id, scene_variant_id ON jobs
        WHEN NEW.scene_variant_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM scene_variants
              WHERE id = NEW.scene_variant_id AND campaign_id = NEW.campaign_id
          )
        BEGIN
            SELECT RAISE(ABORT, 'SceneVariant must belong to Campaign');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_referenced_scene_campaign_update
        BEFORE UPDATE OF campaign_id ON scene_variants
        WHEN EXISTS (
            SELECT 1
            FROM jobs
            WHERE scene_variant_id = OLD.id
              AND campaign_id <> NEW.campaign_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'referenced SceneVariant campaign is immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER prevent_finished_job_attempt_update
        BEFORE UPDATE ON job_attempts
        WHEN OLD.finished_at IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'finished job attempt is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_job_attempt_delete
        BEFORE DELETE ON job_attempts
        BEGIN
            SELECT RAISE(ABORT, 'job attempt history is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_job_attempt_replace
        BEFORE INSERT ON job_attempts
        WHEN EXISTS (
            SELECT 1 FROM job_attempts
            WHERE id = NEW.id
               OR (job_id = NEW.job_id AND attempt_number = NEW.attempt_number)
        )
        BEGIN
            SELECT RAISE(ABORT, 'job attempt history is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_job_event_update
        BEFORE UPDATE ON job_events
        BEGIN
            SELECT RAISE(ABORT, 'job event history is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_job_event_delete
        BEFORE DELETE ON job_events
        BEGIN
            SELECT RAISE(ABORT, 'job event history is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_job_event_replace
        BEFORE INSERT ON job_events
        WHEN EXISTS (
            SELECT 1 FROM job_events
            WHERE id = NEW.id
               OR sequence = NEW.sequence
        )
        BEGIN
            SELECT RAISE(ABORT, 'job event history is immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS prevent_job_event_replace")
    op.execute("DROP TRIGGER IF EXISTS prevent_job_attempt_replace")
    op.execute("DROP TRIGGER IF EXISTS prevent_referenced_scene_campaign_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_job_scene_campaign_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_job_scene_campaign_insert")
    op.execute("DROP TRIGGER IF EXISTS prevent_job_event_delete")
    op.execute("DROP TRIGGER IF EXISTS prevent_job_event_update")
    op.execute("DROP TRIGGER IF EXISTS prevent_job_attempt_delete")
    op.execute("DROP TRIGGER IF EXISTS prevent_finished_job_attempt_update")
    op.drop_index("ix_job_events_event_type", table_name="job_events")
    op.drop_index("ix_job_events_job_id", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_job_attempts_job_id", table_name="job_attempts")
    op.drop_table("job_attempts")
    op.drop_index("ix_jobs_retry_due", table_name="jobs")
    op.drop_index("ix_jobs_queue_order", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_scene_variant_id", table_name="jobs")
    op.drop_index("ix_jobs_campaign_id", table_name="jobs")
    op.drop_table("jobs")

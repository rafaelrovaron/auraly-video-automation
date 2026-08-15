"""Durable image generations, candidates, and review invariants.

Revision ID: 0004_image_domain
Revises: 0003_voice_master
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_image_domain"
down_revision: str | None = "0003_voice_master"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "image_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(80),
            sa.ForeignKey("campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "scene_variant_id",
            sa.String(36),
            sa.ForeignKey("scene_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("prompt_snapshot", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("reference_image_path", sa.String(500), nullable=True),
        sa.Column("reference_image_sha256", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("executor", sa.String(32), nullable=False),
        sa.Column("provider_state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("generation_number > 0", name="image_generation_number"),
        sa.CheckConstraint("provider = 'google_flow'", name="image_generation_provider"),
        sa.CheckConstraint(
            "executor IN ('local_fake','playwright_python')", name="image_generation_executor"
        ),
        sa.CheckConstraint(
            "provider_state IN ('created','queued','generating','completed','failed','blocked')",
            name="image_generation_provider_state",
        ),
        sa.CheckConstraint(
            "(reference_image_path IS NULL) = (reference_image_sha256 IS NULL)",
            name="image_generation_reference_pair",
        ),
        sa.UniqueConstraint(
            "scene_variant_id", "generation_number", name="uq_image_generation_scene_number"
        ),
        sa.UniqueConstraint("job_id", name="uq_image_generations_job_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_image_generations_idempotency_key"),
    )
    op.create_index("ix_image_generations_campaign_id", "image_generations", ["campaign_id"])
    op.create_index(
        "ix_image_generations_scene_variant_id", "image_generations", ["scene_variant_id"]
    )
    op.create_index(
        "ix_image_generations_provider_state", "image_generations", ["provider_state"]
    )
    op.create_index(
        "ix_image_generations_scene_number",
        "image_generations",
        ["scene_variant_id", "generation_number"],
    )

    op.create_table(
        "image_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "image_generation_id",
            sa.String(36),
            sa.ForeignKey("image_generations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(120), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "superseded_by_candidate_id",
            sa.String(36),
            sa.ForeignKey("image_candidates.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("candidate_index >= 0", name="image_candidate_index"),
        sa.CheckConstraint(
            "width > 0 AND height > 0 AND size_bytes > 0",
            name="image_candidate_artifact_facts",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending_review','approved','rejected','superseded')",
            name="image_candidate_review_status",
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL) = (approved_by IS NULL) "
            "AND (review_status <> 'approved' OR approved_at IS NOT NULL)",
            name="image_candidate_approval_audit",
        ),
        sa.CheckConstraint(
            "((rejected_at IS NULL AND rejected_by IS NULL AND rejection_reason IS NULL) OR "
            "(rejected_at IS NOT NULL AND rejected_by IS NOT NULL AND rejection_reason IS NOT NULL)) "
            "AND (review_status <> 'rejected' OR rejected_at IS NOT NULL)",
            name="image_candidate_rejection_audit",
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL) = (superseded_by_candidate_id IS NULL) "
            "AND (review_status <> 'superseded' OR superseded_at IS NOT NULL)",
            name="image_candidate_supersession_audit",
        ),
        sa.UniqueConstraint(
            "image_generation_id",
            "candidate_index",
            name="uq_image_candidate_generation_index",
        ),
    )
    op.create_index(
        "ix_image_candidates_image_generation_id",
        "image_candidates",
        ["image_generation_id"],
    )
    op.create_index(
        "ix_image_candidates_review_status", "image_candidates", ["review_status"]
    )
    op.create_index(
        "ix_image_candidates_generation_index",
        "image_candidates",
        ["image_generation_id", "candidate_index"],
    )

    op.execute(
        """
        CREATE TRIGGER enforce_image_generation_scene_campaign_insert
        BEFORE INSERT ON image_generations
        WHEN NOT EXISTS (
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
        CREATE TRIGGER enforce_image_generation_scene_campaign_update
        BEFORE UPDATE OF campaign_id, scene_variant_id ON image_generations
        WHEN NOT EXISTS (
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
        CREATE TRIGGER prevent_image_generation_intent_update
        BEFORE UPDATE OF id, campaign_id, scene_variant_id, job_id, generation_number,
          idempotency_key, request_fingerprint, prompt_snapshot, prompt_sha256,
          reference_image_path, reference_image_sha256, provider, executor
        ON image_generations
        WHEN NEW.id IS NOT OLD.id
          OR NEW.campaign_id IS NOT OLD.campaign_id
          OR NEW.scene_variant_id IS NOT OLD.scene_variant_id
          OR NEW.job_id IS NOT OLD.job_id
          OR NEW.generation_number IS NOT OLD.generation_number
          OR NEW.idempotency_key IS NOT OLD.idempotency_key
          OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
          OR NEW.prompt_snapshot IS NOT OLD.prompt_snapshot
          OR NEW.prompt_sha256 IS NOT OLD.prompt_sha256
          OR NEW.reference_image_path IS NOT OLD.reference_image_path
          OR NEW.reference_image_sha256 IS NOT OLD.reference_image_sha256
          OR NEW.provider IS NOT OLD.provider
          OR NEW.executor IS NOT OLD.executor
        BEGIN
            SELECT RAISE(ABORT, 'generation intent is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_image_candidate_artifact_update
        BEFORE UPDATE OF id, image_generation_id, candidate_index, source_path, sha256,
          width, height, size_bytes, format
        ON image_candidates
        WHEN NEW.id IS NOT OLD.id
          OR NEW.image_generation_id IS NOT OLD.image_generation_id
          OR NEW.candidate_index IS NOT OLD.candidate_index
          OR NEW.source_path IS NOT OLD.source_path
          OR NEW.sha256 IS NOT OLD.sha256
          OR NEW.width IS NOT OLD.width
          OR NEW.height IS NOT OLD.height
          OR NEW.size_bytes IS NOT OLD.size_bytes
          OR NEW.format IS NOT OLD.format
        BEGIN
            SELECT RAISE(ABORT, 'candidate artifact identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_single_approved_image_candidate_insert
        BEFORE INSERT ON image_candidates
        WHEN NEW.review_status = 'approved' AND EXISTS (
            SELECT 1
            FROM image_candidates AS existing
            JOIN image_generations AS existing_generation
              ON existing_generation.id = existing.image_generation_id
            JOIN image_generations AS new_generation
              ON new_generation.id = NEW.image_generation_id
            WHERE existing.review_status = 'approved'
              AND existing_generation.scene_variant_id = new_generation.scene_variant_id
              AND existing.id <> NEW.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'approved candidate already exists for SceneVariant');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_single_approved_image_candidate_update
        BEFORE UPDATE OF review_status ON image_candidates
        WHEN NEW.review_status = 'approved' AND EXISTS (
            SELECT 1
            FROM image_candidates AS existing
            JOIN image_generations AS existing_generation
              ON existing_generation.id = existing.image_generation_id
            JOIN image_generations AS new_generation
              ON new_generation.id = NEW.image_generation_id
            WHERE existing.review_status = 'approved'
              AND existing_generation.scene_variant_id = new_generation.scene_variant_id
              AND existing.id <> NEW.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'approved candidate already exists for SceneVariant');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS enforce_single_approved_image_candidate_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_single_approved_image_candidate_insert")
    op.execute("DROP TRIGGER IF EXISTS prevent_image_candidate_artifact_update")
    op.execute("DROP TRIGGER IF EXISTS prevent_image_generation_intent_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_image_generation_scene_campaign_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_image_generation_scene_campaign_insert")
    op.drop_index("ix_image_candidates_generation_index", table_name="image_candidates")
    op.drop_index("ix_image_candidates_review_status", table_name="image_candidates")
    op.drop_index("ix_image_candidates_image_generation_id", table_name="image_candidates")
    op.drop_table("image_candidates")
    op.drop_index("ix_image_generations_scene_number", table_name="image_generations")
    op.drop_index("ix_image_generations_provider_state", table_name="image_generations")
    op.drop_index("ix_image_generations_scene_variant_id", table_name="image_generations")
    op.drop_index("ix_image_generations_campaign_id", table_name="image_generations")
    op.drop_table("image_generations")

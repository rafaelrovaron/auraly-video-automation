"""Persist resumable Flow generation and candidate-slot checkpoints.

Revision ID: 0005_flow_generation_recovery
Revises: 0004_image_domain
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_flow_generation_recovery"
down_revision: str | None = "0004_image_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "flow_generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "image_generation_id",
            sa.String(36),
            sa.ForeignKey("image_generations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("required_candidate_count", sa.Integer(), nullable=False),
        sa.Column("required_resolution", sa.String(8), nullable=False),
        sa.Column("provider_workspace_path", sa.String(500), nullable=True),
        sa.Column("provider_workspace_fingerprint", sa.String(64), nullable=True),
        sa.Column("dispatch_attempt_number", sa.Integer(), nullable=False),
        sa.Column("dispatch_intent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grid_evidence_path", sa.String(500), nullable=True),
        sa.Column("grid_evidence_sha256", sa.String(64), nullable=True),
        sa.Column("last_failure_code", sa.String(100), nullable=True),
        sa.Column("provider_action_approved_by", sa.String(120), nullable=False),
        sa.Column("provider_action_approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("required_candidate_count = 2", name="flow_run_candidate_count"),
        sa.CheckConstraint("required_resolution = '2K'", name="flow_run_resolution"),
        sa.CheckConstraint(
            "stage IN ('prepared','inputs_verified','dispatch_intent_recorded',"
            "'dispatch_confirmed','candidates_observed','downloading','completed',"
            "'ambiguous','blocked','failed')",
            name="flow_run_stage",
        ),
        sa.CheckConstraint("dispatch_attempt_number > 0", name="flow_run_dispatch_attempt_number"),
        sa.CheckConstraint(
            "(provider_workspace_path IS NULL) = (provider_workspace_fingerprint IS NULL)",
            name="flow_run_workspace_pair",
        ),
        sa.CheckConstraint(
            "(grid_evidence_path IS NULL) = (grid_evidence_sha256 IS NULL)",
            name="flow_run_grid_evidence_pair",
        ),
        sa.CheckConstraint(
            "dispatch_confirmed_at IS NULL OR dispatch_intent_at IS NOT NULL",
            name="flow_run_dispatch_confirmation_requires_intent",
        ),
        sa.CheckConstraint(
            "dispatch_confirmed_at IS NULL OR dispatch_intent_at <= dispatch_confirmed_at",
            name="flow_run_dispatch_timestamp_order",
        ),
        sa.UniqueConstraint("image_generation_id", name="uq_flow_generation_runs_image_generation_id"),
    )
    op.create_index(
        "ix_flow_generation_runs_stage", "flow_generation_runs", ["stage"]
    )

    op.create_table(
        "flow_candidate_slots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "flow_generation_run_id",
            sa.String(36),
            sa.ForeignKey("flow_generation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("provider_slot_fingerprint", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("download_intent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("staging_path", sa.String(500), nullable=True),
        sa.Column("staged_sha256", sa.String(64), nullable=True),
        sa.Column(
            "image_candidate_id",
            sa.String(36),
            sa.ForeignKey("image_candidates.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("slot_index >= 0 AND slot_index < 2", name="flow_slot_index"),
        sa.CheckConstraint(
            "state IN ('pending','observed','download_intent_recorded','downloaded','ingested','blocked')",
            name="flow_slot_state",
        ),
        sa.CheckConstraint(
            "(staging_path IS NULL) = (staged_sha256 IS NULL)", name="flow_slot_staging_pair"
        ),
        sa.CheckConstraint(
            "(state = 'ingested') = (image_candidate_id IS NOT NULL)",
            name="flow_slot_ingested_candidate",
        ),
        sa.UniqueConstraint(
            "flow_generation_run_id", "slot_index", name="uq_flow_candidate_slots_run_index"
        ),
        sa.UniqueConstraint("image_candidate_id", name="uq_flow_candidate_slots_image_candidate_id"),
    )
    op.create_index(
        "ix_flow_candidate_slots_run_id", "flow_candidate_slots", ["flow_generation_run_id"]
    )
    op.create_index("ix_flow_candidate_slots_state", "flow_candidate_slots", ["state"])

    op.execute(
        """
        CREATE TRIGGER prevent_flow_generation_run_generation_update
        BEFORE UPDATE OF image_generation_id ON flow_generation_runs
        WHEN NEW.image_generation_id IS NOT OLD.image_generation_id
        BEGIN
            SELECT RAISE(ABORT, 'Flow run generation identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_flow_slot_candidate_generation_insert
        BEFORE INSERT ON flow_candidate_slots
        WHEN NEW.image_candidate_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM flow_generation_runs AS flow_run
            JOIN image_candidates AS candidate ON candidate.id = NEW.image_candidate_id
            WHERE flow_run.id = NEW.flow_generation_run_id
              AND candidate.image_generation_id = flow_run.image_generation_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Flow slot candidate must belong to run generation');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_flow_slot_candidate_generation_update
        BEFORE UPDATE OF flow_generation_run_id, image_candidate_id ON flow_candidate_slots
        WHEN NEW.image_candidate_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM flow_generation_runs AS flow_run
            JOIN image_candidates AS candidate ON candidate.id = NEW.image_candidate_id
            WHERE flow_run.id = NEW.flow_generation_run_id
              AND candidate.image_generation_id = flow_run.image_generation_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Flow slot candidate must belong to run generation');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS enforce_flow_slot_candidate_generation_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_flow_slot_candidate_generation_insert")
    op.execute("DROP TRIGGER IF EXISTS prevent_flow_generation_run_generation_update")
    op.drop_index("ix_flow_candidate_slots_state", table_name="flow_candidate_slots")
    op.drop_index("ix_flow_candidate_slots_run_id", table_name="flow_candidate_slots")
    op.drop_table("flow_candidate_slots")
    op.drop_index("ix_flow_generation_runs_stage", table_name="flow_generation_runs")
    op.drop_table("flow_generation_runs")

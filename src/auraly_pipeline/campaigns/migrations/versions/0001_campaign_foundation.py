"""Campaign foundation schema.

Revision ID: 0001_campaign_foundation
Revises:
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_campaign_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("character", sa.String(length=40), nullable=False),
        sa.Column("proof_object", sa.String(length=255), nullable=False),
        sa.Column("voice_preset", sa.String(length=120), nullable=False),
        sa.Column("edit_preset", sa.String(length=120), nullable=False),
        sa.Column("budget_json", sa.JSON(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'draft'", name="campaign_status"),
    )
    op.create_table(
        "copy_masters",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=80),
            sa.ForeignKey("campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("hook", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cta", sa.Text(), nullable=False),
        sa.Column("spoken_text", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("approval_state", sa.String(length=20), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "approval_state = 'approved' AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL",
            name="copy_master_approved",
        ),
        sa.CheckConstraint("version >= 1", name="copy_master_version"),
        sa.UniqueConstraint("campaign_id", "version", name="uq_copy_master_campaign_version"),
    )
    op.create_index("ix_copy_masters_campaign_id", "copy_masters", ["campaign_id"])
    op.execute(
        """
        CREATE TRIGGER prevent_approved_copy_master_update
        BEFORE UPDATE ON copy_masters
        WHEN OLD.approval_state = 'approved'
        BEGIN
            SELECT RAISE(ABORT, 'approved copy master is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_approved_copy_master_delete
        BEFORE DELETE ON copy_masters
        WHEN OLD.approval_state = 'approved'
        BEGIN
            SELECT RAISE(ABORT, 'approved copy master is immutable');
        END
        """
    )
    op.create_table(
        "scene_variants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=80),
            sa.ForeignKey("campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("variant_id", sa.String(length=80), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("time_atmosphere", sa.String(length=255), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("proof_object", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status = 'not_started'", name="scene_variant_status"
        ),
        sa.UniqueConstraint("campaign_id", "variant_id", name="uq_variant_campaign_variant"),
    )
    op.create_index("ix_scene_variants_campaign_id", "scene_variants", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_scene_variants_campaign_id", table_name="scene_variants")
    op.drop_table("scene_variants")
    op.execute("DROP TRIGGER IF EXISTS prevent_approved_copy_master_delete")
    op.execute("DROP TRIGGER IF EXISTS prevent_approved_copy_master_update")
    op.drop_index("ix_copy_masters_campaign_id", table_name="copy_masters")
    op.drop_table("copy_masters")
    op.drop_table("campaigns")

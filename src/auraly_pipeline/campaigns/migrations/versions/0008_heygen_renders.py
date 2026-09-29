"""Durable HeyGen video renders and paid reservations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_heygen_renders"
down_revision: str | None = "0007_heygen_remote_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "heygen_renders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(80), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column(
            "scene_variant_id", sa.String(36), sa.ForeignKey("scene_variants.id"), nullable=False
        ),
        sa.Column(
            "image_candidate_id",
            sa.String(36),
            sa.ForeignKey("image_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "voice_master_id", sa.String(36), sa.ForeignKey("voice_masters.id"), nullable=False
        ),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("provider_account_ref", sa.String(200), nullable=False),
        sa.Column("logical_key", sa.String(64), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("item_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("max_paid_renders", sa.Integer(), nullable=False),
        sa.Column("approved_by", sa.String(200), nullable=False),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote_video_id", sa.String(200), nullable=True),
        sa.Column("manual_binding", sa.Integer(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("logical_key", name="uq_heygen_render_logical"),
        sa.UniqueConstraint(
            "provider_account_ref", "remote_video_id", name="uq_heygen_render_remote"
        ),
        sa.CheckConstraint(
            "status IN ('planned','submitting','processing','download_pending','ready','failed','reconciliation_required')",
            name="heygen_render_status",
        ),
        sa.CheckConstraint("max_paid_renders > 0", name="heygen_render_budget"),
    )
    op.create_index("ix_heygen_renders_campaign_id", "heygen_renders", ["campaign_id"])


def downgrade() -> None:
    op.drop_table("heygen_renders")

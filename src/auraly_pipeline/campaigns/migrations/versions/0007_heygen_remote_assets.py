"""Reusable HeyGen remote assets.

Revision ID: 0007_heygen_remote_assets
Revises: 0006_manual_image_import
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007_heygen_remote_assets"
down_revision: str | None = "0006_manual_image_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remote_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_account_ref", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("remote_asset_id", sa.String(200), nullable=False),
        sa.Column("remote_batch_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_error_code", sa.String(120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider = 'heygen'", name="remote_asset_provider"),
        sa.CheckConstraint("kind IN ('image','audio')", name="remote_asset_kind"),
        sa.CheckConstraint("size_bytes > 0", name="remote_asset_size"),
        sa.CheckConstraint(
            "status IN ('allocated','processing','ready','failed','reconciliation_required')",
            name="remote_asset_status",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_account_ref",
            "kind",
            "sha256",
            name="uq_remote_asset_source",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_account_ref",
            "remote_asset_id",
            name="uq_remote_asset_provider_id",
        ),
    )
    op.create_index(
        "ix_remote_assets_provider_account_ref", "remote_assets", ["provider_account_ref"]
    )
    op.create_index("ix_remote_assets_remote_batch_id", "remote_assets", ["remote_batch_id"])
    op.create_index("ix_remote_assets_status", "remote_assets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_remote_assets_status", table_name="remote_assets")
    op.drop_index("ix_remote_assets_remote_batch_id", table_name="remote_assets")
    op.drop_index("ix_remote_assets_provider_account_ref", table_name="remote_assets")
    op.drop_table("remote_assets")

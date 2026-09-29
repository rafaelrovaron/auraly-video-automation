from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from auraly_pipeline.campaigns.db_models import Base


class RemoteAssetRow(Base):
    __tablename__ = "remote_assets"
    __table_args__ = (
        CheckConstraint("provider = 'heygen'", name="remote_asset_provider"),
        CheckConstraint("kind IN ('image','audio')", name="remote_asset_kind"),
        CheckConstraint("size_bytes > 0", name="remote_asset_size"),
        CheckConstraint(
            "status IN ('allocated','processing','ready','failed','reconciliation_required')",
            name="remote_asset_status",
        ),
        UniqueConstraint(
            "provider",
            "provider_account_ref",
            "kind",
            "sha256",
            name="uq_remote_asset_source",
        ),
        UniqueConstraint(
            "provider",
            "provider_account_ref",
            "remote_asset_id",
            name="uq_remote_asset_provider_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_account_ref: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    remote_asset_id: Mapped[str] = mapped_column(String(200))
    remote_batch_id: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HeyGenRenderRow(Base):
    __tablename__ = "heygen_renders"
    __table_args__ = (
        UniqueConstraint("logical_key", name="uq_heygen_render_logical"),
        UniqueConstraint("provider_account_ref", "remote_video_id", name="uq_heygen_render_remote"),
        CheckConstraint(
            "status IN ('planned','submitting','processing','download_pending','ready','failed','reconciliation_required')",
            name="heygen_render_status",
        ),
        CheckConstraint("max_paid_renders > 0", name="heygen_render_budget"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    scene_variant_id: Mapped[str] = mapped_column(ForeignKey("scene_variants.id"))
    image_candidate_id: Mapped[str] = mapped_column(ForeignKey("image_candidates.id"))
    voice_master_id: Mapped[str] = mapped_column(ForeignKey("voice_masters.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    provider_account_ref: Mapped[str] = mapped_column(String(200))
    logical_key: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    item_json: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    max_paid_renders: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[str] = mapped_column(String(200))
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    remote_video_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manual_binding: Mapped[int] = mapped_column(Integer, default=0)
    source_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

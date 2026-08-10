from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "pk": "pk_%(table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
        }
    )


class CampaignRow(Base):
    __tablename__ = "campaigns"
    __table_args__ = (CheckConstraint("status = 'draft'", name="campaign_status"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    character: Mapped[str] = mapped_column(String(40))
    proof_object: Mapped[str] = mapped_column(String(255))
    voice_preset: Mapped[str] = mapped_column(String(120))
    edit_preset: Mapped[str] = mapped_column(String(120))
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    copy_masters: Mapped[list[CopyMasterRow]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="CopyMasterRow.version",
        lazy="selectin",
    )
    scene_variants: Mapped[list[SceneVariantRow]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="SceneVariantRow.variant_id",
        lazy="selectin",
    )


class CopyMasterRow(Base):
    __tablename__ = "copy_masters"
    __table_args__ = (
        CheckConstraint(
            "approval_state = 'approved' AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL",
            name="copy_master_approved",
        ),
        CheckConstraint("version >= 1", name="copy_master_version"),
        UniqueConstraint("campaign_id", "version", name="uq_copy_master_campaign_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    source_text: Mapped[str] = mapped_column(Text)
    headline: Mapped[str] = mapped_column(Text)
    hook: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    cta: Mapped[str] = mapped_column(Text)
    spoken_text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    approval_state: Mapped[str] = mapped_column(String(20))
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[CampaignRow] = relationship(back_populates="copy_masters")


class SceneVariantRow(Base):
    __tablename__ = "scene_variants"
    __table_args__ = (
        CheckConstraint("status = 'not_started'", name="scene_variant_status"),
        UniqueConstraint("campaign_id", "variant_id", name="uq_variant_campaign_variant"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True
    )
    variant_id: Mapped[str] = mapped_column(String(80))
    location: Mapped[str] = mapped_column(String(255))
    time_atmosphere: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    proof_object: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[CampaignRow] = relationship(back_populates="scene_variants")

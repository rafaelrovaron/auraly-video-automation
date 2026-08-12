from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from auraly_pipeline.campaigns.db_models import Base


class VoiceMasterRow(Base):
    __tablename__ = "voice_masters"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','generating','processing','review_required','approved','rejected','failed')",
            name="voice_master_status",
        ),
        CheckConstraint("provider = 'elevenlabs'", name="voice_master_provider"),
        CheckConstraint(
            "provider_state IN ('not_dispatched','dispatching','response_received','ambiguous')",
            name="voice_provider_state",
        ),
        CheckConstraint(
            "copy_master_version >= 1 AND generation >= 1", name="voice_master_versions"
        ),
        CheckConstraint(
            "(status <> 'approved') OR (approved_at IS NOT NULL AND approved_by IS NOT NULL "
            "AND raw_audio_path IS NOT NULL AND raw_sha256 IS NOT NULL AND raw_size_bytes IS NOT NULL "
            "AND raw_format IS NOT NULL AND processed_audio_path IS NOT NULL AND processed_sha256 IS NOT NULL "
            "AND transcript_path IS NOT NULL AND transcript_sha256 IS NOT NULL "
            "AND manifest_path IS NOT NULL AND manifest_sha256 IS NOT NULL "
            "AND duration_seconds IS NOT NULL "
            "AND word_count IS NOT NULL AND wpm IS NOT NULL AND sample_rate IS NOT NULL AND channels IS NOT NULL "
            "AND loudness_lufs IS NOT NULL AND true_peak_dbfs IS NOT NULL "
            "AND leading_silence_seconds IS NOT NULL AND trailing_silence_seconds IS NOT NULL "
            "AND transcript_source IS NOT NULL AND transcript_match_status = 'matched' "
            "AND headline_spoken = 0 AND json_array_length(qc_findings_json) = 0 "
            "AND provider_state = 'response_received')",
            name="voice_master_approval",
        ),
        CheckConstraint(
            "(status <> 'rejected') OR "
            "(rejected_at IS NOT NULL AND rejected_by IS NOT NULL AND rejection_reason IS NOT NULL)",
            name="voice_master_rejection",
        ),
        UniqueConstraint("logical_key", name="uq_voice_masters_logical_key"),
        UniqueConstraint("copy_master_id", "generation", name="uq_voice_master_copy_generation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True
    )
    copy_master_id: Mapped[str] = mapped_column(
        ForeignKey("copy_masters.id", ondelete="RESTRICT"), index=True
    )
    copy_master_version: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    logical_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    voice_preset: Mapped[str] = mapped_column(String(120))
    voice_id: Mapped[str] = mapped_column(String(120))
    model_id: Mapped[str] = mapped_column(String(120))
    output_format: Mapped[str] = mapped_column(String(40))
    settings_json: Mapped[dict[str, object]] = mapped_column(JSON)
    settings_fingerprint: Mapped[str] = mapped_column(String(64))
    raw_audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processed_audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    transcript_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loudness_lufs: Mapped[float | None] = mapped_column(Float, nullable=True)
    true_peak_dbfs: Mapped[float | None] = mapped_column(Float, nullable=True)
    leading_silence_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    trailing_silence_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_internal_pauses_json: Mapped[list[list[float]]] = mapped_column(JSON)
    transcript_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    transcript_match_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transcript_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    headline_spoken: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qc_findings_json: Mapped[list[str]] = mapped_column(JSON)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_dispatched"
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

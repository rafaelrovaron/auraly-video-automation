"""Campaign-level Voice Master records and immutable approval history.

Revision ID: 0003_voice_master
Revises: 0002_persistent_job_orchestration
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_voice_master"
down_revision: str | None = "0002_persistent_job_orchestration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voice_masters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(80),
            sa.ForeignKey("campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "copy_master_id",
            sa.String(36),
            sa.ForeignKey("copy_masters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("copy_master_version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("logical_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("voice_preset", sa.String(120), nullable=False),
        sa.Column("voice_id", sa.String(120), nullable=False),
        sa.Column("model_id", sa.String(120), nullable=False),
        sa.Column("output_format", sa.String(40), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("settings_fingerprint", sa.String(64), nullable=False),
        sa.Column("raw_audio_path", sa.String(500), nullable=True),
        sa.Column("processed_audio_path", sa.String(500), nullable=True),
        sa.Column("transcript_path", sa.String(500), nullable=True),
        sa.Column("manifest_path", sa.String(500), nullable=True),
        sa.Column("raw_sha256", sa.String(64), nullable=True),
        sa.Column("processed_sha256", sa.String(64), nullable=True),
        sa.Column("transcript_sha256", sa.String(64), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=True),
        sa.Column("raw_format", sa.String(20), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("wpm", sa.Float(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("loudness_lufs", sa.Float(), nullable=True),
        sa.Column("true_peak_dbfs", sa.Float(), nullable=True),
        sa.Column("leading_silence_seconds", sa.Float(), nullable=True),
        sa.Column("trailing_silence_seconds", sa.Float(), nullable=True),
        sa.Column("long_internal_pauses_json", sa.JSON(), nullable=False),
        sa.Column("transcript_source", sa.String(40), nullable=True),
        sa.Column("transcript_match_status", sa.String(32), nullable=True),
        sa.Column("transcript_match_score", sa.Float(), nullable=True),
        sa.Column("headline_spoken", sa.Boolean(), nullable=True),
        sa.Column("qc_findings_json", sa.JSON(), nullable=False),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("provider_state", sa.String(20), nullable=False, server_default="not_dispatched"),
        sa.Column(
            "job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(120), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','generating','processing','review_required','approved','rejected','failed')",
            name="voice_master_status",
        ),
        sa.CheckConstraint("provider = 'elevenlabs'", name="voice_master_provider"),
        sa.CheckConstraint(
            "provider_state IN ('not_dispatched','dispatching','response_received','ambiguous')",
            name="voice_provider_state",
        ),
        sa.CheckConstraint(
            "copy_master_version >= 1 AND generation >= 1", name="voice_master_versions"
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "(status <> 'rejected') OR (rejected_at IS NOT NULL AND rejected_by IS NOT NULL AND rejection_reason IS NOT NULL)",
            name="voice_master_rejection",
        ),
        sa.UniqueConstraint("logical_key", name="uq_voice_masters_logical_key"),
        sa.UniqueConstraint("copy_master_id", "generation", name="uq_voice_master_copy_generation"),
        sa.UniqueConstraint("job_id", name="uq_voice_masters_job_id"),
    )
    op.create_index("ix_voice_masters_campaign_id", "voice_masters", ["campaign_id"])
    op.create_index("ix_voice_masters_copy_master_id", "voice_masters", ["copy_master_id"])
    op.create_index("ix_voice_masters_status", "voice_masters", ["status"])
    op.execute(
        """
        CREATE TRIGGER enforce_voice_initial_state
        BEFORE INSERT ON voice_masters
        WHEN NEW.status != 'pending'
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster must be inserted pending');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_voice_copy_campaign_insert
        BEFORE INSERT ON voice_masters
        WHEN NOT EXISTS (
            SELECT 1 FROM copy_masters
            WHERE id = NEW.copy_master_id
              AND campaign_id = NEW.campaign_id
              AND version = NEW.copy_master_version
              AND approval_state = 'approved'
        )
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster requires matching approved CopyMaster');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_voice_copy_campaign_update
        BEFORE UPDATE OF campaign_id, copy_master_id, copy_master_version ON voice_masters
        WHEN NOT EXISTS (
            SELECT 1 FROM copy_masters
            WHERE id = NEW.copy_master_id
              AND campaign_id = NEW.campaign_id
              AND version = NEW.copy_master_version
              AND approval_state = 'approved'
        )
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster requires matching approved CopyMaster');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_voice_master_replace
        BEFORE INSERT ON voice_masters
        WHEN EXISTS (
            SELECT 1 FROM voice_masters
            WHERE id = NEW.id
               OR logical_key = NEW.logical_key
               OR (copy_master_id = NEW.copy_master_id AND generation = NEW.generation)
               OR (NEW.job_id IS NOT NULL AND job_id = NEW.job_id)
        )
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster history cannot be replaced');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_voice_job_insert
        BEFORE INSERT ON voice_masters
        WHEN NEW.job_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM jobs
            WHERE id = NEW.job_id
              AND campaign_id = NEW.campaign_id
              AND job_type = 'voice.generate'
              AND json_extract(input_json, '$.voiceMasterId') = NEW.id
              AND idempotency_key = 'voice.generate:' || NEW.logical_key
        )
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster requires matching voice generation Job');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_voice_job_update
        BEFORE UPDATE OF job_id, campaign_id, logical_key ON voice_masters
        WHEN NEW.job_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM jobs
            WHERE id = NEW.job_id
              AND campaign_id = NEW.campaign_id
              AND job_type = 'voice.generate'
              AND json_extract(input_json, '$.voiceMasterId') = NEW.id
              AND idempotency_key = 'voice.generate:' || NEW.logical_key
        )
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster requires matching voice generation Job');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_job_event_uuid_insert
        BEFORE INSERT ON job_events
        WHEN length(NEW.id) != 36
          OR NEW.id GLOB '*[^0-9a-f-]*'
          OR substr(NEW.id, 9, 1) != '-'
          OR substr(NEW.id, 14, 1) != '-'
          OR substr(NEW.id, 19, 1) != '-'
          OR substr(NEW.id, 24, 1) != '-'
          OR substr(NEW.id, 15, 1) NOT IN ('1','2','3','4','5')
          OR substr(NEW.id, 20, 1) NOT IN ('8','9','a','b')
        BEGIN
            SELECT RAISE(ABORT, 'job event id must be a valid UUID');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_linked_voice_job_update
        BEFORE UPDATE OF input_json, campaign_id, job_type, idempotency_key ON jobs
        WHEN EXISTS (
            SELECT 1 FROM voice_masters
            WHERE job_id = OLD.id
              AND (
                  NEW.campaign_id IS NOT campaign_id
                  OR NEW.job_type IS NOT 'voice.generate'
                  OR json_extract(NEW.input_json, '$.voiceMasterId') IS NOT id
                  OR NEW.idempotency_key IS NOT 'voice.generate:' || logical_key
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'Job mutation violates linked VoiceMaster');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_linked_voice_job_delete
        BEFORE DELETE ON jobs
        WHEN EXISTS (SELECT 1 FROM voice_masters WHERE job_id = OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'linked VoiceMaster Job is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_voice_approval_gate
        BEFORE UPDATE OF status ON voice_masters
        WHEN NEW.status = 'approved' AND (
            OLD.status != 'review_required'
            OR NEW.headline_spoken != 0
            OR NEW.transcript_match_status != 'matched'
            OR json_array_length(NEW.qc_findings_json) != 0
            OR NEW.processed_audio_path IS NULL
            OR NEW.processed_sha256 IS NULL
            OR NEW.transcript_sha256 IS NULL
            OR NEW.manifest_sha256 IS NULL
            OR NEW.job_id IS NULL
            OR NEW.approved_at IS NULL
            OR NEW.approved_by IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster approval gate failed');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_final_voice_master_update
        BEFORE UPDATE ON voice_masters
        WHEN OLD.status IN ('approved','rejected')
        BEGIN
            SELECT RAISE(ABORT, 'final VoiceMaster is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_voice_master_delete
        BEFORE DELETE ON voice_masters
        BEGIN
            SELECT RAISE(ABORT, 'VoiceMaster history is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_voice_masters_one_approved_campaign
        ON voice_masters(campaign_id)
        WHERE status = 'approved'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_voice_masters_one_approved_campaign")
    op.execute("DROP TRIGGER IF EXISTS enforce_voice_initial_state")
    op.execute("DROP TRIGGER IF EXISTS prevent_voice_master_replace")
    op.execute("DROP TRIGGER IF EXISTS enforce_voice_approval_gate")
    op.execute("DROP TRIGGER IF EXISTS enforce_voice_job_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_job_event_uuid_insert")
    op.execute("DROP TRIGGER IF EXISTS enforce_linked_voice_job_update")
    op.execute("DROP TRIGGER IF EXISTS prevent_linked_voice_job_delete")
    op.execute("DROP TRIGGER IF EXISTS enforce_voice_job_insert")
    op.execute("DROP TRIGGER IF EXISTS enforce_voice_copy_campaign_update")
    op.execute("DROP TRIGGER IF EXISTS prevent_voice_master_delete")
    op.execute("DROP TRIGGER IF EXISTS prevent_final_voice_master_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_voice_copy_campaign_insert")
    op.drop_index("ix_voice_masters_status", table_name="voice_masters")
    op.drop_index("ix_voice_masters_copy_master_id", table_name="voice_masters")
    op.drop_index("ix_voice_masters_campaign_id", table_name="voice_masters")
    op.drop_table("voice_masters")

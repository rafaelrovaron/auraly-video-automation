"""Honest imported voice provenance, preserving legacy records and triggers."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_external_voice_import"
down_revision: str | None = "0008_heygen_renders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild(*, imported: bool) -> None:
    connection = op.get_bind()
    triggers = connection.execute(sa.text(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql LIKE '%voice_masters%'"
    )).all()
    for name, _ in triggers:
        op.execute(f'DROP TRIGGER "{name}"')
    approval = next(
        item["sqltext"] for item in sa.inspect(connection).get_check_constraints("voice_masters")
        if item["name"] == "ck_voice_masters_voice_master_approval"
    )
    with op.batch_alter_table("voice_masters", recreate="always") as batch:
        for name in ("voice_master_provider", "voice_provider_state", "voice_master_approval"):
            batch.drop_constraint(op.f("ck_voice_masters_" + name), type_="check")
        if imported:
            batch.create_check_constraint("voice_master_provider", "provider IN ('elevenlabs','imported')")
            batch.create_check_constraint("voice_provider_state",
                "provider_state IN ('not_dispatched','dispatching','response_received','ambiguous','local_ready')")
            batch.create_check_constraint("voice_master_origin_state",
                "(provider = 'elevenlabs' AND provider_state <> 'local_ready') OR "
                "(provider = 'imported' AND provider_state IN ('not_dispatched','local_ready') "
                "AND provider_request_id IS NULL AND voice_id = 'imported' AND model_id = 'external-audio-v1')")
            approval = approval.replace("provider_state = 'response_received'",
                "((provider = 'elevenlabs' AND provider_state = 'response_received') OR "
                "(provider = 'imported' AND provider_state = 'local_ready'))")
        else:
            batch.drop_constraint(op.f("ck_voice_masters_voice_master_origin_state"), type_="check")
            batch.create_check_constraint("voice_master_provider", "provider = 'elevenlabs'")
            batch.create_check_constraint("voice_provider_state",
                "provider_state IN ('not_dispatched','dispatching','response_received','ambiguous')")
            approval = approval.replace(
                "((provider = 'elevenlabs' AND provider_state = 'response_received') OR "
                "(provider = 'imported' AND provider_state = 'local_ready'))",
                "provider_state = 'response_received'")
        batch.create_check_constraint("voice_master_approval", approval)
    for name, sql in triggers:
        if name == "prevent_voice_provenance_update":
            continue
        if imported:
            sql = sql.replace("job_type = 'voice.generate'",
                "job_type = CASE NEW.provider WHEN 'imported' THEN 'voice.import' ELSE 'voice.generate' END")
            sql = sql.replace("idempotency_key = 'voice.generate:' || NEW.logical_key",
                "idempotency_key = CASE NEW.provider WHEN 'imported' THEN 'voice.import:' ELSE 'voice.generate:' END || NEW.logical_key")
            sql = sql.replace("NEW.job_type IS NOT 'voice.generate'",
                "NEW.job_type IS NOT CASE provider WHEN 'imported' THEN 'voice.import' ELSE 'voice.generate' END")
            sql = sql.replace("NEW.idempotency_key IS NOT 'voice.generate:' || logical_key",
                "NEW.idempotency_key IS NOT CASE provider WHEN 'imported' THEN 'voice.import:' ELSE 'voice.generate:' END || logical_key")
        else:
            sql = sql.replace("CASE NEW.provider WHEN 'imported' THEN 'voice.import' ELSE 'voice.generate' END", "'voice.generate'")
            sql = sql.replace("CASE NEW.provider WHEN 'imported' THEN 'voice.import:' ELSE 'voice.generate:' END", "'voice.generate:'")
            sql = sql.replace("CASE provider WHEN 'imported' THEN 'voice.import' ELSE 'voice.generate' END", "'voice.generate'")
            sql = sql.replace("CASE provider WHEN 'imported' THEN 'voice.import:' ELSE 'voice.generate:' END", "'voice.generate:'")
        op.execute(sql)
    if imported:
        op.execute("""
            CREATE TRIGGER prevent_voice_provenance_update BEFORE UPDATE OF provider ON voice_masters
            WHEN NEW.provider IS NOT OLD.provider
            BEGIN SELECT RAISE(ABORT, 'Voice origin is immutable'); END
        """)


def upgrade() -> None:
    _rebuild(imported=True)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM voice_masters WHERE provider='imported'")):
        raise RuntimeError("cannot downgrade while imported voices exist")
    _rebuild(imported=False)

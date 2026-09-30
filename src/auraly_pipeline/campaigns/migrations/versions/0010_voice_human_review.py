"""Audited human acceptance of a reviewable imported transcript."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010_voice_human_review"
down_revision: str | None = "0009_external_voice_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_QC = "transcript_match_status = 'matched' AND headline_spoken = 0 AND json_array_length(qc_findings_json) = 0"
_QC = """COALESCE(headline_spoken = 0 AND (
    (approval_review_reason IS NULL AND transcript_match_status = 'matched'
     AND json_array_length(qc_findings_json) = 0) OR
    (approval_review_reason IS NOT NULL AND provider = 'imported'
     AND transcript_match_status = 'review_required'
     AND json_array_length(qc_findings_json) = 1
     AND json_extract(qc_findings_json, '$[0]') = 'The narration transcript requires human review.')), 0)"""
_REASON_CHECK = """approval_review_reason IS NULL OR (status = 'approved'
    AND length(trim(approval_review_reason)) BETWEEN 1 AND 512
    AND instr(approval_review_reason, char(10)) = 0 AND instr(approval_review_reason, char(13)) = 0)"""


def _rebuild(*, human_review: bool) -> None:
    connection = op.get_bind()
    triggers = connection.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql LIKE '%voice_masters%'"
        )
    ).all()
    approval = next(
        item["sqltext"]
        for item in sa.inspect(connection).get_check_constraints("voice_masters")
        if item["name"] == "ck_voice_masters_voice_master_approval"
    )
    for name, _ in triggers:
        op.execute(f'DROP TRIGGER "{name}"')
    with op.batch_alter_table("voice_masters", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_voice_masters_voice_master_approval"), type_="check")
        if human_review:
            batch.add_column(sa.Column("approval_review_reason", sa.String(512), nullable=True))
            batch.create_check_constraint("voice_review_reason", _REASON_CHECK)
            approval = approval.replace(_OLD_QC, _QC)
        else:
            batch.drop_constraint(op.f("ck_voice_masters_voice_review_reason"), type_="check")
            batch.drop_column("approval_review_reason")
            approval = approval.replace(_QC, _OLD_QC)
        batch.create_check_constraint("voice_master_approval", approval)
    for name, sql in triggers:
        if name == "enforce_voice_approval_gate":
            old_guard = "OR NEW.transcript_match_status != 'matched'\n            OR json_array_length(NEW.qc_findings_json) != 0"
            new_guard = (
                "OR NOT ("
                + _QC.replace("headline_spoken", "NEW.headline_spoken")
                .replace("approval_review_reason", "NEW.approval_review_reason")
                .replace("transcript_match_status", "NEW.transcript_match_status")
                .replace("qc_findings_json", "NEW.qc_findings_json")
                .replace("provider =", "NEW.provider =")
                + ")"
            )
            sql = (
                sql.replace(old_guard, new_guard)
                if human_review
                else sql.replace(new_guard, old_guard)
            )
        op.execute(sql)


def upgrade() -> None:
    _rebuild(human_review=True)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM voice_masters WHERE approval_review_reason IS NOT NULL")
    ):
        raise RuntimeError("cannot downgrade while human review approvals exist")
    _rebuild(human_review=False)

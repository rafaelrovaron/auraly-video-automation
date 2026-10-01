"""Reject blank Unicode review reasons without rebuilding immutable voice history."""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_voice_reason_whitespace"
down_revision: str | None = "0010_voice_human_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Python str.strip() whitespace; sensitive-text sanitization remains application-owned.
    for event in ("INSERT", "UPDATE"):
        op.execute(f"""
            CREATE TRIGGER enforce_voice_reason_{event.lower()}
            BEFORE {event} ON voice_masters
            WHEN NEW.approval_review_reason IS NOT NULL AND (
                instr(NEW.approval_review_reason, char(0)) > 0 OR
                length(NEW.approval_review_reason) NOT BETWEEN 1 AND 512 OR
                length(trim(NEW.approval_review_reason,
                    char(9,10,11,12,13,28,29,30,31,32,133,160,5760,
                    8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288))) = 0
            )
            BEGIN SELECT RAISE(ABORT, 'invalid voice review reason'); END
        """)


def downgrade() -> None:
    for event in ("insert", "update"):
        op.execute(f"DROP TRIGGER enforce_voice_reason_{event}")

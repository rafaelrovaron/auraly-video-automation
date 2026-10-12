"""Add a user-facing campaign name without changing immutable campaign IDs."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0012_campaign_display_name"
down_revision: str | None = "0011_voice_reason_whitespace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("display_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "display_name")

"""Manual image candidate provenance and direct scene ownership.

Revision ID: 0006_manual_image_import
Revises: 0005_flow_generation_recovery
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_manual_image_import"
down_revision: str | None = "0005_flow_generation_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_candidate_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS enforce_flow_slot_candidate_generation_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_flow_slot_candidate_generation_insert")
    op.execute("DROP TRIGGER IF EXISTS enforce_single_approved_image_candidate_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_single_approved_image_candidate_insert")
    op.execute("DROP TRIGGER IF EXISTS prevent_image_candidate_artifact_update")


def _create_flow_slot_triggers(*, source_kind_guard: bool = True) -> None:
    source_kind_clause = (
        "AND candidate.source_kind = 'generated'" if source_kind_guard else ""
    )
    op.execute(
        f"""
        CREATE TRIGGER enforce_flow_slot_candidate_generation_insert
        BEFORE INSERT ON flow_candidate_slots
        WHEN NEW.image_candidate_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM flow_generation_runs AS flow_run
            JOIN image_candidates AS candidate ON candidate.id = NEW.image_candidate_id
            WHERE flow_run.id = NEW.flow_generation_run_id
              {source_kind_clause}
              AND candidate.image_generation_id = flow_run.image_generation_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Flow slot candidate must belong to run generation');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER enforce_flow_slot_candidate_generation_update
        BEFORE UPDATE OF flow_generation_run_id, image_candidate_id ON flow_candidate_slots
        WHEN NEW.image_candidate_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM flow_generation_runs AS flow_run
            JOIN image_candidates AS candidate ON candidate.id = NEW.image_candidate_id
            WHERE flow_run.id = NEW.flow_generation_run_id
              {source_kind_clause}
              AND candidate.image_generation_id = flow_run.image_generation_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Flow slot candidate must belong to run generation');
        END
        """
    )


def _create_candidate_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER prevent_image_candidate_artifact_update
        BEFORE UPDATE OF id, scene_variant_id, source_kind, image_generation_id,
          import_manifest_sha256, import_source_path, candidate_index, source_path, sha256,
          width, height, size_bytes, format
        ON image_candidates
        WHEN NEW.id IS NOT OLD.id
          OR NEW.scene_variant_id IS NOT OLD.scene_variant_id
          OR NEW.source_kind IS NOT OLD.source_kind
          OR NEW.image_generation_id IS NOT OLD.image_generation_id
          OR NEW.import_manifest_sha256 IS NOT OLD.import_manifest_sha256
          OR NEW.import_source_path IS NOT OLD.import_source_path
          OR NEW.candidate_index IS NOT OLD.candidate_index
          OR NEW.source_path IS NOT OLD.source_path
          OR NEW.sha256 IS NOT OLD.sha256
          OR NEW.width IS NOT OLD.width
          OR NEW.height IS NOT OLD.height
          OR NEW.size_bytes IS NOT OLD.size_bytes
          OR NEW.format IS NOT OLD.format
        BEGIN
            SELECT RAISE(ABORT, 'candidate artifact identity is immutable');
        END
        """
    )
    for action in ("INSERT", "UPDATE OF review_status"):
        suffix = "insert" if action == "INSERT" else "update"
        op.execute(
            f"""
            CREATE TRIGGER enforce_single_approved_image_candidate_{suffix}
            BEFORE {action} ON image_candidates
            WHEN NEW.review_status = 'approved' AND EXISTS (
                SELECT 1 FROM image_candidates AS existing
                WHERE existing.review_status = 'approved'
                  AND existing.scene_variant_id = NEW.scene_variant_id
                  AND existing.id <> NEW.id
            )
            BEGIN
                SELECT RAISE(ABORT, 'approved candidate already exists for SceneVariant');
            END
            """
        )
    _create_flow_slot_triggers()


def upgrade() -> None:
    _drop_candidate_triggers()
    op.add_column("image_candidates", sa.Column("scene_variant_id", sa.String(36), nullable=True))
    op.add_column("image_candidates", sa.Column("source_kind", sa.String(32), nullable=True))
    op.add_column(
        "image_candidates", sa.Column("import_manifest_sha256", sa.String(64), nullable=True)
    )
    op.add_column("image_candidates", sa.Column("import_source_path", sa.String(500), nullable=True))
    op.execute(
        """
        UPDATE image_candidates
        SET scene_variant_id = (
            SELECT scene_variant_id FROM image_generations
            WHERE image_generations.id = image_candidates.image_generation_id
        ), source_kind = 'generated'
        """
    )
    with op.batch_alter_table("image_candidates", recreate="always") as batch:
        batch.alter_column("scene_variant_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("source_kind", existing_type=sa.String(32), nullable=False)
        batch.alter_column(
            "image_generation_id", existing_type=sa.String(36), nullable=True
        )
        batch.create_foreign_key(
            "fk_image_candidates_scene_variant_id",
            "scene_variants",
            ["scene_variant_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "image_candidate_source_kind",
            "source_kind IN ('generated','manual_import')",
        )
        batch.create_check_constraint(
            "image_candidate_provenance",
            "(source_kind = 'generated' AND image_generation_id IS NOT NULL "
            "AND import_manifest_sha256 IS NULL AND import_source_path IS NULL) OR "
            "(source_kind = 'manual_import' AND image_generation_id IS NULL "
            "AND import_manifest_sha256 IS NOT NULL AND import_source_path IS NOT NULL)",
        )
    op.create_index(
        "ix_image_candidates_scene_variant_id", "image_candidates", ["scene_variant_id"]
    )
    op.create_index(
        "uq_manual_image_candidate_scene_sha",
        "image_candidates",
        ["scene_variant_id", "sha256"],
        unique=True,
        sqlite_where=sa.text("source_kind = 'manual_import'"),
    )
    _create_candidate_triggers()


def _create_legacy_candidate_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER prevent_image_candidate_artifact_update
        BEFORE UPDATE OF id, image_generation_id, candidate_index, source_path, sha256,
          width, height, size_bytes, format
        ON image_candidates
        WHEN NEW.id IS NOT OLD.id
          OR NEW.image_generation_id IS NOT OLD.image_generation_id
          OR NEW.candidate_index IS NOT OLD.candidate_index
          OR NEW.source_path IS NOT OLD.source_path
          OR NEW.sha256 IS NOT OLD.sha256
          OR NEW.width IS NOT OLD.width
          OR NEW.height IS NOT OLD.height
          OR NEW.size_bytes IS NOT OLD.size_bytes
          OR NEW.format IS NOT OLD.format
        BEGIN
            SELECT RAISE(ABORT, 'candidate artifact identity is immutable');
        END
        """
    )
    for action in ("INSERT", "UPDATE OF review_status"):
        suffix = "insert" if action == "INSERT" else "update"
        op.execute(
            f"""
            CREATE TRIGGER enforce_single_approved_image_candidate_{suffix}
            BEFORE {action} ON image_candidates
            WHEN NEW.review_status = 'approved' AND EXISTS (
                SELECT 1
                FROM image_candidates AS existing
                JOIN image_generations AS existing_generation
                  ON existing_generation.id = existing.image_generation_id
                JOIN image_generations AS new_generation
                  ON new_generation.id = NEW.image_generation_id
                WHERE existing.review_status = 'approved'
                  AND existing_generation.scene_variant_id = new_generation.scene_variant_id
                  AND existing.id <> NEW.id
            )
            BEGIN
                SELECT RAISE(ABORT, 'approved candidate already exists for SceneVariant');
            END
            """
        )
    _create_flow_slot_triggers(source_kind_guard=False)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT COUNT(*) FROM image_candidates WHERE source_kind='manual_import'")
    ):
        raise RuntimeError("cannot downgrade while manual image candidates exist")
    _drop_candidate_triggers()
    op.drop_index("uq_manual_image_candidate_scene_sha", table_name="image_candidates")
    op.drop_index("ix_image_candidates_scene_variant_id", table_name="image_candidates")
    with op.batch_alter_table("image_candidates", recreate="always") as batch:
        batch.drop_constraint("image_candidate_provenance", type_="check")
        batch.drop_constraint("image_candidate_source_kind", type_="check")
        batch.drop_constraint("fk_image_candidates_scene_variant_id", type_="foreignkey")
        batch.alter_column(
            "image_generation_id", existing_type=sa.String(36), nullable=False
        )
        batch.drop_column("import_source_path")
        batch.drop_column("import_manifest_sha256")
        batch.drop_column("source_kind")
        batch.drop_column("scene_variant_id")
    _create_legacy_candidate_triggers()

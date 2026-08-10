from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, CopyMasterRow, SceneVariantRow
from auraly_pipeline.campaigns.domain import (
    Campaign,
    CampaignCreate,
    CopyMaster,
    CopyMasterCreate,
    SceneVariant,
)
from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.campaigns.repository import CampaignRepository


class CampaignError(Exception):
    """Base error with a stable public message."""

    public_message = "The campaign operation failed safely."


class CampaignAlreadyExistsError(CampaignError):
    public_message = "A campaign with this ID already exists."


class CampaignNotFoundError(CampaignError):
    public_message = "Campaign not found."


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CampaignService:
    """Application boundary for deterministic campaign operations."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def for_database(cls, database_path: Path) -> CampaignService:
        migrate_database(database_path)
        return cls(create_sqlite_engine(database_path))

    def close(self) -> None:
        self._engine.dispose()

    def create_campaign(self, request: CampaignCreate) -> Campaign:
        now = datetime.now(UTC)
        with self._sessions() as session:
            repository = CampaignRepository(session)
            if repository.get(request.campaign_id) is not None:
                raise CampaignAlreadyExistsError
            campaign = CampaignRow(
                id=request.campaign_id,
                character=request.character,
                proof_object=request.proof_object,
                voice_preset=request.voice_preset,
                edit_preset=request.edit_preset,
                budget_json=dict(request.budget),
                config_json=dict(request.config),
                status=request.status,
                created_at=now,
                updated_at=now,
                copy_masters=[self._copy_row(request.campaign_id, 1, request.copy_master, now)],
                scene_variants=[
                    SceneVariantRow(
                        id=str(uuid4()),
                        campaign_id=request.campaign_id,
                        variant_id=variant.variant_id,
                        location=variant.location,
                        time_atmosphere=variant.time_atmosphere,
                        action=variant.action,
                        prompt=variant.prompt,
                        proof_object=variant.proof_object,
                        status=variant.status,
                        created_at=now,
                        updated_at=now,
                    )
                    for variant in request.scene_variants
                ],
            )
            try:
                repository.add(campaign)
                repository.commit()
            except IntegrityError as exc:
                repository.rollback()
                raise CampaignAlreadyExistsError from exc
            return self._to_domain(campaign)

    def get_campaign(self, campaign_id: str) -> Campaign:
        with self._sessions() as session:
            campaign = CampaignRepository(session).get(campaign_id)
            if campaign is None:
                raise CampaignNotFoundError
            return self._to_domain(campaign)

    def list_campaigns(self) -> list[Campaign]:
        with self._sessions() as session:
            return [self._to_domain(campaign) for campaign in CampaignRepository(session).list()]

    def add_copy_master_version(
        self,
        campaign_id: str,
        copy_master: CopyMasterCreate,
    ) -> Campaign:
        now = datetime.now(UTC)
        with self._sessions() as session:
            repository = CampaignRepository(session)
            campaign = repository.get(campaign_id)
            if campaign is None:
                raise CampaignNotFoundError
            next_version = max(item.version for item in campaign.copy_masters) + 1
            repository.add_copy_master(
                self._copy_row(campaign_id, next_version, copy_master, now)
            )
            campaign.updated_at = now
            repository.commit()
        return self.get_campaign(campaign_id)

    @staticmethod
    def _copy_row(
        campaign_id: str,
        version: int,
        copy_master: CopyMasterCreate,
        now: datetime,
    ) -> CopyMasterRow:
        approved_at = now if copy_master.approval_state == "approved" else None
        return CopyMasterRow(
            id=str(uuid4()),
            campaign_id=campaign_id,
            version=version,
            source_text=copy_master.source_text,
            headline=copy_master.headline,
            hook=copy_master.hook,
            body=copy_master.body,
            cta=copy_master.cta,
            spoken_text=copy_master.spoken_text,
            sha256=copy_master.sha256,
            approval_state=copy_master.approval_state,
            approved_by=copy_master.approved_by,
            approved_at=approved_at,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _to_domain(row: CampaignRow) -> Campaign:
        copy_masters = [
            CopyMaster(
                copy_master_id=item.id,
                campaign_id=item.campaign_id,
                version=item.version,
                source_text=item.source_text,
                headline=item.headline,
                hook=item.hook,
                body=item.body,
                cta=item.cta,
                approval_state=cast(Literal["draft", "approved"], item.approval_state),
                approved_by=item.approved_by,
                approved_at=_utc(item.approved_at) if item.approved_at else None,
                created_at=_utc(item.created_at),
                updated_at=_utc(item.updated_at),
            )
            for item in sorted(row.copy_masters, key=lambda item: item.version)
        ]
        variants = [
            SceneVariant(
                scene_variant_id=item.id,
                campaign_id=item.campaign_id,
                variant_id=item.variant_id,
                location=item.location,
                time_atmosphere=item.time_atmosphere,
                action=item.action,
                prompt=item.prompt,
                proof_object=item.proof_object,
                status=cast(Literal["not_started"], item.status),
                created_at=_utc(item.created_at),
                updated_at=_utc(item.updated_at),
            )
            for item in sorted(row.scene_variants, key=lambda item: item.variant_id)
        ]
        return Campaign(
            campaign_id=row.id,
            character=cast(Literal["susan-smith", "soul-constellation"], row.character),
            proof_object=row.proof_object,
            voice_preset=row.voice_preset,
            edit_preset=row.edit_preset,
            budget=cast("dict[str, JsonValue]", row.budget_json),
            config=cast("dict[str, JsonValue]", row.config_json),
            status=cast(Literal["draft"], row.status),
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
            copy_masters=copy_masters,
            scene_variants=variants,
        )

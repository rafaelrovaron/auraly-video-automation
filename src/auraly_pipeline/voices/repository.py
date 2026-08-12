from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from auraly_pipeline.campaigns.db_models import CampaignRow, CopyMasterRow
from auraly_pipeline.voices.db_models import VoiceMasterRow


class VoiceMasterRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def campaign(self, campaign_id: str) -> CampaignRow | None:
        return self._session.get(CampaignRow, campaign_id)

    def approved_copy(self, campaign_id: str, version: int | None = None) -> CopyMasterRow | None:
        statement = select(CopyMasterRow).where(
            CopyMasterRow.campaign_id == campaign_id,
            CopyMasterRow.approval_state == "approved",
        )
        if version is None:
            statement = statement.order_by(CopyMasterRow.version.desc())
        else:
            statement = statement.where(CopyMasterRow.version == version)
        return self._session.execute(statement.limit(1)).scalar_one_or_none()

    def get(self, voice_master_id: str) -> VoiceMasterRow | None:
        return self._session.get(VoiceMasterRow, voice_master_id)

    def by_logical_key(self, logical_key: str) -> VoiceMasterRow | None:
        return self._session.execute(
            select(VoiceMasterRow).where(VoiceMasterRow.logical_key == logical_key)
        ).scalar_one_or_none()

    def list(
        self, *, campaign_id: str | None = None, status: str | None = None
    ) -> Sequence[VoiceMasterRow]:
        statement: Select[tuple[VoiceMasterRow]] = select(VoiceMasterRow)
        if campaign_id is not None:
            statement = statement.where(VoiceMasterRow.campaign_id == campaign_id)
        if status is not None:
            statement = statement.where(VoiceMasterRow.status == status)
        statement = statement.order_by(VoiceMasterRow.created_at, VoiceMasterRow.id)
        return self._session.execute(statement).scalars().all()

    def approved_for_campaign(self, campaign_id: str) -> VoiceMasterRow | None:
        return self._session.execute(
            select(VoiceMasterRow).where(
                VoiceMasterRow.campaign_id == campaign_id,
                VoiceMasterRow.status == "approved",
            )
        ).scalar_one_or_none()

    def next_generation(self, copy_master_id: str) -> int:
        current = self._session.execute(
            select(func.max(VoiceMasterRow.generation)).where(
                VoiceMasterRow.copy_master_id == copy_master_id
            )
        ).scalar_one()
        return int(current or 0) + 1

    def add(self, row: VoiceMasterRow) -> None:
        self._session.add(row)
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

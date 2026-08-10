from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from auraly_pipeline.campaigns.db_models import CampaignRow, CopyMasterRow


class CampaignRepository:
    """SQLAlchemy persistence operations without application policy decisions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _campaign_query() -> Select[tuple[CampaignRow]]:
        return select(CampaignRow).options(
            selectinload(CampaignRow.copy_masters),
            selectinload(CampaignRow.scene_variants),
        )

    def get(self, campaign_id: str) -> CampaignRow | None:
        statement = self._campaign_query().where(CampaignRow.id == campaign_id)
        return self._session.execute(statement).scalar_one_or_none()

    def list(self) -> Sequence[CampaignRow]:
        statement = self._campaign_query().order_by(CampaignRow.created_at, CampaignRow.id)
        return self._session.execute(statement).scalars().all()

    def add(self, campaign: CampaignRow) -> None:
        self._session.add(campaign)
        self._session.flush()

    def add_copy_master(self, copy_master: CopyMasterRow) -> None:
        self._session.add(copy_master)
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

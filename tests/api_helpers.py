from __future__ import annotations

from pathlib import Path
import sqlite3

from auraly_pipeline.api.contracts import ApiSettings
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from tests.test_campaign_domain import valid_campaign_data


def create_api_fixture(tmp_path: Path) -> ApiSettings:
    db = tmp_path / "api.db"
    service = CampaignService.for_database(db)
    data = valid_campaign_data()
    data["campaignId"] = "campaign-one"
    data["sceneVariants"] = data["sceneVariants"][:1]
    try:
        service.create_campaign(CampaignCreate.model_validate(data))
    finally:
        service.close()
    return ApiSettings.from_options(project_root=tmp_path, database=db)


def database_dump(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        return list(connection.iterdump())

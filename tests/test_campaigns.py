from __future__ import annotations

from pathlib import Path
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from auraly_pipeline.campaigns.domain import CampaignCreate, CopyMasterCreate
from auraly_pipeline.campaigns.persistence import sqlite_url
from auraly_pipeline.campaigns.service import (
    CampaignAlreadyExistsError,
    CampaignNotFoundError,
    CampaignService,
)
from tests.test_campaign_domain import valid_campaign_data


def test_campaign_persists_across_application_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    request = CampaignCreate.model_validate(valid_campaign_data())

    first_process = CampaignService.for_database(database_path)
    created = first_process.create_campaign(request)
    first_process.close()

    restarted_process = CampaignService.for_database(database_path)
    retrieved = restarted_process.get_campaign("eight-of-cups-pilot")
    restarted_process.close()

    assert created.campaign_id == "eight-of-cups-pilot"
    assert retrieved == created
    assert created.status == "draft"
    assert len(created.copy_masters) == 1
    assert created.copy_masters[0].version == 1
    assert created.copy_masters[0].approval_state == "approved"
    assert len(created.scene_variants) == 3
    assert {variant.campaign_id for variant in created.scene_variants} == {
        "eight-of-cups-pilot"
    }


def test_approved_copy_change_creates_new_immutable_version(tmp_path: Path) -> None:
    service = CampaignService.for_database(tmp_path / "auraly.db")
    original = service.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    original_copy = original.copy_masters[0]

    updated = service.add_copy_master_version(
        "eight-of-cups-pilot",
        CopyMasterCreate(
            source_text="Revised canonical source",
            headline="HE RETURNS AFTER YOU RELEASE HIM",
            hook="You finally released the attachment.",
            body="That changed what you were willing to accept.",
            cta="Take the one-minute reading.",
            approval_state="approved",
            approved_by="rafael",
        ),
    )
    service.close()

    assert [copy.version for copy in updated.copy_masters] == [1, 2]
    assert updated.copy_masters[0] == original_copy
    assert updated.copy_masters[1].sha256 != original_copy.sha256
    assert updated.copy_masters[1].approval_state == "approved"


def test_database_rejects_mutation_of_approved_copy_master(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    service = CampaignService.for_database(database_path)
    service.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    service.close()

    engine = create_engine(sqlite_url(database_path))
    with pytest.raises(DBAPIError, match="approved copy master is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE copy_masters SET headline = 'MUTATED' WHERE version = 1")
            )
    with pytest.raises(DBAPIError, match="approved copy master is immutable"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM copy_masters WHERE version = 1"))
    engine.dispose()


def test_duplicate_campaign_is_rejected_without_overwrite(tmp_path: Path) -> None:
    service = CampaignService.for_database(tmp_path / "auraly.db")
    request = CampaignCreate.model_validate(valid_campaign_data())
    original = service.create_campaign(request)

    with pytest.raises(CampaignAlreadyExistsError):
        service.create_campaign(request)

    assert service.get_campaign(request.campaign_id) == original
    service.close()


def test_list_campaigns_is_deterministic_and_get_missing_is_safe(tmp_path: Path) -> None:
    service = CampaignService.for_database(tmp_path / "auraly.db")
    second_data = deepcopy(valid_campaign_data())
    second_data["campaignId"] = "second-campaign"
    service.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    service.create_campaign(CampaignCreate.model_validate(second_data))

    listed = service.list_campaigns()

    assert [campaign.campaign_id for campaign in listed] == [
        "eight-of-cups-pilot",
        "second-campaign",
    ]
    with pytest.raises(CampaignNotFoundError):
        service.get_campaign("missing-campaign")
    service.close()

from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from auraly_pipeline.api.contracts import ApiSettings
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.editing.batch_domain import EditBatchRequest
from auraly_pipeline.jobs.domain import JobSubmit
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


def create_ready_api_fixture(tmp_path: Path, mp4: bytes) -> tuple[ApiSettings, EditBatchRequest]:
    from tests.test_editing_batch_service import setup_batch

    database, work, request = setup_batch(tmp_path, mp4)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("UPDATE campaigns SET character='soul-constellation'")
        for job in connection.execute("SELECT * FROM jobs").fetchall():
            submission = JobSubmit(
                job_type=job["job_type"], campaign_id=job["campaign_id"],
                scene_variant_id=job["scene_variant_id"], idempotency_key=job["idempotency_key"],
                input=json.loads(job["input_json"]), priority=job["priority"],
                max_attempts=job["max_attempts"], retry_safety=job["retry_safety"],
            )
            connection.execute("UPDATE jobs SET request_fingerprint=? WHERE id=?",
                               (submission.request_fingerprint, job["id"]))
    return ApiSettings(tmp_path, work, database), request

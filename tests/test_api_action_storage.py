from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns import persistence
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images import import_batch
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.handlers import RetryOnceHandler
from auraly_pipeline.jobs.repository import JobRepository
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.voices.service import VoiceMasterService
from tests.api_helpers import create_api_fixture
from tests.test_campaign_domain import valid_campaign_data
from tests.test_job_service import MutableClock


def existing(database: Path) -> Engine:
    factory: Any = getattr(persistence, "create_existing_sqlite_engine", None)
    assert callable(factory), "existing writable engine not implemented"
    return factory(database)


def test_existing_writer_never_creates_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "api.db"
    with pytest.raises(ValueError, match="existing regular database required"):
        existing(missing)
    assert not missing.parent.exists()


def test_existing_writer_preserves_journal_and_rejects_incompatible_db(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with sqlite3.connect(settings.database) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
    engine = existing(settings.database)
    try:
        with engine.begin() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "delete"
            connection.execute(text("UPDATE campaigns SET status='draft'"))
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='old'"))
        with pytest.raises(ValueError, match="incompatible database"):
            existing(settings.database)
    finally:
        engine.dispose()


def test_borrowed_image_service_prepares_without_migration_or_engine_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = create_api_fixture(tmp_path)
    engine = persistence.create_sqlite_engine(settings.database)
    disposed: list[bool] = []
    event.listen(engine, "engine_disposed", lambda _: disposed.append(True))

    def forbidden_migration(_: Path) -> None:
        raise AssertionError("borrowed service must not migrate")

    monkeypatch.setattr(import_batch, "migrate_database", forbidden_migration)
    constructor: Any = getattr(import_batch.ImageImportService, "from_engine", None)
    assert callable(constructor), "borrowed image engine not implemented"
    service = constructor(engine, work_root=settings.work_root)
    try:
        prepared = service.prepare_directory("campaign-one", settings.work_root / "inbox")
        assert prepared.variant_count == 1
        assert prepared.manifest_path.is_file()
        service.close()
        assert disposed == []
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM campaigns")).scalar() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("state", ["queued", "stale", "due_retry"])
@pytest.mark.parametrize("scope", ["campaign", "type", "both"])
def test_scoped_claim_leaves_foreign_job_and_audit_unchanged(
    tmp_path: Path, state: str, scope: str,
) -> None:
    settings = create_api_fixture(tmp_path)
    engine = persistence.create_sqlite_engine(settings.database)
    campaigns = CampaignService(engine)
    data = deepcopy(valid_campaign_data())
    data["campaignId"] = "campaign-two"
    campaigns.create_campaign(CampaignCreate.model_validate(data))
    clock = MutableClock()
    repository = JobRepository(sessionmaker(engine, expire_on_commit=False, class_=Session))
    jobs = JobService(engine, repository, clock=clock, handlers={
        "fake.success": RetryOnceHandler(), "fake.retry-once": RetryOnceHandler(),
    })
    foreign_campaign = "campaign-two" if scope in {"campaign", "both"} else "campaign-one"
    foreign_type = "fake.retry-once" if scope in {"type", "both"} else "fake.success"
    foreign = jobs.submit_job(JobSubmit(
        campaign_id=foreign_campaign, job_type=foreign_type,
        idempotency_key="foreign", input={}, max_attempts=3,
    ))
    if state == "stale":
        jobs.claim_next_job("old-worker", lease_seconds=10,
                            campaign_id=foreign_campaign, job_type=foreign_type)
    elif state == "due_retry":
        assert jobs.worker_once("old-worker", campaign_id=foreign_campaign,
                                job_type=foreign_type) is not None
    selected = jobs.submit_job(JobSubmit(
        campaign_id="campaign-one", job_type="fake.success",
        idempotency_key="selected", input={}, max_attempts=3,
    ))
    clock.advance(60)
    before = jobs.get_job(foreign.job_id)
    try:
        claimed = jobs.claim_next_job("api-worker", campaign_id="campaign-one",
                                      job_type="fake.success")
        assert claimed is not None
        assert claimed.job_id == selected.job_id
        assert claimed.campaign_id == "campaign-one"
        assert claimed.job_type == "fake.success"
        assert jobs.get_job(foreign.job_id) == before
    finally:
        engine.dispose()


def test_voice_worker_respects_campaign_and_type(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    engine = persistence.create_sqlite_engine(settings.database)
    jobs = JobService(engine, JobRepository(sessionmaker(engine, expire_on_commit=False)))
    other = jobs.submit_job(JobSubmit(
        campaign_id="campaign-one", job_type="fake.blocked", idempotency_key="other", input={},
    ))
    selected = jobs.submit_job(JobSubmit(
        campaign_id="campaign-one", job_type="fake.success", idempotency_key="selected", input={},
    ))
    service = VoiceMasterService(engine, work_root=settings.work_root)
    try:
        completed = service.worker_once(
            "voice-worker", campaign_id="campaign-one", job_type="fake.success",
        )
        assert completed is not None and completed.job_id == selected.job_id
        assert completed.status == "completed"
        assert jobs.get_job(other.job_id).attempt_count == 0
    finally:
        engine.dispose()

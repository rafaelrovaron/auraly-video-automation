from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.campaigns.persistence import create_sqlite_engine, sqlite_url
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.handlers import SuccessHandler
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.voices.db_models import VoiceMasterRow
from tests.test_campaign_domain import valid_campaign_data


def config_for(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sqlite_url(database))
    return config


def pending_voice(database: Path, *, provider: str, job_type: str) -> str:
    campaign_service = CampaignService.for_database(database)
    campaign = campaign_service.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaign_service.close()
    voice_id = str(uuid4())
    key = "a" * 64
    jobs = JobService.for_database(database, handlers={job_type: SuccessHandler()})
    job = jobs.submit_job(JobSubmit(
        campaign_id=campaign.campaign_id, job_type=job_type,
        idempotency_key=job_type + ":" + key, input={"voiceMasterId": voice_id}))
    jobs.close()
    now = datetime.now(UTC)
    engine = create_sqlite_engine(database)
    try:
        with Session(engine) as session:
            session.add(VoiceMasterRow(
                id=voice_id, campaign_id=campaign.campaign_id,
                copy_master_id=campaign.copy_masters[0].copy_master_id, copy_master_version=1,
                generation=1, logical_key=key, status="pending", provider=provider,
                voice_preset=campaign.voice_preset, voice_id="imported", model_id="external-audio-v1",
                output_format="wav", settings_json={}, settings_fingerprint="b" * 64,
                long_internal_pauses_json=[], qc_findings_json=[], provider_state="not_dispatched",
                job_id=job.job_id, created_at=now, updated_at=now))
            session.commit()
    finally:
        engine.dispose()
    return voice_id


def test_imported_voice_requires_matching_import_job_and_local_state(tmp_path: Path) -> None:
    database = tmp_path / "import.db"
    voice_id = pending_voice(database, provider="imported", job_type="voice.import")
    engine = create_sqlite_engine(database)
    with engine.begin() as connection:
        connection.execute(text("UPDATE voice_masters SET provider_state='local_ready' WHERE id=:id"),
                           {"id": voice_id})
    for statement in (
        "UPDATE voice_masters SET provider_state='response_received'",
        "UPDATE voice_masters SET provider_request_id='fake-remote-id'",
        "UPDATE voice_masters SET provider='elevenlabs'",
        "UPDATE jobs SET job_type='voice.generate'",
        "UPDATE voice_masters SET status='approved'",
        "DELETE FROM voice_masters",
        "DELETE FROM jobs",
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(statement))
    engine.dispose()
    with pytest.raises(RuntimeError, match="imported"):
        command.downgrade(config_for(database), "0008_heygen_renders")
    engine = create_sqlite_engine(database)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM voice_masters")) == 1
    engine.dispose()


@pytest.mark.parametrize("provider,job_type", [
    ("imported", "voice.generate"), ("elevenlabs", "voice.import")
])
def test_voice_origin_cannot_cross_job_types(tmp_path: Path, provider: str, job_type: str) -> None:
    with pytest.raises(IntegrityError):
        pending_voice(tmp_path / "cross.db", provider=provider, job_type=job_type)


def test_migration_roundtrip_preserves_elevenlabs_and_child_references(tmp_path: Path) -> None:
    database = tmp_path / "upgrade.db"
    voice_id = pending_voice(database, provider="elevenlabs", job_type="voice.generate")
    config = config_for(database)
    command.downgrade(config, "0008_heygen_renders")
    engine = create_sqlite_engine(database)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE migration_child (voice_id TEXT REFERENCES voice_masters(id))"))
        connection.execute(text("INSERT INTO migration_child VALUES (:id)"), {"id": voice_id})
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_sqlite_engine(database)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT provider FROM voice_masters WHERE id=:id"), {"id": voice_id}) == "elevenlabs"
        assert connection.scalar(text("SELECT voice_id FROM migration_child")) == voice_id
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_migration_foreign_key_violation_rolls_back_schema_changes(tmp_path: Path) -> None:
    database = tmp_path / "invalid.db"
    config = config_for(database)
    command.upgrade(config, "0008_heygen_renders")
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE broken_child (voice_id TEXT REFERENCES voice_masters(id))")
        connection.execute("INSERT INTO broken_child VALUES ('missing-voice')")
    with pytest.raises(RuntimeError, match="foreign key"):
        command.upgrade(config, "head")
    engine = create_sqlite_engine(database)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0008_heygen_renders"
        assert "local_ready" not in connection.scalar(text("SELECT sql FROM sqlite_master WHERE name='voice_masters'"))
    engine.dispose()

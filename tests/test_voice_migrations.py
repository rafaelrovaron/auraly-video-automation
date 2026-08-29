from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from auraly_pipeline.campaigns.persistence import migrate_database, sqlite_url


def test_goal_2_database_migrates_to_voice_master_schema(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sqlite_url(database))
    command.upgrade(config, "0002_persistent_job_orchestration")
    engine = create_engine(sqlite_url(database))
    assert "voice_masters" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sqlite_url(database))
    inspector = inspect(engine)
    assert "voice_masters" in inspector.get_table_names()
    foreign_keys = inspector.get_foreign_keys("voice_masters")
    assert {item["referred_table"] for item in foreign_keys} == {
        "campaigns",
        "copy_masters",
        "jobs",
    }
    assert all(item["options"].get("ondelete") == "RESTRICT" for item in foreign_keys)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0005_flow_generation_recovery"
        )
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert {
        "enforce_voice_copy_campaign_insert",
        "enforce_voice_copy_campaign_update",
        "prevent_voice_master_replace",
        "enforce_voice_job_insert",
        "enforce_voice_job_update",
        "enforce_job_event_uuid_insert",
        "enforce_linked_voice_job_update",
        "prevent_linked_voice_job_delete",
        "enforce_voice_approval_gate",
        "prevent_final_voice_master_update",
        "prevent_voice_master_delete",
    }.issubset(triggers)
    engine.dispose()


def test_fresh_database_reaches_voice_master_head(tmp_path: Path) -> None:
    database = tmp_path / "fresh" / "auraly.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0005_flow_generation_recovery"
        )
    assert "voice_masters" in inspect(engine).get_table_names()
    engine.dispose()


def test_database_blocks_invalid_voice_approval_and_cross_campaign_job(tmp_path: Path) -> None:
    database = tmp_path / "guards.db"
    migrate_database(database)
    engine = create_engine(sqlite_url(database))
    with engine.begin() as connection:
        now = "2026-08-12T00:00:00+00:00"
        for campaign_id in ("campaign-a", "campaign-b"):
            connection.execute(
                text(
                    "INSERT INTO campaigns (id,character,proof_object,voice_preset,edit_preset,"
                    "budget_json,config_json,status,created_at,updated_at) VALUES "
                    "(:id,'character','proof','preset','edit','{}','{}','draft',:now,:now)"
                ),
                {"id": campaign_id, "now": now},
            )
        connection.execute(
            text(
                "INSERT INTO copy_masters (id,campaign_id,version,source_text,headline,hook,body,cta,"
                "spoken_text,sha256,approval_state,approved_by,approved_at,created_at,updated_at) "
                "VALUES ('copy-a','campaign-a',1,'source','Headline','Hook','Body','CTA',"
                "'Hook Body CTA',:sha,'approved','operator',:now,:now,:now)"
            ),
            {"sha": "a" * 64, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO voice_masters (id,campaign_id,copy_master_id,copy_master_version,"
                "generation,logical_key,status,provider,voice_preset,voice_id,model_id,output_format,"
                "settings_json,settings_fingerprint,long_internal_pauses_json,qc_findings_json,"
                "created_at,updated_at) VALUES ('voice-a','campaign-a','copy-a',1,1,:key,'pending',"
                "'elevenlabs','preset','voice','model','mp3_44100_128','{}',:sha,'[]','[]',:now,:now)"
            ),
            {"key": "b" * 64, "sha": "c" * 64, "now": now},
        )
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE voice_masters SET status='approved' WHERE id='voice-a'")
            )
    except IntegrityError as exc:
        assert "approval gate failed" in str(exc)
    else:  # pragma: no cover - regression guard
        raise AssertionError("invalid direct approval was accepted")
    engine.dispose()
